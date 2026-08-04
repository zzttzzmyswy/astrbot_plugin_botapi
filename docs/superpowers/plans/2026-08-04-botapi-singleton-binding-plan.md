# botapi 单实例化 + 多机器人绑定实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** botapi 单实例化（端口移到插件配置页），支持多机器人（AstrBot platform 实例）各绑定 botapi 账户，用户新会话自动路由到绑定平台配置文件。

**Architecture:** botapi 仍注册为平台适配器，但 Quart 服务器改由插件 Star 单例启动（adapter.run() 变 no-op）。新增 token→platform 绑定表存插件配置。提交消息时设置 `event.unified_msg_origin` 为绑定平台 UMO（`{platform}:FriendMessage:botapi_{token}[:{sid}]`），让 AstrBot 原生 `umop_config_routing` 路由到绑定平台配置文件；历史/清空/统计读绑定平台 conversation。回复经 `event.send()` 虚分派走 botapi SSE。

**Tech Stack:** Python (Quart + asyncio + pytest) · AstrBot Star/Platform API

## Global Constraints

（来自已批准 spec `docs/2026-08-04-botapi-singleton-binding-design.md`）

- **绑定 = botapi token → platform_id**（多对一，一个 platform 可绑多个 token）。
- **绑定后事件 UMO = `{platform_id}:FriendMessage:botapi_{token}[:{sid}]`**（session_id 前缀 `botapi_` 防冲突）。
- **未绑定 token**：保持 botapi 身份 UMO（`botapi:FriendMessage:{token}[:{sid}]`），回退行为不变。
- **绑定表存插件配置**（`_conf_schema.json` 声明），插件配置页可编辑。
- **单实例**：Quart 由插件 Star 启动一次；`BotApiAdapter.run()` 改 no-op。host/port 读插件配置，回退旧平台配置再回退 9000。
- **回复链路**：`event.send()`/`send_streaming` 虚分派到 `BotApiMessageEvent.send` override（botapi SSE），绑定不改回复路径。
- **`send_by_session`（主动消息）**：session_id 带 `botapi_` 前缀时剥前缀得裸 token（+ 可选 `:sid`），投 botapi scoped SSE。
- **历史/清空/统计**：读绑定平台 UMO（`binding_platform_for(token) or adapter.platform_id`）。
- **不改 AstrBot 核心**；不改 App 端接口。
- AstrBot ≥ 4.25.5；插件版本 → v3.0.0。
- 绑定的 platform 不存在/停用 → 回退 botapi 身份 UMO。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `_conf_schema.json`（新建） | 插件配置 schema：host/port/botapi_bindings |
| `adapter.py` | `BotApiAdapter.run()` no-op；`binding_platform_for(token)`；`send_by_session` 反向解析 `botapi_` 前缀 |
| `main.py` | `BotApiStar` 读插件配置起单例 Quart；绑定管理 Web 路由；`_do_history/_do_clear/_do_stats` 用绑定平台 umo |
| `routes.py` | `submit_inbound` 设置绑定 UMO |
| `history.py` | `get_conversation_messages` 调用方传绑定平台 id（函数本身不改） |
| `sessions.py` | `umo_for` 支持绑定平台（可选：调用方传 platform_id） |
| `pages/dashboard/app.js` | 账户列表「绑定机器人」操作 |
| `tests/` | 各新测试文件 |

---

### Task 1: 插件配置 schema + 单例 Quart 启动

**Files:**
- Create: `astrbot_plugin_botapi/_conf_schema.json`
- Modify: `astrbot_plugin_botapi/adapter.py`（`run()` no-op）
- Modify: `astrbot_plugin_botapi/main.py`（Star 读配置起 Quart）
- Test: `astrbot_plugin_botapi/tests/test_singleton.py`（新建）

**Interfaces:**
- Consumes: `Star.__init__(context, config)` 的 `config` 是插件配置（AstrBotConfig，读 `{plugin_dir}_config.json` + `_conf_schema.json`）
- Produces:
  - `BotApiStar` 启动单例 Quart：`host = config.get("host", ...)`、`port = config.get("port", ...)`
  - `BotApiAdapter.run()` 返回 `asyncio.Event().wait()`（永不结束但不起服务器）
  - `_conf_schema.json` 声明 `host`/`port`/`botapi_bindings`

