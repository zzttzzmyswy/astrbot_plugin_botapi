# BotAPI AstrBot 插件 — 设计规格（spec v3）

> 实现方案书《BotAPI 自定义平台适配器 — 开发方案书 v1.2》中 §4（适配器/序列化器/模型）与 §5（内嵌管理页面 + 管理 API）的可落地设计。
> 已对照本地 `AstrBot` 4.25.5 源码**三轮**核实（7 路核对 + 7 路对抗式验证 + 4 路重写重验），修正了原方案书与历版 spec 的全部错误假设。

- **作者**：ZZT ｜ **日期**：2026-06-24 ｜ **目标版本**：AstrBot 4.25.5（源码 `/home/zzt/workspace/AstrBot`，核对 API 唯一权威；WebSearch 本环境返回伪造内容不可信）
- **范围**：AstrBot 插件部分（后端 Python + 管理页前端）。不含手机 App（§6）与部署（§8，仅配置说明）。

---

## 1. 背景与核心约束（源码核实事实）

1. **`Platform` 实例没有 `self.context`**。`conversation_manager`/`message_history_manager`/`register_web_api` 都挂在 `Context` 上，而 `Context` **只注入给 `Star` 插件**（`star/base.py:23-26`）。`Platform.__init__(self, config, event_queue)`（`platform/platform.py:38`）不收 context，`PlatformManager`（`manager.py:210`）也从不注入。`Platform` 与 `Star` 是两个独立基类、由两个独立 manager 在不同时机用不同 `__init__` 实例化——**一个类无法同时是两者**。
2. **平台适配器必须用 `@register_platform_adapter(...)` 装饰器注册**（`platform/register.py:11-63`）。装饰器真实形参**只有** `adapter_name, desc, default_config_tmpl, adapter_display_name, logo_path, support_streaming_message, i18n_resources, config_metadata`——**没有 `support_proactive_message`**（该字段只在 `meta()` 返回的 `PlatformMetadata` 上，`platform_metadata.py:22`，默认 True）。装饰器注册时自动把 `type/enable/id` 写入 `default_config_tmpl`（`register.py:34-41`，`enable` 默认 **False**，首启后须在 WebUI 启用实例）。
3. **适配器 `__init__` 必须 3 参** `(platform_config, platform_settings, event_queue)`，`super().__init__(platform_config, event_queue)`。`PlatformManager` 用 3 个位置参实例化（`manager.py:210`）。
4. **没有 `_on_reply` 钩子**。回复回流靠 `RespondStage` 调 `event.send(chain)`（非流式，`respond/stage.py:249,276,285`）与 `event.send_streaming(generator, ...)`（流式，`respond/stage.py:200`）。基类 `send/send_streaming` 是空操作（`astr_message_event.py:474-491,278-290`）。**必须子类化 `AstrMessageEvent` 重写这两个方法**。`commit_event` 是 fire-and-forget（`platform.py:146-148`，`event_bus.py:39-54`）。
5. **流式需 `enable_streaming` extra**。`provider_settings.streaming_response` 全局默认 **False**（`config/default.py:137`）；pipeline 仅当 `streaming_response` 或 `event.get_extra("enable_streaming")` 为真时才走 `STREAMING_RESULT`+`set_async_stream`（`internal.py:170-172,336-349`）。`AstrMessageEvent.set_extra(self, key, value)` 真实存在（`astr_message_event.py:221-223`），webchat 在入站时 `set_extra("enable_streaming", True)`（`webchat_adapter.py:249-251`）。**BotAPI 必须照做**。
6. **`unified_msg_origin` 格式** `{platform_id}:{MessageType.value}:{session_id}`，`FRIEND_MESSAGE.value == "FriendMessage"`（**驼峰**，`message_type.py:4-7`，`message_session.py:18`）。umo 首段 = `platform_meta.id` = `self.config.get('id','botapi')`（`astr_message_event.py:68-69`，`register.py:40-41`）。`session_id` 只传 **token**。
7. **`tool_call`/`tool_result` 结构化事件对非 webchat 平台不可达**。agent 工具循环里，结构化原链只对 `get_platform_id()=="webchat"` 发（`astr_agent_run_util.py:199-200,227-228`）；其它平台仅在 `show_tool_use`（默认 True）/`show_tool_call_result`（默认 False，`internal.py:73-74`）为真时发一条**纯文本状态** `MessageChain(type="tool_call").message("🔨 调用工具: {name}")`（`astr_agent_run_util.py:201-207,229-237,61-65`）。**`tool_direct_result` 例外**：是"工具直接发给用户的内容"（可含 Image/File），对**所有平台**经 `event.send(MessageChain(chain=res.chain, type="tool_direct_result"))` 发送（`astr_agent_tool_exec.py:670,676-681`，无 webchat 门控）。结构化 `arguments` 在 `AgentResponse` 里、`send` 拿不到。
8. **流式 generator 的 `chain.type` 全集** = `{None/纯文本, "reasoning", "break", "audio_chunk"}`（`astr_agent_run_util.py:218,271,454`，audio_chunk 仅 Live 模式经 `internal.py:309-310`）。另有 `"aborted"`（`tool_loop_agent_runner.py:1395`）。**generator 不 yield `tool_call`**——工具状态是 generator 体内 `await event.send(...)` 直接发的，与 `async for` 交错但不经 yield。generator 契约 `AsyncGenerator[MessageChain | None, None]`（`astr_agent_run_util.py:123`，下游 `if chain is None: continue`，`:516-517`）。
9. **`Conversation` 无逐条稳定 ID**。9 字段：`platform_id/user_id/cid/history/title/persona_id/created_at/updated_at/token_usage`（`db/po.py:531-552`）。`history` 是整段 OpenAI JSON 字符串（`conversation_mgr.py:70`），会被压缩/截断（`internal.py:94-96`）。稳定逐条 id+时间戳在 `platform_message_history` 表（`db/po.py:226-247`，自增 int `id` + `created_at/updated_at` 为 **datetime**），但**只有 webchat 手动 `insert`**（`webchat_adapter.py:148`，走全局 `db_helper`）。BotAPI 须自己写入（走 `Context.message_history_manager`）。
10. **`Conversation.history` 的 reasoning 不在 `reasoning_content`**。`Message` 模型只有 `role/content/tool_calls/tool_call_id`（`agent/message.py:195-246`）。reasoning 以 `ThinkPart` 塞进 assistant 的 **content 列表**（`{type:'think',think:...}`，`tool_loop_agent_runner.py:885-891`）。`tool` 角色无 `name`（`message.py:255-258`），工具名在上一条 assistant 的 `tool_calls[].function.name`（按 `tool_call_id` 回溯）。
11. **管理 API 总路由 `/api/plug/<path:subpath>` 只接受 GET/POST**（`dashboard/server.py:311-314`）。DELETE/PATCH 即 405。
12. **Bridge `window.AstrBotPluginPage`** 只暴露 `ready/getContext/getLocale/getI18n/t/onContext/apiGet/apiPost/upload/download/subscribeSSE/unsubscribeSSE`（`plugin_page_bridge.js:201-268`），**无 `apiDelete/apiPatch`**。bridge 把请求 postMessage 给父窗口、父窗口 axios 同源发起携仪表盘 JWT Cookie；handler 在已认证 `g.username`（`server.py:434-437`）。bridge 在 `status!=='error'` 时解包 `.data`（`PluginPagePage.vue:215-231`）。
13. **`register_to_file_service()`** async 返回 URL（`components.py:531,245,311,858`），但：①要求 `callback_api_base` 非空（默认空→抛异常，`config/default.py:302`）；②URL 走**仪表盘**进程 `/api/file/<token>`（默认端口 **6185**，`dashboard/routes/file.py:15`，`config/default.py:255`）；③token **单次有效 + 默认 300s 过期**（`file_token_service.py:40,67,95,12`）；④`/api/file` 免认证（`server.py:404-413`）。**每次调用铸一个新 token**——多 SSE 客户端须各自铸。
14. **`File` 构造参数是 `file=`**，不是 `file_=`（`components.py:742`）。`File.file` 是 property（异步返回空，`components.py:746-778`）；取路径 `await comp.get_file(allow_return_url=True)`（仅 File，`components.py:793`）。Image/Record 无 `get_file`，等价 `await comp.convert_to_file_path()`（`components.py:478,193`，返回**本地路径**非 URL）。
15. **import 路径**：`MessageChain`←`astrbot.api.event`；`PlatformStatus`←`astrbot.core.platform.platform`；`from astrbot.api.platform` 导出 **8 名**（含 `Group`，`api/platform/__init__.py:1-22`）；`astrbot_config`←`astrbot.core`（`core/__init__.py:33`）；`Response`←`astrbot.dashboard.routes.route`（`route.py:42-59`）；`secure_filename`←`werkzeug.utils`；`after_message_sent`←`astrbot.api.event.filter`（`api/event/filter/__init__.py:11`）。
16. **`quart` 已是自带依赖**（`pyproject.toml:44`），无需 `requirements.txt`。**`@register`（`register_star`）已废弃且多余**——Star 子类由 `__init_subclass__` 自动注册（`base.py:38-49`），元数据来自 `metadata.yaml`（`star_manager.py:1005-1019`）。**不要用 `@register`**。
17. **Star 类必须定义在入口模块 `main.py`**。`__init_subclass__` 按 `cls.__module__` 注册（`base.py:40,43,48`）；Star 仅当 `cls.__module__ == data.plugins.<name>.main`（即 `path in star_map`，`star_manager.py:999,944-945`）才经 `__init_subclass__` 路径实例化。定义在 `star.py` 等非入口模块 → `path in star_map` False → 落 `_get_classes` fallback（`star_manager.py:1117,262-272`，只挑 `name.lower().endswith('plugin') or =='main'`）→ 'BotApiStar' 不匹配 → raise "未通过 Star 注册" → **插件加载失败**。
18. **事件循环**：`Platform.run()`、仪表盘 handler、event_bus 同在 `core_lifecycle` 的事件循环（`core_lifecycle.py:283`，`manager.py:50`）。`RuntimeState` 共享可变状态单线程访问，**无需加锁**。
19. **SSE 队列须非阻塞写入**。AstrBot 自身 `LogBroker` 用 `Queue(maxsize=...)` + `put_nowait` + `except asyncio.QueueFull: pass`（`log.py:134,144-147`）。`asyncio.Queue.put` 在有界队列满时是**挂起**而非抛异常——若用 `await q.put`，慢客户端会阻塞 `send/send_streaming` → 阻塞 `RespondStage` 整条回复（`respond/stage.py:200,249`）。

