# adapter.py
import asyncio
import time
import uuid
from collections import defaultdict
from pathlib import Path

from astrbot.api.platform import (register_platform_adapter, Platform,
    PlatformMetadata, AstrBotMessage, MessageMember, MessageType)
from astrbot.api.event import MessageChain
from astrbot.core import astrbot_config
from astrbot.core.utils.metrics import Metric

from .models import BotApiConfig, SSEEvent
from .serializer import MessageSerializer
from .runtime import runtime
from . import sessions as _sessions


@register_platform_adapter(
    "botapi",
    "BotAPI 自定义移动端适配器 — 一人一 Bot 极简移动端接入，支持弱网断连恢复",
    # 模板须非空：AstrBot config_service 用 `if not platform.default_config_tmpl: continue`
    # 跳过空模板，空 dict 会导致新建平台下拉看不到 botapi。tokens 不入模板
    # （账户注册表在插件配置，靠 _star_managed 分支区分布建流程，无则回落条目模式）。
    default_config_tmpl={"id": "botapi", "type": "botapi", "enable": False},
    adapter_display_name="BotAPI 移动端",
    support_streaming_message=True,
)
class BotApiAdapter(Platform):
    def __init__(self, platform_config, platform_settings, event_queue) -> None:
        super().__init__(platform_config, event_queue)
        self.platform_id = platform_config.get("id", "botapi")
        # _shutdown 需在 _is_entry 分支前设置：条目模式的 run() 返回 _shutdown.wait()。
        self._shutdown = asyncio.Event()
        self._is_entry = not platform_config.get("_star_managed")
        if self._is_entry:
            # 条目模式：极简占位壳，仅记录 id 作为绑定目标。不建 app、不设 runtime、不读插件配置。
            return
        # 插件模式：完整初始化
        self.client_self_id = uuid.uuid4().hex
        self.cfg = BotApiConfig(tokens=[], sessions={})
        self._token_to_origin: dict = {}
        self._sse_clients: dict = defaultdict(list)
        self._active_platforms: set = set()
        self._disabled_tokens: set = set()
        self._last_active: dict = {}
        self._uploaded_files: dict = {}
        self._upload_dir = Path(astrbot_config.get("data_path", "./data")) / "botapi_uploads"
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._media_enabled = bool(astrbot_config.get("callback_api_base"))
        self._serializer = MessageSerializer(_media_enabled=self._media_enabled)
        runtime().adapter = self
        from quart import Quart
        self.app = Quart(__name__)
        from .routes import _setup_routes
        self._setup_routes = lambda: _setup_routes(self)
        self._setup_routes()
        from .plugin_conf import load_plugin_conf, get_tokens, get_host, get_port
        self._conf = load_plugin_conf()
        self._host = get_host() or "0.0.0.0"
        self._port = get_port() or 9000
        self._migrate_accounts()
        self.cfg.tokens = get_tokens()
        self._server_started = False

    def _migrate_accounts(self):
        """把平台条目残留的账户数据收敛进插件配置（全局）。

        1. 插件 tokens 为空且 botapi 平台条目 tokens 非空 → 迁到插件（剥离条目 tokens）。
        2. 清理 botapi 平台条目的 botapi_bindings/nicknames/host/port/sessions，
           并重新启用所有 type==botapi 条目（enable=True，v3.0.4 起 botapi 重新注册为
           平台适配器，entries 是绑定目标；v3.0.3 曾禁用它们防 adapter not found）。
        幂等：插件 tokens 非空即视为已迁移，跳过 1（2 仍执行，无键即 no-op）；
        已迁移的 botapi 条目 tokens 保留不动（只经 1 剥离一次）。
        绑定关系存插件配置 bindings 表（后台重建策略），迁移不做 bindings→平台展开。
        """
        from .plugin_conf import get_tokens, set_tokens, save
        try:
            platforms = astrbot_config.get("platform")
            if not isinstance(platforms, list):
                return
            botapi_id = "botapi"
            changed = False
            plugin_tokens = get_tokens()
            if not plugin_tokens:
                # 1. botapi 平台条目 tokens → 插件 tokens
                for p in platforms:
                    if p.get("id") == botapi_id:
                        pt = p.get("tokens") or []
                        if pt:
                            set_tokens(list(pt))
                            p.pop("tokens", None)
                            changed = True
                        break
            # 2. 清理 botapi 平台条目旧键并重新启用（tokens 仅经 1 剥离，幂等场景保留）。
            #    v3.0.4 起 botapi 重新注册为平台适配器，botapi 条目恢复作为绑定目标；
            #    原 enable=False 条目（v3.0.3 迁移残留）须恢复 True，
            #    否则 PlatformManager 启动时该条目被跳过（绑定目标不可达）。
            #    遍历所有 type==botapi 条目（不只 id 首个），处理其他 id 的 botapi 残留。
            for p in platforms:
                if p.get("type") == botapi_id:
                    for key in ("botapi_bindings", "nicknames", "host", "port", "sessions"):
                        if key in p:
                            p.pop(key, None)
                            changed = True
                    # 反转 v3.0.3：重新启用 botapi 条目（作为绑定目标）。
                    # 重新注册后 enable 不再触发 adapter not found。
                    if p.get("enable") is not True:
                        p["enable"] = True
                        changed = True
            if changed:
                save()
                # 平台条目清理必须持久化到 astrbot_config（核心 config.json），否则磁盘残留旧键，
                # 用户删光账户后重启 _migrate_accounts 会从残留 tokens 复活账户。
                try:
                    _save = getattr(astrbot_config, "save_config", None)
                    if _save:
                        _save()
                except Exception:
                    pass
        except Exception:
            pass

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="botapi",
            description="BotAPI 自定义移动端适配器",
            id=self.platform_id,
            adapter_display_name="BotAPI 移动端",
            support_streaming_message=True,
            support_proactive_message=True,
        )

    def run(self):
        if self._is_entry:
            # 条目模式：仅等 shutdown（占位壳，不建 server）。
            return self._shutdown.wait()
        # 插件模式：host/port 来自插件配置（插件配置页）。服务器生命周期由 Star 拉起。
        return self.app.run_task(host=self._host, port=self._port,
                                 shutdown_trigger=self._shutdown.wait)

    async def shutdown(self) -> None:
        self._shutdown.set()
        runtime().adapter = None
        for token, queues in list(self._sse_clients.items()):
            for q in queues:
                self._put(q, None)

    async def send_by_session(self, session, message_chain) -> None:
        if getattr(self, "_is_entry", False):
            # 条目占位壳：无 SSE 客户端/序列化器（__init__ 早退），本身无法投递。
            # AstrBot context.send_message 按 platform.meta().id 在 platform_insts 找
            # 回复目标，绑定账户 UMO 前缀 = 条目 id → 命中的是壳实例。转发到插件实例
            # （跑服务器、持有 SSE 客户端），否则主动消息被基类 no-op 静默丢弃。
            rt_adapter = getattr(runtime(), "adapter", None)
            if rt_adapter is not None and rt_adapter is not self:
                await rt_adapter.send_by_session(session, message_chain)
                return
        # 原 Platform.send_by_session 的指标埋点（去 Platform 继承后自实现）：
        # create_task 调度 Metric.upload（不阻塞本次发送）。
        asyncio.create_task(
            Metric.upload(msg_event_tick=1, adapter_name=self.meta().name)
        )
        # session.session_id 是 MessageSession 的第三段（裸 scoped key）：
        # 默认会话="{token}"，分会话="{token}:{sid}"（完整 umo 由 __str__ 拼前缀）。
        sess_id = session.session_id
        # 绑定平台 UMO：第三段带 botapi_ 前缀 → 反向解析回 botapi token
        if sess_id.startswith("botapi_"):
            rest = sess_id[len("botapi_"):]
            parts = rest.split(":")
            token = parts[0]
            sid = parts[1] if len(parts) > 1 else "default"
        else:
            parts = sess_id.split(":")
            token = parts[0] if len(parts) > 0 else sess_id
            sid = parts[1] if len(parts) > 1 else "default"
        mid = f"botapi_proactive_{uuid.uuid4().hex[:12]}"
        payload = await self._serializer.serialize_chain(message_chain, None)
        scoped = _sessions.scoped_key_for(self, token, sid)
        evt = SSEEvent("message", {**payload, "streaming": False, "final": True,
                                   "session_id": "" if sid == "default" else sid})
        await self._broadcast_to(scoped, evt)
        await self._push_media(message_chain, token, mid, sid)

    # ── 非阻塞 SSE 投递（spec §4.2）──
    def _put(self, q: asyncio.Queue, evt):
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(evt)
            except Exception:
                pass

    async def _broadcast_to(self, token: str, evt: SSEEvent):
        for q in list(self._sse_clients.get(token, [])):
            self._put(q, evt)

    async def _push_media(self, chain, token: str, message_id: str, sid="default"):
        if chain is None:
            return
        scoped = _sessions.scoped_key_for(self, token, sid)
        queues = list(self._sse_clients.get(scoped, []))
        for comp in (chain.chain or []):
            ct = comp.type.value.lower() if hasattr(comp.type, "value") else str(comp.type).lower()
            if ct not in ("image", "record", "file"):
                continue
            mtype = {"image": "image", "record": "audio", "file": "file"}[ct]
            for q in queues:   # 每队列铸独立 token
                url = await self._serializer._media_url(comp)
                if not url:
                    continue
                data = {"message_id": message_id, "type": mtype,
                        "content": ({"name": getattr(comp, "name", "file"), "url": url}
                                    if mtype == "file" else url),
                        "streaming": False, "final": False, "timestamp": int(time.time()),
                        "session_id": "" if sid == "default" else sid}
                self._put(q, SSEEvent("message", data))

    # ── token→platform 绑定（多机器人）──
    # 绑定存插件配置 bindings 列表（[{token, platform_id}]，一对一）。全局数据不存
    # 平台条目（update_bot 会整体覆盖导致丢失）。插件做 token→平台索引，
    # 平台→abconf 路由交给 AstrBot（用户在配置文件管理页配 umop_config_routing）。

    def commit_event(self, event) -> None:
        self._event_queue.put_nowait(event)

    def binding_platform_for(self, token: str) -> str | None:
        """返回 token 绑定的 platform_id；未绑定或平台不活跃返回 None。"""
        from .plugin_conf import get_bindings
        pid = None
        for item in get_bindings():
            if item.get("token") == token and item.get("platform_id"):
                pid = item["platform_id"]
                break
        if not pid:
            return None
        active = getattr(self, "_active_platforms", None)
        if active is not None and active:
            return pid if pid in active else None
        try:
            for p in astrbot_config.get("platform", []):
                if p.get("id") == pid:
                    return pid if p.get("enable") else None
        except Exception:
            pass
        return None

    def unbind_token(self, token: str) -> None:
        """从插件配置 bindings 移除该 token 条目；有变更才落盘。"""
        from .plugin_conf import get_bindings, set_bindings, save
        binds = [b for b in get_bindings() if b.get("token") != token]
        if len(binds) != len(get_bindings()):
            set_bindings(binds)
            save()

    def bind_token(self, token: str, platform_id: str) -> None:
        """绑定 token → 目标平台（含 botapi 条目）：先移除旧条目（一对一），再追加。"""
        from .plugin_conf import get_bindings, set_bindings, save
        binds = [b for b in get_bindings() if b.get("token") != token]
        binds.append({"token": token, "platform_id": platform_id})
        set_bindings(binds)
        save()