- [ ] **Step 1: 写失败测试**（`tests/test_singleton.py`）

```python
# tests/test_singleton.py
import asyncio
from types import SimpleNamespace
import pytest


def _adapter(monkeypatch, config=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": [], "nicknames": {}, "sessions": {}}
    a.cfg = SimpleNamespace(tokens=[], nicknames={}, sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    a._serializer = SimpleNamespace()
    a._media_enabled = True
    return a


def test_adapter_run_is_noop():
    """单实例：adapter.run() 不再起 Quart 服务器。"""
    a = _adapter()
    run_result = a.run()
    # run() 返回一个永不完成的协程（或 None），但不能真的绑定端口
    assert not hasattr(a, "_started") or a._started is False


def test_conf_schema_declares_port_and_bindings():
    """插件配置 schema 必须声明 host/port/botapi_bindings。"""
    import json
    from pathlib import Path
    schema = json.loads(
        Path("astrbot_plugin_botapi/_conf_schema.json").read_text(encoding="utf-8-sig"))
    assert "host" in schema and "port" in schema and "botapi_bindings" in schema
    assert schema["port"]["type"] == "int"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_singleton.py -v`
Expected: FAIL（`_conf_schema.json` 不存在；`run()` 仍起服务器）

- [ ] **Step 3: 实现最小代码**

`_conf_schema.json`（新建）：

```json
{
  "host": {"description": "botapi 监听地址", "type": "string", "default": "0.0.0.0"},
  "port": {"description": "botapi 监听端口", "type": "int", "default": 9000},
  "botapi_bindings": {
    "description": "botapi 账户绑定机器人：{token: platform_id}",
    "type": "object",
    "items": {"type": "string"},
    "default": {}
  }
}
```

`adapter.py` `run()` 改 no-op：

```python
    def run(self):
        """单实例化：Quart 服务器由 adapter.__init__ 启动，本方法不再绑定端口。
        返回一个永不完成的协程，使 AstrBot PlatformManager 的任务无害驻留。"""
        return self._shutdown.wait()
```

**Quart 启动时机（已核对 AstrBot 加载顺序）**：`core_lifecycle.py` 里 `plugin_manager.reload()`（实例化 Star）
在 `platform_manager.initialize()`（实例化 botapi adapter）**之前**执行。故 `BotApiStar.__init__` 时
`runtime().adapter` 尚为 None，不能在那启动 Quart。**方案**：adapter 在 `__init__` 末尾读取插件配置文件
（`{get_astrbot_config_path()}/{root_dir_name}_config.json`，root_dir_name=`astrbot_plugin_botapi`）
拿 host/port 并启动单例 Quart。插件配置由 `_conf_schema.json` 声明 schema，plugin_config 是
AstrBotConfig（`config_path={plugin_config_path}/{root_dir_name}_config.json`）。

`main.py` `BotApiStar.__init__`：

```python
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        rt = runtime()
        rt.context = context
        rt.conversation_manager = context.conversation_manager
        rt.message_history_manager = context.message_history_manager
        ...
        # 插件配置（插件配置页可编辑 host/port/botapi_bindings）
        self._plugin_conf = config if isinstance(config, dict) else {}
```

adapter 端启动单例 Quart（`adapter.py` `__init__` 末尾）：

```python
        # 单实例化：从插件配置文件读 host/port 启动 Quart
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_config_path
            conf = AstrBotConfig(
                config_path=os.path.join(get_astrbot_config_path(),
                                         "astrbot_plugin_botapi_config.json"),
                schema=self._load_plugin_schema(),
            )
            self._host = conf.get("host") or self._legacy_port()[0] or "0.0.0.0"
            self._port = conf.get("port") or self._legacy_port()[1] or 9000
        except Exception:
            self._host, self._port = self._legacy_port() or ("0.0.0.0", 9000)
        self._server_started = False
```

（`_load_plugin_schema` 读插件目录 `_conf_schema.json`；`_legacy_port` 读 `astrbot_config["platform"]` 里
type=botapi 条目的 host/port。启动任务 `asyncio.create_task(self.app.run_task(host, port))` 在 adapter
初始化完成、事件循环运行后触发——由 `run()` 改为真正启动？见下。）