---

## 2. 架构：Platform + Star 双组件 + RuntimeState 单例

### 2.1 组件划分

| 组件 | 基类 | 职责 | 配置 |
|:--|:--|:--|:--|
| `BotApiAdapter` | `Platform` | 手机 API（Quart 9000）；SSE 池；上传；`BotApiMessageEvent` 回复回流；`send_by_session`；文本镜像写入 | 平台实例配置（`default_config_tmpl`） |
| `BotApiStar` | `Star` | 持 `self.context`；注册管理 API（仪表盘 `/api/plug/`）；注入 managers 到 `RuntimeState` | metadata.yaml |

### 2.2 RuntimeState 单例（解决加载顺序）

加载顺序：`PluginManager.reload()` 先跑（实例化 Star + 执行顶层 `@register_platform_adapter` 注册 Platform 类），`platform_manager.initialize()` 后跑（3 参实例化 Platform，`core_lifecycle.py:243,272`）。**Star `__init__` 时 Platform 实例尚未创建**。

```python
# runtime.py
class RuntimeState:
    adapter: "BotApiAdapter | None" = None
    conversation_manager = None
    message_history_manager = None
    context = None
_runtime = RuntimeState()
def runtime() -> RuntimeState: return _runtime
```

- `BotApiStar.__init__`（在 main.py，见 §9.1）：注入 `context`/`conversation_manager`/`message_history_manager`。
- `BotApiAdapter.__init__`：`runtime().adapter = self`。
- adapter 的 `/history`/`/stream` 补推/stats 惰性读 `runtime().conversation_manager`；Star 管理 handler 惰性读 `runtime().adapter`。
- **单实例约定**：`RuntimeState.adapter` 持单个 adapter。所有 `platform_id` 从 `self.meta().id` 派生（默认 "botapi"），与 umo 首段一致。多用户靠 `tokens` 列表，非多实例。多实例不在本期（见 §12 开放点 5）。

### 2.3 备选架构（已否决）

- **A. 单 Platform 类（方案书原意）**：不可行（事实 1）。
- **B. Star 主控 + Platform 薄桥**：回复回流绑 Platform 事件子类，Star 反向注入 SSE 池控制流绕。否决。

---

## 3. 文件 / 模块结构

```
astrbot_plugin_botapi/
├── metadata.yaml
├── main.py                    # 入口：@register_platform_adapter 注册 BotApiAdapter；定义 BotApiStar（Star 必须在入口模块，事实 17）
├── adapter.py                 # BotApiAdapter(Platform)：HTTP、SSE 池、上传、生命周期、send_by_session、_broadcast_to、_push_media
├── event.py                   # BotApiMessageEvent(AstrMessageEvent)：send/send_streaming
├── serializer.py              # MessageSerializer：链/流式片段 → payload；媒体铸 URL
├── history.py                 # platform_message_history 文本镜像写入/读取（稳定 ID + since）
├── routes.py                  # 手机 API 路由（auth/message/upload/stream/history）
├── runtime.py                 # RuntimeState 单例
├── models.py                  # BotApiConfig / SSEEvent / 数据模型
└── pages/dashboard/{index.html,app.js,style.css}
```

> 入口 `main.py`（`star_manager.py:277-294,944-945`），模块路径 `data.plugins.astrbot_plugin_botapi.main`。无需 `__init__.py`。无需 `requirements.txt`。**`BotApiStar` 必须定义在 main.py**（事实 17），可 `from .adapter import BotApiAdapter` + `from .event import BotApiMessageEvent` 等。

---

## 4. 平台适配器 `BotApiAdapter`

### 4.1 注册（装饰器在 `adapter.py` 类上，无 `support_proactive_message`）

装饰器直接装饰 `adapter.py` 里的 `BotApiAdapter` 类（`@register_platform_adapter` 在类定义时把类写入 `platform_cls_map`，`register.py:11-63`）。`main.py` 只需 `from .adapter import BotApiAdapter`（触发模块加载→装饰器执行→注册）；`BotApiStar` 定义在 `main.py` 本身（§9.1，事实 17）。装饰器参数：

```python
# adapter.py 顶部
@register_platform_adapter(
    "botapi",
    "BotAPI 自定义移动端适配器 — 一人一 Bot 极简移动端接入，支持弱网断连恢复",
    default_config_tmpl={"host": "0.0.0.0", "port": 9000, "tokens": []},
    config_metadata={
        # schema：{description, type, hint?, options?, labels?, condition?}——无 default 键；type:"list" 须带 items
        "host":   {"description": "监听地址", "type": "string", "hint": "0.0.0.0"},
        "port":   {"description": "监听端口", "type": "int", "hint": "9000"},
        "tokens": {"description": "允许的 Token 列表（空则允许所有非空 token）",
                   "type": "list", "items": {"type": "string"}},
    },
    adapter_display_name="BotAPI 移动端",
    support_streaming_message=True,   # 无 support_proactive_message（该字段只在 meta()，§4.2）
)
class BotApiAdapter(Platform):
    ...   # 类体见 §4.2
```