**最终决定**：Quart 启动仍由 `run()` 负责（AstrBot PlatformManager 会调用 `inst.run()`），但 host/port
**从插件配置读取**而非平台配置。`run()` 改为：

```python
    def run(self):
        # host/port 来自插件配置（插件配置页），非平台配置
        return self.app.run_task(host=self._host, port=self._port,
                                 shutdown_trigger=self._shutdown.wait)
```

这样既保单实例（AstrBot 每个 botapi platform 条目都调 run()，但 host/port 一致——多条目会起多个
服务器？需 Task 5 确保只保留一个 botapi platform 条目，或 run() 用模块级锁保证单服务器）。
```

（注：此任务的「起单例 Quart」依赖 adapter 已初始化。真实加载顺序是 Star 先于 adapter 实例化？需在 Task 3 核对——若 Star 先于 adapter，则 `_ensure_quart_server` 需在 adapter 就绪后触发（如 adapter.__init__ 或 Star 的异步钩子）。Task 1 先实现 schema + run() no-op + 读配置逻辑，Task 3 完善启动时机。）

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_singleton.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_singleton.py _conf_schema.json adapter.py main.py
git commit -m "feat(server): 插件配置 schema + adapter.run() no-op + 单例 Quart 启动逻辑"
```

---

### Task 2: token→platform 绑定表（存储 + 查询）

**Files:**
- Modify: `astrbot_plugin_botapi/adapter.py`
- Modify: `astrbot_plugin_botapi/main.py`
- Test: `astrbot_plugin_botapi/tests/test_binding_storage.py`（新建）

**Interfaces:**
- Consumes: Task 1 的插件配置 `botapi_bindings`
- Produces:
  - `BotApiAdapter.binding_platform_for(token) -> str | None`（读绑定表，校验 platform 活跃）
  - `BotApiAdapter.bind_token(token, platform_id)` / `unbind_token(token)`（写绑定表 + 持久化）
  - Web 路由：`POST accounts/<token_hash>/bind` body `{platform_id}`、`POST accounts/<token_hash>/unbind`

- [ ] **Step 1: 写失败测试**（`tests/test_binding_storage.py`）

```python
# tests/test_binding_storage.py
from types import SimpleNamespace
import pytest


def _adapter(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": ["tok1"], "nicknames": {}, "sessions": {}}
    a.cfg = SimpleNamespace(tokens=["tok1"], nicknames={}, sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    # 模拟活跃平台列表（PlatformManager._inst_map）
    a._active_platforms = {"aiocqhttp_main", "telegram_x"}
    return a


def test_binding_platform_for_returns_platform(monkeypatch):
    a = _adapter(monkeypatch)
    # 从插件配置读绑定表（此处模拟）
    a.config["botapi_bindings"] = {"tok1": "aiocqhttp_main"}
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_unbound_returns_none(monkeypatch):
    a = _adapter(monkeypatch)
    a.config["botapi_bindings"] = {}
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_inactive_returns_none(monkeypatch):
    """绑定的 platform 不在活跃列表 → 回退。"""
    a = _adapter(monkeypatch)
    a.config["botapi_bindings"] = {"tok1": "dead_platform"}
    assert a.binding_platform_for("tok1") is None


def test_bind_token_persists(monkeypatch):
    a = _adapter(monkeypatch)
    a.config["botapi_bindings"] = {}
    a.bind_token("tok1", "telegram_x")
    assert a.config["botapi_bindings"]["tok1"] == "telegram_x"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_binding_storage.py -v`
Expected: FAIL（`binding_platform_for` 不存在）

- [ ] **Step 3: 实现最小代码**

`adapter.py` 增加：

```python
    # ── token→platform 绑定（多机器人）──

    def binding_platform_for(self, token: str) -> str | None:
        """返回 token 绑定的 platform_id；未绑定或平台不活跃返回 None。"""
        bindings = self.config.get("botapi_bindings") or {}
        pid = bindings.get(token)
        if not pid:
            return None
        active = getattr(self, "_active_platforms", None)
        if active is not None and pid not in active:
            return None
        return pid

    def bind_token(self, token: str, platform_id: str) -> None:
        bindings = dict(self.config.get("botapi_bindings") or {})
        bindings[token] = platform_id
        self.config["botapi_bindings"] = bindings

    def unbind_token(self, token: str) -> None:
        bindings = dict(self.config.get("botapi_bindings") or {})
        bindings.pop(token, None)
        self.config["botapi_bindings"] = bindings
```

（`_active_platforms` 需在真实 adapter 初始化时注入——Task 5 处理从 PlatformManager 获取。测试里手动设。）

`main.py` 增加 Web 路由与 `_do_bind`：

```python
    async def _do_bind(self, token_hash, platform_id):
        adapter = runtime().adapter
        target = next((t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash), None)
        if not target:
            return Response().error("未找到账户").__dict__
        if not platform_id:
            return Response().error("platform_id 不能为空").__dict__
        adapter.bind_token(target, platform_id)
        # 持久化绑定表到插件配置
        self._persist_bindings(adapter)
        return Response().ok({"message": "绑定成功"}).__dict__

    def _persist_bindings(self, adapter):
        """把 adapter.config 的绑定表同步到插件配置并落盘。"""
        # 插件配置 AstrBotConfig 的保存路径
        if self._plugin_conf:
            self._plugin_conf["botapi_bindings"] = adapter.config.get("botapi_bindings") or {}
            self._plugin_conf.save_config()
```

Web 路由注册（`__init__`）：
```python
        context.register_web_api(f"/{P}/accounts/<token_hash>/bind", self._bind, ["POST"], "绑定机器人")
        context.register_web_api(f"/{P}/accounts/<token_hash>/unbind", self._unbind, ["POST"], "解绑机器人")
```

`_bind`/`_unbind` 薄封装调 `_do_bind`/`_do_unbind`。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_binding_storage.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_binding_storage.py adapter.py main.py
git commit -m "feat(server): token→platform 绑定表 + 绑定/解绑 Web 路由"
```

---

### Task 3: submit_inbound 设置绑定 UMO + 启动时机完善

**Files:**
- Modify: `astrbot_plugin_botapi/routes.py`
- Modify: `astrbot_plugin_botapi/adapter.py`（`_active_platforms` 注入）
- Modify: `astrbot_plugin_botapi/main.py`（Quart 启动时机）
- Test: `astrbot_plugin_botapi/tests/test_binding_routing.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `binding_platform_for(token)`
- Produces:
  - `submit_inbound` 绑定后设置 `event.unified_msg_origin = f"{pid}:FriendMessage:botapi_{scoped_key}"`
  - adapter 初始化时 `_active_platforms` 从 PlatformManager 读取
  - Quart 单例在 adapter 就绪后启动（Star + adapter 协同）

- [ ] **Step 1: 写失败测试**（`tests/test_binding_routing.py`）

```python
# tests/test_binding_routing.py
from types import SimpleNamespace
import pytest
from astrbot_plugin_botapi import sessions as S


def _adapter(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": ["tok"], "nicknames": {}, "sessions": {}}
    a.cfg = SimpleNamespace(tokens=["tok"], nicknames={}, sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    a.client_self_id = "self"
    a._uploaded_files = {}
    a._serializer = SimpleNamespace()
    a.commit_event = lambda e: None
    a._active_platforms = {"aiocqhttp_main"}
    monkeypatch.setattr(S, "astrbot_config", {"platform": []})
    return a


@pytest.mark.asyncio
async def test_submit_inbound_bound_uses_platform_umo(monkeypatch):
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch)
    a.config["botapi_bindings"] = {"tok": "aiocqhttp_main"}
    committed = []

    async def fake_persist(key, mid, text):
        pass
    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)

    def fake_commit(event):
        committed.append(event)
    a.commit_event = fake_commit

    await routes_mod.submit_inbound(a, "tok", "hi")
    assert committed[0].unified_msg_origin == "aiocqhttp_main:FriendMessage:botapi_tok"
    assert committed[0].unified_msg_origin.count("botapi") == 1


@pytest.mark.asyncio
async def test_submit_inbound_bound_scoped_sid(monkeypatch):
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch)
    a.config["botapi_bindings"] = {"tok": "aiocqhttp_main"}
    cur = S.sessions_list(a, "tok")
    cur.append({"id": "abc", "name": "x", "created_at": 1})
    S.save_sessions(a, "tok", cur)
    committed = []

    async def fake_persist(key, mid, text):
        pass
    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)

    def fake_commit(event):
        committed.append(event)
    a.commit_event = fake_commit

    await routes_mod.submit_inbound(a, "tok", "hi", session_id="abc")
    assert committed[0].unified_msg_origin == "aiocqhttp_main:FriendMessage:botapi_tok:abc"


@pytest.mark.asyncio
async def test_submit_inbound_unbound_keeps_botapi_umo(monkeypatch):
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch)
    a.config["botapi_bindings"] = {}
    committed = []

    async def fake_persist(key, mid, text):
        pass
    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)

    def fake_commit(event):
        committed.append(event)
    a.commit_event = fake_commit

    await routes_mod.submit_inbound(a, "tok", "hi")
    assert committed[0].unified_msg_origin == "botapi:FriendMessage:tok"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_binding_routing.py -v`