> `config_metadata` 字段无 `default`（默认值来自 `default_config_tmpl`，`config/default.py:546-614`，`config.py:1562-1565`）。`type:"list"` 必带 `"items":{"type":"string"}`（`config/default.py:1081-1084`）。`support_proactive_message` 只在 `meta()`（§4.2）。用户在 WebUI「机器人/平台→新增→选 type=botapi→填配置→启用」创建实例（`enable` 默认 False 须手动启用，事实 2）。

### 4.2 适配器类

```python
# adapter.py
import asyncio, time, uuid
from collections import defaultdict
from pathlib import Path

from astrbot.api.platform import (register_platform_adapter, Platform, PlatformMetadata,
    AstrBotMessage, MessageMember, MessageType, AstrMessageEvent)
from astrbot.api.message_components import Plain, Image, File, Record
from astrbot.api.event import MessageChain
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core import astrbot_config
from quart import Quart, jsonify, request, make_response
from werkzeug.utils import secure_filename

from .models import BotApiConfig, SSEEvent
from .serializer import MessageSerializer
from .runtime import runtime

@register_platform_adapter("botapi", "...", default_config_tmpl={...}, config_metadata={...},
                          adapter_display_name="...", support_streaming_message=True)  # 无 support_proactive_message
class BotApiAdapter(Platform):
    def __init__(self, platform_config: dict, platform_settings: dict, event_queue: asyncio.Queue) -> None:
        super().__init__(platform_config, event_queue)
        self.settings = platform_settings
        self.cfg = BotApiConfig(**platform_config)        # 运行时副本（枚举账户用）；落盘见 §9.3
        self.platform_id = self.meta().id
        self.app = Quart("astrbot_plugin_botapi")

        self._token_to_origin: dict[str, str] = {}
        self._sse_clients: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._disabled_tokens: set[str] = set()
        self._last_active: dict[str, float] = {}
        self._uploaded_files: dict[str, dict] = {}

        self._upload_dir = Path(astrbot_config.get("data_path", "./data")) / "botapi_uploads"
        self._upload_dir.mkdir(parents=True, exist_ok=True)

        self._shutdown = asyncio.Event()                  # __init__ 内（terminate 可能先于 run task）
        self._media_enabled = bool(astrbot_config.get("callback_api_base"))

        self._serializer = MessageSerializer()
        runtime().adapter = self
        self._setup_routes()

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="botapi", description="BotAPI 自定义移动端适配器",
            id=self.config.get("id", "botapi"),
            adapter_display_name="BotAPI 移动端",
            support_streaming_message=True,
            support_proactive_message=True,   # 此处才设
        )

    # ── 非阻塞 SSE 广播（事实 19）──
    def _put(self, q: asyncio.Queue, evt: SSEEvent):
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            try:   # 丢最旧腾位再放
                q.get_nowait(); q.put_nowait(evt)
            except Exception:
                pass   # 仍满则丢弃，不阻塞 pipeline

    async def _broadcast_to(self, token: str, evt: SSEEvent):
        """向该 token 的所有 SSE 队列非阻塞投递（文本/thinking/error 等跨客户端一致的事件）。"""
        for q in list(self._sse_clients.get(token, [])):
            self._put(q, evt)

    async def _push_media(self, chain: MessageChain | None, token: str, message_id: str):
        """链内媒体：每队列铸独立 token 后非阻塞投递（事实 13 单次有效）。"""
        if chain is None: return
        queues = list(self._sse_clients.get(token, []))
        for comp in (chain.chain or []):
            ct = comp.type.value.lower() if hasattr(comp.type, "value") else str(comp.type).lower()
            if ct not in ("image", "record", "file"): continue
            mtype = {"image": "image", "record": "audio", "file": "file"}[ct]
            for q in queues:   # 每队列铸一个新 token
                url = await self._serializer._media_url(comp)
                if not url: continue
                data = {"message_id": message_id, "type": mtype,
                        "content": {"name": getattr(comp, "name", "file"), "url": url} if mtype == "file" else url,
                        "streaming": False, "final": False, "timestamp": int(time.time())}
                self._put(q, SSEEvent("message", data))
```

> `self.config` 由基类设（`platform.py:41`）。`self.client_self_id` = `uuid.uuid4().hex`（`platform.py:44`）。`self.status` 是 property（`platform.py:51-61`），框架在 `_task_wrapper` 设 RUNNING（`manager.py:237`）。

### 4.3 生命周期

```python
    def run(self):   # 普通 def 返回协程（manager.py:50 用 create_task）
        return self.app.run_task(host=self.cfg.host, port=self.cfg.port,
                                 shutdown_trigger=self._shutdown.wait)

    async def terminate(self) -> None:   # 基类默认空实现（platform.py:125-126，非 abstract），建议重写
        self._shutdown.set()
        for token, queues in list(self._sse_clients.items()):
            for q in queues:
                self._put(q, None)   # 哨兵（非阻塞）
```

---

## 5. 事件子类 `BotApiMessageEvent`（SSE 回流核心）

```python
# event.py
import time
from astrbot.api.platform import AstrMessageEvent
from astrbot.api.event import MessageChain
from .models import SSEEvent
from .history import persist_assistant_text, persist_assistant_thinking, persist_inbound_text

# 仅"🔨 调用工具"状态文本（事实 7）。tool_direct_result 是工具直答（可带媒体），走普通回复分支。
TOOL_STATUS_TYPE = "tool_call"

class BotApiMessageEvent(AstrMessageEvent):
    def __init__(self, message_str, message_obj, platform_meta, session_id, adapter):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.adapter = adapter
        self.token = message_obj.sender.user_id
        self._text_buf: list[str] = []

    async def _broadcast(self, evt: SSEEvent):
        await self.adapter._broadcast_to(self.token, evt)   # 非阻塞

    async def send(self, message: MessageChain) -> None:
        if message is None:                 # 守卫在前（事实 8：None 可达）
            return
        await super().send(message)         # metrics
        mtype = getattr(message, "type", None) or ""
        mid = self.message_obj.message_id

        if mtype == TOOL_STATUS_TYPE:       # 工具状态文本，不并入答案
            txt = message.get_plain_text() if hasattr(message, "get_plain_text") else ""
            if txt:
                await self._broadcast(SSEEvent("message", {
                    "message_id": mid, "type": "text", "subtype": "tool_status", "content": txt,
                    "streaming": False, "final": False, "timestamp": int(time.time())}))
                await persist_assistant_text(self.token, mid, txt, kind="tool_status")
            return

        # 普通回复（含 tool_direct_result 工具直答，可带媒体）
        payload = await self.adapter._serializer.serialize_chain(message, self)
        await self._broadcast(SSEEvent("message", {**payload, "streaming": False, "final": True}))
        await self.adapter._push_media(message, self.token, mid)   # 推送媒体（每队列独立 token）
        await persist_assistant_text(self.token, mid, payload.get("content", ""), kind="final")

    async def send_streaming(self, generator, use_fallback=False) -> None:
        await super().send_streaming(generator, use_fallback)   # metrics（基类不消费 generator）
        mid = self.message_obj.message_id
        full_text: list[str] = []
        thinking: list[str] = []
        async for chain in generator:
            if chain is None:               # 事实 8：generator 可 yield None
                continue
            ctype = getattr(chain, "type", None) or ""
            if ctype == "break":            # 分段信号（astr_agent_run_util.py:218）
                if self._text_buf:
                    seg = "".join(self._text_buf)
                    await self._broadcast(SSEEvent("message", {
                        "message_id": mid, "type": "text", "content": seg,
                        "streaming": True, "segment_end": True, "timestamp": int(time.time())}))
                    full_text.extend(self._text_buf); self._text_buf.clear()
                continue
            if ctype == "reasoning":        # 精确匹配（webchat_event.py:204）
                t = chain.get_plain_text() if hasattr(chain, "get_plain_text") else ""
                if t:
                    thinking.append(t)
                    await self._broadcast(SSEEvent("thinking", {
                        "message_id": mid, "content": t, "streaming": True, "timestamp": int(time.time())}))
                continue
            if ctype == "audio_chunk":      # Live 模式；本期不处理
                continue
            if ctype == "aborted":          # 事实 8
                continue
            # plain 文本增量（ctype 为 "" 或 None）
            t = chain.get_plain_text() if hasattr(chain, "get_plain_text") else ""
            if t:
                self._text_buf.append(t)
                await self._broadcast(SSEEvent("message", {
                    "message_id": mid, "type": "text", "content": t,
                    "streaming": True, "timestamp": int(time.time())}))
            await self.adapter._push_media(chain, self.token, mid)   # 流式中的媒体即时推
        # generator 耗尽 = 流式完成（after_message_sent 对流式不触发，respond/stage.py:201）
        if self._text_buf:
            full_text.extend(self._text_buf); self._text_buf.clear()
        final_text = "".join(full_text)
        await self._broadcast(SSEEvent("message", {
            "message_id": mid, "type": "text", "content": final_text,
            "streaming": False, "final": True, "timestamp": int(time.time())}))
        if thinking:
            await persist_assistant_thinking(self.token, mid, "".join(thinking))
        if final_text:
            await persist_assistant_text(self.token, mid, final_text, kind="final")
```