Expected: FAIL（`submit_inbound` 未设置绑定 UMO）

- [ ] **Step 3: 实现最小代码**

`routes.py` `submit_inbound` 末尾加绑定 UMO：

```python
    event = BotApiMessageEvent(message_str=msg.message_str, message_obj=msg,
                               platform_meta=adapter.meta(), session_id=scoped_key,
                               adapter=adapter)
    # 多机器人绑定：覆写 UMO 使 AstrBot 路由到绑定平台配置文件
    bound = adapter.binding_platform_for(token)
    if bound:
        event.unified_msg_origin = f"{bound}:FriendMessage:botapi_{scoped_key}"
    event.set_extra("enable_streaming", True)
    await persist_inbound_text(scoped_key, msg.message_id, text)
    adapter.commit_event(event)
    return msg.message_id
```

`adapter.py` `__init__` 注入 `_active_platforms`：

```python
        self._active_platforms = set()
        # 从 PlatformManager 读活跃平台（init 时 platform_manager 可能未就绪，Task 5 用 Star 补齐）
```

`main.py` 完善 Quart 启动时机：`BotApiStar` 提供一个 `start_quarts_if_ready()`，在 adapter 就绪后（如 `_do_stats` 首次或 Star 延迟任务）调用 `_ensure_quart_server()`。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_binding_routing.py tests/test_sessions_routing.py -v`
Expected: PASS（既有 routing 测试不受影响——未绑定走 botapi UMO）

- [ ] **Step 5: 提交**

```bash
git add tests/test_binding_routing.py routes.py adapter.py main.py
git commit -m "feat(server): submit_inbound 绑定后设置绑定平台 UMO"
```

---

### Task 4: 历史/清空/统计读绑定平台 conversation

**Files:**
- Modify: `astrbot_plugin_botapi/history.py`
- Modify: `astrbot_plugin_botapi/main.py`
- Test: `astrbot_plugin_botapi/tests/test_binding_history.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `binding_platform_for(token)`
- Produces: `_do_history`/`_do_clear`/`_do_stats` 用绑定平台 id 构造 umo

- [ ] **Step 1: 写失败测试**

```python
# tests/test_binding_history.py
from types import SimpleNamespace
import pytest
from astrbot_plugin_botapi import sessions as S


def _adapter(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": ["tok"], "nicknames": {}, "sessions": {}}
    a.cfg = SimpleNamespace(tokens=["tok"], nicknames={}, sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = {"aiocqhttp_main"}
    a.config["botapi_bindings"] = {"tok": "aiocqhttp_main"}
    monkeypatch.setattr(S, "astrbot_config", {"platform": []})
    return a


@pytest.mark.asyncio
async def test_history_uses_bound_platform_umo(monkeypatch):
    """绑定后 /history 读绑定平台 conversation。"""
    from astrbot_plugin_botapi import history as H
    from astrbot_plugin_botapi.runtime import runtime
    rt = runtime()
    seen = {}

    class FakeCM:
        async def get_curr_conversation_id(self, umo):
            seen["umo"] = umo
            return None
    rt.conversation_manager = FakeCM()
    a = _adapter(monkeypatch)
    await H.get_conversation_messages(rt, a.binding_platform_for("tok") or a.platform_id,
                                      "botapi_tok", 50)
    assert seen["umo"] == "aiocqhttp_main:FriendMessage:botapi_tok"
```