> **关键**：序列化读 `send` 传入的 `message.chain`。`tool_direct_result`（工具直答，可带媒体）走普通回复分支（serialize_chain + _push_media + persist content），**不**误标 tool_status。流式 generator 不 yield `tool_call`（事实 8），故 send_streaming 无 tool 分支。媒体 `_push_media` 每队列铸独立 token（事实 13）。`_broadcast` 非阻塞（事实 19）。persist 用 `payload.get("content","")`（serialize_chain 的键是 `content`）。

### 5.1 主动消息 `send_by_session`

```python
# adapter.py
async def send_by_session(self, session, message_chain: MessageChain) -> None:
    await super().send_by_session(session, message_chain)
    token = session.session_id   # MessageSession.from_str(umo) 第三段即 token
    mid = f"botapi_proactive_{uuid.uuid4().hex[:12]}"
    payload = await self._serializer.serialize_chain(message_chain, None)
    await self._broadcast_to(token, SSEEvent("message", {**payload, "streaming": False, "final": True}))
    await self._push_media(message_chain, token, mid)   # 主动消息媒体也推
```

### 5.2 构造并提交入站消息（含 `enable_streaming` extra）

```python
# adapter.py，POST /message handler 内
msg = AstrBotMessage()
msg.type = MessageType.FRIEND_MESSAGE
msg.self_id = self.client_self_id
msg.session_id = token                 # 只传 token
msg.message_id = f"botapi_{uuid.uuid4().hex[:12]}"
msg.sender = MessageMember(user_id=token, nickname="User")
msg.timestamp = int(time.time())
msg.message = components
msg.message_str = text or "[消息]"
msg.raw_message = await request.get_json()

event = BotApiMessageEvent(message_str=msg.message_str, message_obj=msg,
                           platform_meta=self.meta(), session_id=token, adapter=self)
event.set_extra("enable_streaming", True)   # 事实 5：强制流式（set_extra 已确认，astr_message_event.py:221）
await persist_inbound_text(token, msg.message_id, text)
self.commit_event(event)               # fire-and-forget
return jsonify({"message_id": msg.message_id})
```

---

## 6. 手机 API 契约

Base URL：`https://<host>:<port>/api/v1/botapi`（适配器 9000，nginx 反代）。认证：`Authorization: Bearer <token>`。

### 6.1 端点

| 端点 | 方法 | 入参 | 出参 |
|:--|:--|:--|:--|
| `/auth` | POST | `{token}` | `{user_id, session_id}` 或 401 |
| `/message` | POST | `{text?, file_ids?, reply_to?}` | `{message_id}`（**仅此**） |
| `/upload` | POST multipart | `file` | `{file_id, name, mime_type, size}` |
| `/stream` | GET | `?since=<id>?` | SSE 流（6.3） |
| `/history` | GET | `?since=&before=&limit=` | `{messages:[...], has_more}` |

### 6.2 认证中间件

```python
@self.app.before_request
async def check_auth():
    if request.endpoint == "auth": return
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not self._is_valid_token(token) or token in self._disabled_tokens:
        return jsonify({"error": "unauthorized", "code": "INVALID_TOKEN"}), 401
    self._last_active[token] = time.time()
```

### 6.3 SSE 事件契约（`/stream`）—— quart 完整写法（参照 `dashboard/routes/log.py`）

```python
@self.app.get("/api/v1/botapi/stream")
async def stream():
    token = self._extract_token()
    q: asyncio.Queue = asyncio.Queue(maxsize=256)   # 有界（事实 19）
    self._sse_clients[token].append(q)
    since = request.args.get("since")

    async def gen():
        try:
            if since:   # 先补推漏掉的文本（媒体不补推）
                for evt in await history.catchup_events(self.platform_id, token, since):
                    yield evt.to_sse()
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield SSEEvent.ping().to_sse(); continue
                if item is None:        # 哨兵
                    break
                yield item.to_sse()
        except asyncio.CancelledError:
            pass
        finally:
            if q in self._sse_clients.get(token, []):
                self._sse_clients[token].remove(q)   # 必须注销（log.py:84-86 范本）

    resp = await make_response(gen(), {
        "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
        "Connection": "keep-alive", "Transfer-Encoding": "chunked",
        "X-Accel-Buffering": "no",
    })
    resp.timeout = None   # 防 hypercorn 默认超时（log.py:100）
    return resp
```

**SSE 事件类型**：

| event | data | 说明 |
|:--|:--|:--|
| `message` | `{message_id, type, content, subtype?, streaming?, final?, segment_end?, timestamp}` | 文本/图片/音频/文件。`subtype:"tool_status"`=工具活动文本（不并入答案）；`streaming:true` 增量；`final:true` 完成携完整文本；`segment_end:true` 流式分段边界 |
| `thinking` | `{message_id, content, streaming?, timestamp}` | reasoning |
| `error` | `{code, message}` | 错误 |
| `ping` | `{}` | 30s 保活 |

> **降级（事实 7）**：结构化 `tool_call`/`tool_result` SSE 事件不可达。工具活动以 `message`+`subtype:"tool_status"` 文本推送（"🔨 调用工具: {name}"，受 `show_tool_use` 控制）。`tool_direct_result`（工具直答，可带媒体）作为普通 `message`（final）+ 媒体推送。结构化工具事件实时推送需 agent 钩子（§12 开放点 2）。

**聚合约定**：一次入站事件 `message_id` 全程不变，但 `send`/`send_streaming` 多次调用（工具状态、分段、final）。`subtype:"tool_status"` → 独立系统提示气泡，不并入答案；`streaming:true` → 按 `message_id` 追加增量；`final:true` → 用 `content` 完整文本自纠正；`segment_end:true` → 切段。

### 6.4 `/history`（文本镜像为主）

```python
# history.py
async def get_history(platform_id, token, since=None, before=None, limit=50):
    rt = runtime()
    rows = await rt.message_history_manager.get(
        platform_id=platform_id, user_id=token, page=1, page_size=200)   # 已 reverse 升序
    msgs = [row_to_sse(r) for r in rows]
    if since:  msgs = [m for m in msgs if int(m["message_id"]) > int(since)]    # int 比较（row.id 是 int）
    if before: msgs = [m for m in msgs if int(m["message_id"]) < int(before)]
    return msgs[-limit:], len(msgs) == limit

def row_to_sse(row):
    c = row.content or {}
    return {
        "message_id": str(row.id),                      # SSE/JSON 用 str
        "role": c.get("role", "assistant"),
        "type": {"final": "text", "thinking": "thinking", "tool_status": "tool_status"}.get(c.get("kind"), "text"),
        "content": c.get("text", ""),
        "timestamp": int(row.created_at.timestamp()),   # datetime → int 秒
    }
```

> `message_history_manager.get(platform_id, user_id, page=1, page_size=200)` 已 reverse 升序（`platform_message_history_mgr.py:28-43`）。`row.id`(int)/`.content`(dict)/`.created_at`(datetime) 可读（`po.py:226-247`）。`since`/`before` 应用层按 **int** 过滤（DB 层只支持分页，`sqlite.py:598-618`）。分页陷阱：`get(page=1,page_size=200)` 返回最近 200 条，`since` 早于窗口最旧一条时仅返回窗口内、`has_more` 启发式失真。Conversation.history 富渲染（结构化 tool_calls/ThinkPart）为可选增强（§7.3），本期不依赖。

### 6.5 `/upload`

```python
file = (await request.files).get("file")
if not file:
    return jsonify({"error": "no_file"}), 400
filename = secure_filename(file.filename or "untitled")
file_id = f"f_{uuid.uuid4().hex[:10]}"
save_path = self._upload_dir / f"{file_id}_{filename}"
await file.save(save_path)
info = {"file_id": file_id, "name": filename,
        "mime_type": file.content_type or "application/octet-stream",
        "size": save_path.stat().st_size}   # 不含 path（避免泄露）
self._uploaded_files[file_id] = {**info, "path": str(save_path)}   # path 仅服务端
return jsonify(info)
```

> `file_id` 进程生命周期有效——热重载/重启后 `_uploaded_files` 清零（`manager.py:256-265`）。约定 App 上传后立即 `/message` 引用；可选 TTL 清理（§12 开放点 3）。

```python
def _file_info_to_component(self, info):
    mime = info.get("mime_type", ""); path = info["path"]
    if mime.startswith("image/"):  return Image.fromFileSystem(path)
    if mime.startswith("audio/") or "ogg" in mime: return Record.fromFileSystem(path)
    return File(name=info["name"], file=path)   # file= 不是 file_=
```

---

## 7. 消息序列化器 `MessageSerializer`

### 7.1 出站链 → SSE payload（文本）；媒体由 `_push_media` 单独推

```python
class MessageSerializer:
    async def serialize_chain(self, message: MessageChain, event) -> dict:
        text_parts = []
        for comp in (message.chain or []):
            ct = comp.type.value.lower() if hasattr(comp.type, "value") else str(comp.type).lower()
            if ct == "plain":               # ComponentType 仅 'Plain'（'text' 不会命中，防御性）
                text_parts.append(getattr(comp, "text", ""))
            # image/record/file 由 adapter._push_media 推送，不混入文本 payload
        return {
            "message_id": event.message_obj.message_id if event else None,
            "role": "assistant", "type": "text",
            "content": "".join(text_parts),   # 键是 content（send 的 persist 用 payload.get("content")）
            "timestamp": int(time.time()),
        }
```

> 媒体由 `adapter._push_media(chain, token, mid)` 推送（§4.2），每队列铸独立 token。一条同时含文本+媒体的回复：文本走 `message(final:true)`、媒体走 `message(final:false)`，App 按 `type`/`final` 分别渲染。

### 7.2 媒体 URL（推送时铸 token，不入库）

```python
    async def _media_url(self, comp) -> str | None:
        # 注意：此方法被 adapter._push_media 在每队列循环内调用，每调铸一个新 token（事实 13）
        if not self._media_enabled:   # callback_api_base 缺失
            return None
        try:
            return await comp.register_to_file_service()   # async，返回 /api/file/<token>
        except Exception:
            try:
                if hasattr(comp, "get_file"):              # 仅 File
                    return await comp.get_file(allow_return_url=True)
                if hasattr(comp, "convert_to_file_path"):  # Image/Record
                    p = await comp.convert_to_file_path()
                    return p or None   # 注意：返回本地路径非 URL，App 无法直接加载（见下）
            except Exception:
                pass
            return None   # fallback 失败则跳过该媒体（App 显示占位）
```

> `convert_to_file_path`/`get_file(allow_return_url=True)` fallback 返回**本地路径**非 URL（`components.py:478,193,793`），App 无法加载。此为窄边缘降级场景（`callback_api_base` 已配但 `register_to_file_service` 抛错）；实现期可考虑跳过（返回 None）而非投递裸路径。`_media_enabled` 是 adapter 属性，serializer 须持有 adapter 引用或由 adapter 传入——实现期调整（spec 示意）。

### 7.3 Conversation.history 富渲染（可选增强，本期不启用）

若未来在 `/history` 提供结构化 `tool_calls`/thinking，从 `Conversation.history` 渲染时**必须**：reasoning 解析 assistant `content` 列表的 `part["type"]=="think"`→`part["think"]`（**不读 `reasoning_content`**）；`tool_calls` 字段结构化可用（`arguments` 需二次 `json.loads`）；`tool` 角色工具名按 `tool_call_id` 回溯上一条 assistant 的 `tool_calls[].function.name`；media 省略。本期 `/history` 以镜像为主（§6.4）。

---

## 8. 历史与断连补消息：文本镜像 + 媒体不持久化

**决策（用户确认）**：服务端只保留文本（含 thinking、工具状态文本）。媒体只推送、App 缓存，不入库不回放。

### 8.1 文本镜像写入 `platform_message_history`

```python
# history.py
from .runtime import runtime

async def _insert(content: dict, user_id, sender_id, sender_name):
    rt = runtime()
    if not (rt.message_history_manager and rt.adapter): return
    await rt.message_history_manager.insert(
        platform_id=rt.adapter.platform_id, user_id=user_id, content=content,
        sender_id=sender_id, sender_name=sender_name)   # 返回带自增 id 的记录

async def persist_inbound_text(token, message_id, text):
    if not text: return
    await _insert({"role": "user", "kind": "user", "text": text, "message_id": message_id}, token, token, "User")

async def persist_assistant_text(token, message_id, text, kind: str):  # kind: final/tool_status
    if not text: return
    await _insert({"role": "assistant", "kind": kind, "text": text, "message_id": message_id}, token, "bot", "BotAPI")

async def persist_assistant_thinking(token, message_id, text):
    if not text: return
    await _insert({"role": "assistant", "kind": "thinking", "text": text, "message_id": message_id}, token, "bot", "BotAPI")

async def catchup_events(platform_id, token, since: str):
    rows = await runtime().message_history_manager.get(
        platform_id=platform_id, user_id=token, page=1, page_size=200)
    out = []
    for r in rows:
        if int(r.id) <= int(since): continue    # int 比较（row.id 是 int）
        c = r.content or {}
        etype = "thinking" if c.get("kind") == "thinking" else "message"
        out.append(SSEEvent(etype, row_to_sse(r)))
    return out
```