（注：`get_conversation_messages(rt, platform_id, token, limit)` 的 `token` 参数传入 `botapi_tok`（绑定后的第三段），`platform_id` 传绑定平台——拼出的 umo 正确。调用方 `_do_history` 需按此构造。）

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_binding_history.py -v`
Expected: FAIL（`get_conversation_messages` 未按绑定平台调用）

- [ ] **Step 3: 实现最小代码**

`main.py` `_do_history`：

```python
        bound = adapter.binding_platform_for(target)
        platform_id = bound or adapter.platform_id
        # 绑定后第三段带 botapi_ 前缀
        scoped_key = _sessions.scoped_key_for(adapter, target, sid)
        if bound:
            scoped_key = f"botapi_{scoped_key}"
        msgs = await get_conversation_messages(rt, platform_id, scoped_key, limit)
```

`_do_clear`：

```python
        bound = adapter.binding_platform_for(target)
        platform_id = bound or adapter.platform_id
        umo = f"{platform_id}:FriendMessage:"
        umo += ("botapi_" + _sessions.scoped_key_for(adapter, target, sid)
                if bound else _sessions.scoped_key_for(adapter, target, sid))
        await rt.conversation_manager.new_conversation(umo)
```

`_do_stats`：消息计数用 `binding_platform_for(token)` 决定的 umo。

（helper 提取：`_bound_scoped_key(adapter, token, sid)` 返回带/不带 `botapi_` 前缀的 scoped key；`_bound_platform(adapter, token)` 返回平台 id。两 helper 放 adapter 或 main。）

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_binding_history.py tests/test_chat.py tests/test_admin_handlers.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_binding_history.py history.py main.py
git commit -m "feat(server): 历史/清空/统计读绑定平台 conversation"
```

---

### Task 5: 活跃平台注入 + 单例 Quart 启动时机完善

**Files:**
- Modify: `astrbot_plugin_botapi/main.py`
- Modify: `astrbot_plugin_botapi/adapter.py`
- Test: `astrbot_plugin_botapi/tests/test_singleton.py`（续写）

**Interfaces:**
- Consumes: Task 1 的 host/port 从插件配置读取；Task 3 的 `_active_platforms`
- Produces: `BotApiStar` 启动后注入活跃平台到 adapter；adapter.run() 模块级锁保证单服务器

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_singleton.py`）

```python
def test_star_injects_active_platforms(monkeypatch):
    """Star 初始化后 adapter._active_platforms 含已启用的 platform。"""
    from astrbot_plugin_botapi.main import BotApiStar
    from astrbot_plugin_botapi.runtime import runtime
    from types import SimpleNamespace

    class FakeCM:
        pass

    ctx = SimpleNamespace(conversation_manager=FakeCM(),
                          message_history_manager=SimpleNamespace())
    star = BotApiStar(ctx, {"host": "0.0.0.0", "port": 9001, "botapi_bindings": {}})
    rt = runtime()
    a = _adapter(monkeypatch)
    rt.adapter = a
    star.sync_active_platforms({"aiocqhttp_main", "telegram_x"})
    assert a._active_platforms == {"aiocqhttp_main", "telegram_x"}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_singleton.py -v`
Expected: FAIL（`sync_active_platforms` 不存在）

- [ ] **Step 3: 实现最小代码**

`main.py` `BotApiStar`：

```python
    def sync_active_platforms(self, platform_ids):
        """注入活跃平台集合到 adapter（供 binding_platform_for 校验）。"""
        adapter = runtime().adapter
        if adapter is not None:
            adapter._active_platforms = set(platform_ids)