> `insert(platform_id, user_id, content: dict, sender_id=None, sender_name=None, llm_checkpoint_id=None) -> PlatformMessageHistory`（`platform_message_history_mgr.py:9-17`），`content` 为 `sa_type=JSON` 任意 dict 无 schema 校验（`po.py:246`）。`platform_id` 用 `rt.adapter.platform_id`（= `self.meta().id`），与 umo 首段一致。与 webchat 走全局 `db_helper` 不同，本设计走 `Context.message_history_manager`（已核实签名）。

### 8.2 稳定 ID 与 `since` 语义

- 每条入库文本记录获自增 int `id`，作为 `message_id`（`/history` 与补推均用）。App 跟踪最大 id，重连 `?since=<id>` 补拉 `id > since`（int 比较）。
- 媒体 SSE 事件无稳定 id（瞬态+一次性）；App 推送时下载缓存；离线错过则丢失（用户接受）。

---

## 9. 管理页 API + Plugin Page（`BotApiStar`）

### 9.1 Star（在 main.py，无 `@register`）

```python
# main.py（入口模块——Star 必须定义在此，事实 17）
from astrbot.api.star import Star, Context
from .runtime import runtime
from .adapter import BotApiAdapter   # 触发 @register_platform_adapter 注册
# BotApiAdapter 已在 adapter.py 顶部装饰注册；此处 import 确保模块加载

class BotApiStar(Star):   # 不用 @register（已废弃，元数据来自 metadata.yaml，事实 16）；定义在 main.py
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        rt = runtime()
        rt.context = context
        rt.conversation_manager = context.conversation_manager
        rt.message_history_manager = context.message_history_manager
        P = "astrbot_plugin_botapi"   # 须与 metadata.yaml name 严格一致
        context.register_web_api(f"/{P}/stats",    self._stats,    ["GET"],  "统计")
        context.register_web_api(f"/{P}/accounts", self._accounts, ["GET"],  "账户列表")
        context.register_web_api(f"/{P}/accounts", self._create,   ["POST"], "新增账户")
        context.register_web_api(f"/{P}/accounts/<token_hash>/delete", self._delete, ["POST"], "删除账户")
        context.register_web_api(f"/{P}/accounts/<token_hash>/status", self._toggle, ["POST"], "启停账户")
        context.register_web_api(f"/{P}/sessions/<token_hash>/disconnect", self._disconnect, ["POST"], "断开会话")
        context.register_web_api(f"/{P}/sessions/<token_hash>/clear", self._clear, ["POST"], "清空历史")
    # _stats/_accounts/_create/_delete/_toggle/_disconnect/_clear 定义在 main.py 或同包模块（handler 须是 Star 方法）
```

> `register_web_api(route, view_handler, methods, desc)`（`context.py:515-521`，形参 `view_handler`），handler async。URL=`/api/plug/{route去前导斜杠}`；`<token_hash>` 段作 kwarg（`server.py:331`）。bridge 自动拼 `/api/plug/{pluginName}/{endpoint}`（`PluginPagePage.vue:120-123`），`pluginName==P` 匹配。

### 9.2 handler 范式（envelope + 反查 adapter + 一致 umo）

```python
from astrbot.dashboard.routes.route import Response
import json, hashlib

async def _stats(self):
    rt = runtime(); adapter = rt.adapter
    if not adapter:
        return Response().error("适配器未就绪").__dict__
    pid = adapter.platform_id
    per = []
    for token in adapter.cfg.tokens or []:   # 运行时副本（与落盘同步，见 §9.3）
        umo = f"{pid}:FriendMessage:{token}"
        msg_count = 0
        cid = await rt.conversation_manager.get_curr_conversation_id(umo)
        if cid:
            conv = await rt.conversation_manager.get_conversation(umo, cid)
            if conv and conv.history:
                msg_count = len(json.loads(conv.history))
        per.append({"token_preview": _preview(token), "token_hash": _hash(token),
                    "online": bool(adapter._sse_clients.get(token)),
                    "sse_connections": len(adapter._sse_clients.get(token, [])),
                    "message_count": msg_count, "last_active": adapter._last_active.get(token)})
    return Response().ok({"total_accounts": len(per),
        "total_online": sum(1 for a in per if a["online"]),
        "total_messages": sum(a["message_count"] for a in per), "per_account": per}).__dict__
```

> `Response().ok(data).__dict__`（`route.py:42-59`），Quart 自动 jsonify，bridge 解包 `.data`。umo 用 `{pid}:FriendMessage:{token}`（驼峰 + 实例 id）。

### 9.3 账户持久化（修正三重错）

`_create`/`_delete` 在 Star handler 内，`self` 是 Star（无 `self.cfg`/`self.config`）。正确路径：

```python
async def _create(self):
    data = await request.get_json()
    token = data.get("token") or uuid.uuid4().hex[:16]
    adapter = runtime().adapter
    if adapter:
        # 1. 改全局 astrbot_config 中本平台实例的 tokens（按 id 定位 platform 子树，确保写回根配置）
        from astrbot.core import astrbot_config
        for p in astrbot_config.get("platform", []):
            if p.get("id") == adapter.config.get("id"):
                toks = p.setdefault("tokens", [])
                if token not in toks: toks.append(token)
                break
        # 2. 同步运行时副本（adapter.config 子树 + adapter.cfg 枚举副本）
        adapter.config.setdefault("tokens", [])
        if token not in adapter.config["tokens"]: adapter.config["tokens"].append(token)
        adapter.cfg.tokens = list(adapter.config["tokens"])
        # 3. 全局单例落盘
        astrbot_config.save_config()   # core/__init__.py:33 单例，astrbot_config.py:216
    return Response().ok({"token": token}).__dict__
```

> **关键修正**：`self.config.save_config()` 错（Star 无 self.config；adapter.config 是 plain dict 无 save_config；adapter.cfg 是拷贝不回写）。正确：经 `runtime().adapter`，改**全局 `astrbot_config`** 中本平台子树的 `tokens`（按 `adapter.config['id']` 定位），同步 `adapter.config['tokens']`（子树引用）+ `adapter.cfg.tokens`（枚举副本），调 `astrbot_config.save_config()` 落盘。`adapter.config` 是否为全局子树共享引用需实现期确认（若是则直接 mutate `adapter.config['tokens']` 即改根配置；若为拷贝则须改全局 `astrbot_config['platform']` 子树——§12 开放点 4）。`_disabled_tokens` 仅运行时，重启从 allowlist 重建。

### 9.4 前端 `pages/dashboard/`

目录约定 `pages/dashboard/index.html`（`plugin.py:74-75,364-378,406-436`），无需注册。`app.js`：`bridge.apiGet("stats")` / `bridge.apiPost("accounts/<hash>/delete", {})`（`<hash>` 须替换为真实 token_hash 值）。用 `apiPost`（无 `apiDelete`/`apiPatch`）。认证：iframe sandbox 无 `allow-same-origin`→iframe 为 opaque origin，bridge 代理请求给父窗口、父窗口携 JWT Cookie 同源发起，handler 在 `g.username`（`server.py:434-437`）。亮暗双主题 CSS 沿用方案书 §5.4。

---

## 10. 配置

### 10.1 平台实例配置（`@register_platform_adapter`）

`default_config_tmpl={"host","port","tokens"}` + `config_metadata`（无 `default`、`list` 带 `items`，§4.1）。装饰器自动补 `type/enable/id`（`enable` 默认 False）。用户在 WebUI「机器人/平台→新增→选 type=botapi→填配置→启用」（`config.py:1561-1572,1511-1533`，`config_metadata` 字段需含 `description/hint/labels` 才渲染成控件）。

### 10.2 流式与 `callback_api_base`

- **流式**：`event.set_extra("enable_streaming", True)`（§5.2，已确认 `set_extra` 存在）。
- **`callback_api_base`**：`register_to_file_service` 依赖它（默认空→抛异常）。启动校验 `self._media_enabled=bool(astrbot_config.get("callback_api_base"))`，缺失则告警 + 媒体降级（文本不受影响）。文件 URL 走仪表盘 6185 的 `/api/file/<token>`（非适配器 9000）。

### 10.3 部署：两端口两认证

- App↔适配器：`/api/v1/botapi/*`（Bearer，9000）。
- 媒体 URL：`/api/file/<token>`（免认证，6185）。nginx 分别反代 9000 与 6185 到同域。
- 管理页：仪表盘 6185 + JWT Cookie。

### 10.4 `metadata.yaml`

```yaml
name: astrbot_plugin_botapi
desc: BotAPI 自定义移动端适配器 — 一人一 Bot 极简移动端接入，支持弱网断连恢复。
version: 1.0.0
author: ZZT
repo: https://github.com/ZZT/astrbot_plugin_botapi
astrbot_version: ">=4.25.0"
```

---

## 11. 错误处理与边界

| 场景 | 处理 |
|:--|:--|
| `callback_api_base` 缺失 | 启动告警 + `_media_enabled=False`；媒体返回 None/占位，文本不受影响 |
| token 无效/被禁用 | 401；删除/禁用账户时 `_put(q, None)` 哨兵关闭 |
| SSE 队列满/慢客户端 | `Queue(maxsize=256)` + `put_nowait` + `QueueFull` 丢最旧（`_put`，事实 19）；不阻塞 pipeline |
| SSE 推送异常 | `_broadcast_to`/`_push_media` 内 `_put` 已 try/except；error 事件用 `_put` 直接投（不自我重入） |
| `send`/`send_streaming` 多次调用 | `tool_status` 用 subtype 不入答案；分段用 `segment_end`/`streaming`；`final` 自纠正。仅 `final`/`thinking`/`tool_status` 入库 |
| 流式完成信号 | generator 耗尽自行发 `final`（`after_message_sent` 对流式不触发，`respond/stage.py:201`） |
| generator yield None | `if chain is None: continue`（事实 8） |
| `terminate`/热重载 | `shutdown_trigger` 关 Quart；`_put(q, None)` 哨兵（非阻塞） |
| 上传 `file_id` 重启丢失 | 约定 App 上传后立即 `/message` 引用；可选 TTL 清理 |
| 媒体单次 token 多客户端 | `_push_media` 每队列铸独立 token |
| App 离线错过媒体 | 接受丢失；重连只补文本 |

---

## 12. 待实现期核实的开放点

1. ~~`set_extra("enable_streaming", True)` 精确 API~~ —— **已确认**：`AstrMessageEvent.set_extra(self, key, value)`（`astr_message_event.py:221-223`），webchat 在用（`webchat_adapter.py:249-251`）。
2. **结构化工具事件实时推送**：确认 AstrBot 是否暴露 agent 工具钩子（`on_agent_tool_call` 之类）抓结构化 `{tool_name, arguments, result}`。已确认 `send/send_streaming` 路径对非 webchat 只能拿文本状态（事实 7）。若有钩子可补结构化 `tool_call`/`tool_result` SSE；否则维持文本 `tool_status`。
3. **上传 TTL 清理**：`_upload_dir` 过期文件清理任务。
4. **`adapter.config` 是否为全局 `astrbot_config` 子树共享引用**：若是，`_create`/`_delete` 直接 mutate `adapter.config['tokens']` 即改根配置；若为拷贝，须改全局 `astrbot_config['platform']` 子树（§9.3）。实现期确认后简化 §9.3 写法。
5. **多实例支持**（若需）：`RuntimeState.adapter` 改 dict，`platform_id` 用 `self.meta().id`，token 全局唯一。本期单实例。

> 已关闭：`message_history_manager.insert/get` 签名（§8.1）；流式 `chain.type` 全集（事实 8）；`config_metadata` schema（§4.1）；`set_extra`（本条 1）。

---

## 13. 测试策略

- **单测**：`serializer`（链→payload、`payload.get("content")`、`File(file=)`）；`history`（镜像写入 + int `since`/`before` 过滤 + 升序 + datetime→timestamp；媒体不入库断言）；umo 拼接（`session_id=token`→`{pid}:FriendMessage:{token}`）；`SSEEvent.to_sse()`；`send_streaming` 事件序列（thinking→增量→`segment_end`→final、`tool_status` 不入答案、None 守卫）；`_put` 队列满丢弃不阻塞。
- **集成**：AstrBot pytest fixture 起 adapter+Star，模拟 `commit_event`→`send_streaming` 全链路，断言 SSE 序列与聚合；断连补消息（`since` int 比较）正确性；管理 API 全 POST、envelope 解包、`<token_hash>` kwarg；`set_extra("enable_streaming",True)` 触发流式；`_push_media` 多队列各铸独立 token。对照 `tests/test_dashboard.py`。
- **契约**：handler `Response().ok().__dict__`，bridge 解包；DELETE/PATCH 断言 405。

---

## 14. 与方案书差异清单

方案书 §4/§5 代码**整体重写**：

| 方案书 | 本设计 |
|:--|:--|
| 单 `Platform` 类做所有事 | 拆 `BotApiAdapter(Platform)` + `BotApiStar(Star)` + `RuntimeState` |
| 无装饰器 | `@register_platform_adapter(...)`（无 `support_proactive_message`，该字段只在 `meta()`） |
| `__init__` 2 参 | 3 参 |
| import 错误 | `MessageChain`←`astrbot.api.event`；`PlatformStatus`←`astrbot.core.platform.platform`；`secure_filename`←`werkzeug.utils`；`Response`←`astrbot.dashboard.routes.route` |
| `_on_reply` + `_await_reply` | 子类 `BotApiMessageEvent` 重写 `send`/`send_streaming` |
| 流式不实现 | 重写 `send_streaming`（break/reasoning/audio_chunk/aborted/plain）；入站 `set_extra("enable_streaming",True)` |
| `session_id=origin` | `session_id=token` |
| `File(file_=)` | `File(file=)` |
| `hist_{i}` | `platform_message_history` 自增 int id |
| `/message` 返回同步 reply | 纯 SSE：`{message_id}` |
| 结构化 `tool_call`/`tool_result` SSE | 降级为 `message`+`subtype:"tool_status"`；`tool_direct_result` 走普通回复+媒体 |
| 管理 API DELETE/PATCH | 全 POST + 子路径 |
| Bridge `apiDelete`/`apiPatch` | `apiPost` |
| `@register` 装饰 Star | 删除；Star 定义在 main.py（入口模块） |
| handler `jsonify` | `Response().ok(data).__dict__` envelope |
| 媒体直给 URL、历史回放 | 媒体推送时每队列铸 token、App 缓存、不入库不回放；`callback_api_base` 校验 |
| `self.context.conversation_manager` | 经 `RuntimeState` 由 Star 注入 |
| `/stream` 只给 header | quart 完整 SSE Response（`make_response`/`timeout=None`/chunked/finally 注销/`Queue(maxsize=256)`/`put_nowait`） |
| `_broadcast await q.put` | 非阻塞 `put_nowait`+`QueueFull` 丢最旧 |
| 账户 `self.config.save_config()` | 经全局 `astrbot_config` 子树 + `save_config()` + 同步运行时副本 |
| `/history` str 比较 | int 比较 |
| `_uploaded_files` 无生命周期 | 声明进程生命周期、立即引用、TTL 清理 |