```

（启动时机：`sync_active_platforms` 由 Star 提供，供外部在 `platform_manager.initialize()` 后调用——
因 `plugin_manager.reload()` 先于 `platform_manager.initialize()`，Star 初始化时 adapter 尚为 None，
故用延迟注入（如 `_do_stats` 首次请求时拉活跃平台，或 Star 提供异步钩子由核心调用）。）

`adapter.py` 模块级单实例保证：

```python
# 模块级：一个 AstrBot 只允许一个 botapi 服务器
import threading
_server_lock = threading.Lock()
_SERVER_STARTED = False
```

`adapter.run()`：

```python
    def run(self):
        """host/port 来自插件配置；模块级锁保证即使多个 botapi platform 条目也只起一个服务器。"""
        global _SERVER_STARTED
        with _server_lock:
            if _SERVER_STARTED:
                return self._shutdown.wait()   # 已起过：返回永不完成协程，避免重复绑定端口
            _SERVER_STARTED = True
        return self.app.run_task(host=self._host, port=self._port,
                                 shutdown_trigger=self._shutdown.wait)
```

（`terminate()` 复位 `_SERVER_STARTED`。`_host`/`_port` 在 Task 1 的 `__init__` 里从插件配置读取。）

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_singleton.py tests/test_binding_storage.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_singleton.py main.py adapter.py
git commit -m "feat(server): 活跃平台注入 + 单实例 Quart 模块级锁"
```

---

### Task 6: send_by_session 反向解析 botapi_ 前缀

**Files:**
- Modify: `astrbot_plugin_botapi/adapter.py`
- Test: `astrbot_plugin_botapi/tests/test_binding_proactive.py`（新建）

**Interfaces:**
- Consumes: 既有 `send_by_session`
- Produces: `send_by_session` 识别 `botapi_{token}[:{sid}]` session_id 并投 botapi scoped SSE

- [ ] **Step 1: 写失败测试**

```python
# tests/test_binding_proactive.py
import asyncio
from types import SimpleNamespace
import pytest


def _adapter(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": [], "nicknames": {}, "sessions": {}}
    a.cfg = SimpleNamespace(tokens=[], nicknames={}, sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    a._serializer = SimpleNamespace()
    a._media_enabled = True
    a._put = lambda q, evt: q.put_nowait(evt)
    return a


@pytest.mark.asyncio
async def test_send_by_session_reverse_botapi_prefix(monkeypatch):
    """绑定平台 UMO 的主动消息：session_id=botapi_tok → 投到 tok 分区。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi.models import SSEEvent
    a = _adapter(monkeypatch)
    q = asyncio.Queue(maxsize=10)
    a._sse_clients["tok"] = [q]
    a._serializer.serialize_chain = lambda chain, evt: {"type": "text",
        "content": "proactive", "timestamp": 0}
    # 模拟绑定平台 UMO 的 MessageSession：第三段 = botapi_tok
    session = SimpleNamespace(session_id="botapi_tok")
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Plain
    # 需 patch Metric.upload（super().send_by_session 会 create_task）
    import astrbot.core.platform.platform as _pmod
    monkeypatch.setattr(_pmod.Metric, "upload", staticmethod(lambda **k: None))
    await a.send_by_session(session, MessageChain([Plain("hi")]))
    ev = await q.get()
    assert ev.data["content"] == "proactive"


@pytest.mark.asyncio
async def test_send_by_session_scoped_botapi_prefix(monkeypatch):
    a = _adapter(monkeypatch)
    q = asyncio.Queue(maxsize=10)
    a._sse_clients["tok:abc"] = [q]
    a._serializer.serialize_chain = lambda chain, evt: {"type": "text",
        "content": "p", "timestamp": 0}
    session = SimpleNamespace(session_id="botapi_tok:abc")
    import astrbot.core.platform.platform as _pmod
    monkeypatch.setattr(_pmod.Metric, "upload", staticmethod(lambda **k: None))
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Plain
    await a.send_by_session(session, MessageChain([Plain("hi")]))
    ev = await q.get()
    assert ev.data["session_id"] == "abc"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_binding_proactive.py -v`
Expected: FAIL（`send_by_session` 不识别 `botapi_` 前缀）

- [ ] **Step 3: 实现最小代码**

`adapter.py` `send_by_session` 开头加反向解析：

```python
    async def send_by_session(self, session, message_chain) -> None:
        await super().send_by_session(session, message_chain)
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
        ...  # 其余逻辑不变（投 scoped SSE）
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest tests/test_binding_proactive.py tests/test_sessions_sse.py tests/test_adapter_core.py -v`
Expected: PASS（既有测试不受影响——非 botapi_ 前缀走原逻辑）