方案书 §3（端点、SSE 事件类型）**保留**，仅：`/message` 出参改 `{message_id}`；`tool_call`/`tool_result` 降级为 `message`+`subtype:"tool_status"`；流式加 `streaming`/`final`/`segment_end`。§5.4 前端 HTML/CSS 沿用，`app.js` 改 `apiPost` + envelope。

---

## 附录 A：关键 API 速查

```python
# import
from astrbot.api.platform import (register_platform_adapter, Platform, PlatformMetadata,
    AstrBotMessage, MessageMember, MessageType, AstrMessageEvent, Group)
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image, File, Record
from astrbot.core.platform.platform import PlatformStatus
from astrbot.api.star import Star, Context                       # 无需 register
from astrbot.core import astrbot_config
from astrbot.dashboard.routes.route import Response              # Response().ok(data).__dict__
from astrbot.api.event.filter import after_message_sent
from werkzeug.utils import secure_filename
from quart import Quart, jsonify, request, make_response

# Platform（adapter.py 顶部装饰）
@register_platform_adapter("botapi", "...", default_config_tmpl={...}, config_metadata={...},
                          adapter_display_name="...", support_streaming_message=True)  # 无 support_proactive_message
class BotApiAdapter(Platform):
    def __init__(self, platform_config, platform_settings, event_queue):  # 3 参
        super().__init__(platform_config, event_queue)
        self.platform_id = self.meta().id; self._shutdown = asyncio.Event()
    def meta(self) -> PlatformMetadata: ...   # support_proactive_message=True 在此
    def run(self): return self.app.run_task(host=..., port=..., shutdown_trigger=self._shutdown.wait)
    async def terminate(self): self._shutdown.set(); self._put(q, None) ...
    def _put(self, q, evt): q.put_nowait(evt) except QueueFull: drop oldest   # 非阻塞
    async def _broadcast_to(self, token, evt): for q: self._put(q, evt)
    async def _push_media(self, chain, token, mid): per-queue mint + _put   # 每队列独立 token
    async def send_by_session(self, session, mc): ...

# 事件子类（event.py）
class BotApiMessageEvent(AstrMessageEvent):
    def __init__(self, message_str, message_obj, platform_meta, session_id, adapter): super().__init__(...)
    async def send(self, message: MessageChain): ...      # tool_call→tool_status；其余→serialize_chain+_push_media+persist(content)
    async def send_streaming(self, generator, use_fallback=False): ...  # break/reasoning/audio_chunk/aborted/plain；None 守卫

# 入站
msg.session_id = token; event.set_extra("enable_streaming", True); self.commit_event(event)

# 历史镜像（Context.message_history_manager，Star 注入 RuntimeState）
rt.message_history_manager.insert(platform_id=pid, user_id=token, content={...}, sender_id=..., sender_name=...)
rt.message_history_manager.get(platform_id=pid, user_id=token, page=1, page_size=200)  # 升序；row.id(int)/.content/.created_at(datetime)
rt.conversation_manager.get_curr_conversation_id(f"{pid}:FriendMessage:{token}")      # async
rt.conversation_manager.get_conversation(f"{pid}:FriendMessage:{token}", cid)         # async

# 管理 API（Star 在 main.py，无 @register）
context.register_web_api(f"/astrbot_plugin_botapi/stats", self._stats, ["GET"], "...")
# async def _stats(self): return Response().ok(data).__dict__
# 前端: bridge.apiGet("stats") / bridge.apiPost("accounts/<hash>/delete", {})  # <hash> 替换真实值

# 账户持久化
from astrbot.core import astrbot_config
# 改全局 astrbot_config['platform'] 子树（按 id 定位）的 tokens + 同步 adapter.config/cfg + astrbot_config.save_config()

# 媒体（推送时每队列铸，不入库）
url = await comp.register_to_file_service()   # 需 callback_api_base；每调铸新 token
# fallback: File→await comp.get_file(allow_return_url=True); Image/Record→await comp.convert_to_file_path()（返回本地路径非 URL）
```

## 附录 B：file:line 引用索引

| 事实 | 源码位置 |
|:--|:--|
| Platform 无 context | `core/platform/platform.py:38`、`manager.py:210`、`star/base.py:23-26`、`star/context.py:94` |
| `@register_platform_adapter`（无 support_proactive_message） | `core/platform/register.py:11-20,34-41,46-57`、`platform_metadata.py:22` |
| 3 参 `__init__` | `manager.py:210`、`webchat_adapter.py:62-68`、`platform.py:38` |
| 回复回流 `send/send_streaming` | `pipeline/respond/stage.py:200,249,276,285`、`astr_message_event.py:278-290,474-491` |
| 流式需 `enable_streaming`/`set_extra` | `config/default.py:137`、`internal.py:170-172,336-349`、`astr_message_event.py:221-223`、`webchat_adapter.py:249-251` |
| umo 驼峰 | `message_session.py:18`、`message_type.py:4-7`、`astr_message_event.py:68-69`、`register.py:40-41` |
| 工具事件非 webchat 仅文本 / `tool_direct_result` | `astr_agent_run_util.py:199-207,229-237,61-65`、`astr_agent_tool_exec.py:670,676-681`、`internal.py:73-74` |
| 流式 chain.type 全集 / None | `astr_agent_run_util.py:123,218,271,454,516-517`、`internal.py:309-310`、`tool_loop_agent_runner.py:1395` |
| Conversation 无逐条 id / 9 字段 | `db/po.py:531-552`、`conversation_mgr.py:70`、`internal.py:94-96` |
| reasoning 在 ThinkPart | `agent/message.py:195-246,90-107,255-258`、`tool_loop_agent_runner.py:885-891` |
| platform_message_history 表 | `db/po.py:226-247`、`platform_message_history_mgr.py:9-17,28-43`、`webchat_adapter.py:148` |
| 管理 API 仅 GET/POST | `dashboard/server.py:311-314` |
| Bridge 无 apiDelete/apiPatch | `dashboard/plugin_page_bridge.js:201-268`、`PluginPagePage.vue:120-123,215-231,480` |
| `register_web_api` 在 Context | `star/context.py:515-521`、`dashboard/server.py:321-331` |
| `register_to_file_service` | `components.py:531,245,311,858,258`、`file_token_service.py:40,67,95,12`、`config/default.py:302,255`、`dashboard/routes/file.py:15`、`server.py:404-413` |
| `File(file=)`/`get_file`/`convert_to_file_path` | `components.py:742,746-778,793,478,193` |
| import 路径 | `api/platform/__init__.py:1-22`、`api/event/__init__.py`、`core/platform/platform.py:19`、`core/__init__.py:33`、`dashboard/routes/route.py:42-59`、`api/event/filter/__init__.py:11` |
| quart 自带 / `@register` 废弃 | `pyproject.toml:44`、`star/register/star.py:8-17,41-45`、`star/base.py:38-49`、`star_manager.py:1005-1019` |
| Star 须在入口模块 main.py | `star/base.py:38-49`、`star_manager.py:944-945,999,1051-1064,1117,262-272` |
| SSE quart 范本 / 非阻塞队列 | `dashboard/routes/log.py:15-21,28,45-101,84-86`、`core/log.py:134,144-147` |
| config_metadata schema | `config/default.py:546-614,1081-1084`、`config.py:1511-1533,1561-1572`、`platform/sources/line/line_adapter.py:27-38` |
| 账户持久化 save_config | `config/astrbot_config.py:216`、`core/__init__.py:33`、`manager.py:31,92,102,118` |
| metadata 校验 | `star_manager.py:483-528,573-581,606-637`、`zip_updator.py:272` |
| 事件循环同 loop | `core_lifecycle.py:283`、`manager.py:50` |