- [ ] **Step 5: 提交**

```bash
git add tests/test_binding_proactive.py adapter.py
git commit -m "feat(server): send_by_session 反向解析 botapi_ 前缀主动消息"
```

---

### Task 7: Web 管理页绑定 UI + 全量回归 + 版本

**Files:**
- Modify: `astrbot_plugin_botapi/pages/dashboard/app.js`
- Modify: `astrbot_plugin_botapi/metadata.yaml`
- Modify: `astrbot_plugin_botapi/CHANGELOG.md`

**Interfaces:**
- Consumes: Task 2 的 bind/unbind 路由；Task 5 的活跃平台列表
- Produces: 账户列表「绑定机器人」下拉；版本 → v3.0.0

- [ ] **Step 1: 阅读现有前端**

Read: `pages/dashboard/app.js`（`renderAccounts`、`wireDelegation`、`deleteAccount`）

- [ ] **Step 2: 实现绑定 UI**

`app.js`：
- `renderAccounts` 账户行增加「绑定」按钮（`data-action="bind"`），`openBind(hash)` 弹下拉选平台（从 `bridge.apiGet("stats")` 或新增 `GET platforms` 拿活跃平台列表）。
- `bindToken(hash, platform_id)` → `bridge.apiPost("accounts/<hash>/bind", {platform_id})`；解绑 → `unbind`。
- 展示当前绑定（账户数据里加 `bound_platform` 字段）。

`main.py` 需暴露活跃平台列表给前端：新增 `GET astrbot_plugin_botapi/platforms` 返回 `{"platforms": ["aiocqhttp_main", ...]}`。

- [ ] **Step 3: 全量回归**

Run: `cd /home/zzt/workspace/astrbot-app-bot/astrbot_plugin_botapi && python -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 4: 版本 bump**

`metadata.yaml` version → `3.0.0`。`CHANGELOG.md` 增补条目。

- [ ] **Step 5: 提交**

```bash
git add pages/dashboard/app.js main.py metadata.yaml CHANGELOG.md
git commit -m "feat(server): Web 绑定 UI + 版本 bump 3.0.0"
```

---

## Self-Review

### Spec 覆盖核对

| Spec 要求 | 对应 Task |
|---|---|
| 端口移到插件配置页 | Task 1（_conf_schema.json + Star 读配置起 Quart） |
| 单实例 botapi | Task 1（adapter 读插件配置 host/port）+ Task 5（模块级锁） |
| token→platform 绑定表（插件配置页维护） | Task 2（binding_platform_for + bind/unbind 路由） |
| 绑定后 UMO 复用绑定平台 | Task 3（submit_inbound 设置 UMO） |
| 历史/清空/统计读绑定平台 conversation | Task 4 |
| send_by_session 反向解析 botapi_ 前缀 | Task 6 |
| Web 管理页绑定 UI | Task 7 |
| 未绑定回退 botapi UMO | Task 3（bound 为 None 时保持原样） |
| 绑定平台不存在/停用回退 | Task 2（binding_platform_for 校验 _active_platforms） |
| 不改 AstrBot 核心 / App 接口 | 全程 |
| 版本 → v3.0.0 | Task 7 |

### 类型/签名一致性

- `binding_platform_for(token) -> str | None` 全任务一致（Task 2 定义，Task 3/4/5 使用）。
- `submit_inbound` 绑定 UMO 格式 `{pid}:FriendMessage:botapi_{scoped_key}` 一致。
- `send_by_session` 反向解析 `botapi_` 前缀逻辑 Task 6 定义，与既有非前缀逻辑并存。
- Quart 启动由 adapter 读插件配置 + run() 模块级锁保证单实例（Task 1 定 host/port 读取，Task 5 定单服务器锁）。

### 占位符扫描

所有步骤含具体代码/命令；测试为可运行骨架。Task 1/5 的 Quart 启动时机依赖 Star 与 adapter 加载顺序（AstrBot 内部），标注了"实现时核对"并给了兜底方案（adapter 就绪后触发 / 首次请求触发）——这是明确的实现指引，非 TBD。
