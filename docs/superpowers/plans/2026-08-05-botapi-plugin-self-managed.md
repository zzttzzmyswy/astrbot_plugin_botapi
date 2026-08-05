# botapi v3.0.3 纯插件自管 + 绑定回归插件配置 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 botapi 插件 enable 即自起服务器（不再依赖 botapi 平台条目），绑定关系回归插件配置 bindings 表（后台 WebUI 维护），职责边界为「插件做 token→平台，AstrBot 做平台→abconf」。

**Architecture:** `BotApiAdapter` 去掉 `@register_platform_adapter`/`Platform` 继承，改为普通类由 `BotApiStar`（类级 `initialize`/`terminate`）自建+自起 Quart 服务器。绑定存插件配置 `bindings` 列表（一对一 token→platform_id），`binding_platform_for` 查表 + 活跃校验。Web API 恢复 `/platforms` `/bind` `/unbind`，前端恢复绑定 UI。

**Tech Stack:** Python, AstrBot Star/Context/event_queue, Quart, pytest, 原生 JS 前端

## Global Constraints

- 账户数据（tokens/bindings/sessions）必须存插件配置 `astrbot_plugin_botapi_config.json`（全局），不得存 botapi 平台条目（`update_bot` 整体覆盖会丢）。
- 动态键映射（bindings/sessions）用 list 包裹（`check_config_integrity` 剔 dict 动态键但保留 list 元素）。
- `AstrMessageEvent.session_id` 必须传裸 scoped key（`{token}` / `{token}:{sid}`），AstrBot 自己拼 `{pid}:FriendMessage:` 前缀（双重前缀 bug 教训）。
- 绑定后 UMO = `{bound}:FriendMessage:botapi_{scoped}`；未绑定回退 `botapi:FriendMessage:{scoped}`。
- 绑定后 conversation 读 `bound_conversation_umo`（`{bound}:FriendMessage:botapi_{scoped}`），history/clear/stats 一致。
- `_active_platforms` 非空 → 目标平台须在集合内；为空（重启后平台注入前）→ 回退该条目 `enable=True`。
- `adapter.cfg.tokens` 是 auth 运行时缓存；写操作后须同步（`_persist_tokens`）。
- 测试纪律：任何读写插件配置的测试必须加 `_conf` fixture（`pc.reset_plugin_conf()` + monkeypatch `get_astrbot_config_path` → tmp_path + 预写空配置 `{host,port,tokens:[],bindings:[],sessions:[]}`），teardown 再 reset。
- `Star.initialize()`/`terminate()` 被 AstrBot 以**类级静态调用**（`metadata.star_cls.initialize()`，star_manager.py:1418/2041），必须实现为 `@classmethod`，服务器 task 存类级状态。
- 版本 v3.0.3；metadata.yaml + README + CHANGELOG 同步。
- 绑定目标必须是活跃平台（`_active_platforms`），旧 v3.0.2 平台 tokens 数据**不迁移**（后台重建）。

---

### Task 1: plugin_conf 恢复 bindings 读写

**Files:**
- Modify: `plugin_conf.py`（在 `set_tokens` 后加 get_bindings/set_bindings）
- Modify: `_conf_schema.json`（恢复 bindings 键声明）
- Test: `tests/test_plugin_conf.py`

**Interfaces:**
- Consumes: 无（独立于 adapter）
- Produces: `plugin_conf.get_bindings() -> list`、`plugin_conf.set_bindings(list)` —— 供 Task 3/4 用

- [ ] **Step 1: 写失败测试**（`test_plugin_conf.py` 追加）

```python
def test_bindings_roundtrip_persists(tmp_path):
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_bindings([{"token": "t1", "platform_id": "aiocqhttp_main"}])
    pc.save()
    pc.reset_plugin_conf()
    assert pc.get_bindings() == [{"token": "t1", "platform_id": "aiocqhttp_main"}]


def test_bindings_default_empty():
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == []


def test_schema_declares_bindings():
    import json as _json
    from pathlib import Path
    schema = _json.loads(
        (Path(__file__).resolve().parent.parent / "_conf_schema.json").read_text(encoding="utf-8-sig")
    )
    assert schema["bindings"]["type"] == "list"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_plugin_conf.py -q`
Expected: 3 个新测试失败（`get_bindings` not defined / schema 无 bindings / `bindings` not in schema 断言失败——见 Step 4 改旧断言）

- [ ] **Step 3: 修改 `_conf_schema.json`** 在 `tokens` 后加：

```json
  "bindings": {
    "description": "账户→机器人绑定（[{token, platform_id}]，一对一）",
    "type": "list",
    "default": []
  },
```

- [ ] **Step 4: 改 `test_plugin_conf.py` 旧断言**（`test_schema_declares_account_keys` 第 71 行 `assert "bindings" not in schema` 改为 `assert "bindings" in schema`，并在循环 key 元组加 `"bindings"`）

```python
    for key in ("host", "port", "tokens", "bindings", "sessions"):
        assert key in schema, f"schema 缺 {key}"
    assert schema["bindings"]["type"] == "list"
```

- [ ] **Step 5: 实现 `plugin_conf.py`**（在 `set_tokens` 后）

```python
def get_bindings():
    return list(load_plugin_conf().get("bindings") or [])


def set_bindings(bindings):
    load_plugin_conf()["bindings"] = list(bindings)
```

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/test_plugin_conf.py -q`
Expected: 全部通过

- [ ] **Step 7: 提交**

```bash
git add plugin_conf.py _conf_schema.json tests/test_plugin_conf.py
git commit -m "feat(conf): 插件配置恢复 bindings 表读写（token→平台一对一）"
```

---

### Task 2: adapter 重构为纯插件自管（去 Platform 继承 + 去单实例锁）

**Files:**
- Modify: `adapter.py`（文件头 imports、`__init__`、删 `_server_lock`/`_SERVER_STARTED`/`_server_owner`、`run()`、`terminate()`）
- Test: `tests/test_adapter_init.py`、`tests/test_singleton.py`、`tests/test_setup_canary.py`

**Interfaces:**
- Consumes: 无
- Produces: `BotApiAdapter.__init__(host, port, event_queue)`（新签名）；`adapter.run()` 直接 `app.run_task`（无锁）；`adapter.shutdown()`（设 _shutdown + 复位 runtime().adapter=None）；`adapter.platform_id == "botapi"` 常量；`adapter.client_self_id`、`adapter.meta()`、`adapter.commit_event()` 自实现

- [ ] **Step 1: 写失败测试**（新建 `tests/test_adapter_init.py` 替换现有内容，先只写断言新签名）

```python
import asyncio

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """重置插件配置单例 → tmp_path，预写空配置。"""
    import json, os
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [], "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


@pytest.mark.asyncio
async def test_init_new_signature(tmp_path, monkeypatch):
    """__init__ 新签名 (host, port, event_queue)，不再接受 platform_config。"""
    monkeypatch.setattr(
        "astrbot_plugin_botapi.adapter.astrbot_config",
        {"data_path": str(tmp_path), "callback_api_base": ""},
    )
    adapter = BotApiAdapter("0.0.0.0", 9000, asyncio.Queue())
    assert adapter.platform_id == "botapi"
    assert adapter.client_self_id          # 自生成
    assert adapter.meta().id == "botapi"
    assert adapter.cfg.tokens == []
    assert adapter._upload_dir.exists()


@pytest.mark.asyncio
async def test_commit_event_puts_to_queue():
    from astrbot_plugin_botapi.models import SSEEvent
    q = asyncio.Queue(maxsize=10)
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter._event_queue = q
    evt = SSEEvent("message", {"x": 1})
    adapter.commit_event(evt)
    assert await q.get() is evt
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_adapter_init.py tests/test_setup_canary.py -q`
Expected: FAIL —— `BotApiAdapter.__init__` 仍要 3 个位置参数 platform_config/platform_settings/event_queue，新调用抛 TypeError；`test_setup_canary` 因去掉 import 相关变化（见 Step 4）需调整

- [ ] **Step 3: 重构 `adapter.py`**

替换 imports（第 11-13 行）：

```python
from astrbot.api.platform import (PlatformMetadata, AstrBotMessage,
    MessageMember, MessageType)
from astrbot.api.event import MessageChain
from astrbot.core import astrbot_config
```

删除第 23-29 行模块级锁（`_server_lock`/`_SERVER_STARTED`/`_server_owner`）、第 32-48 行 `_read_raw_bindings`、装饰器 `@register_platform_adapter(...)`（第 52-62 行）。

类定义改为：

```python
class BotApiAdapter:
    def __init__(self, host: str, port: int, event_queue: asyncio.Queue) -> None:
        self._event_queue = event_queue
        self.platform_id = "botapi"
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
        self._shutdown = asyncio.Event()
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
```

`meta()` 改（去掉 `self.config.get("id")`）：

```python
    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="botapi",
            description="BotAPI 自定义移动端适配器",
            id="botapi",
            adapter_display_name="BotAPI 移动端",
            support_streaming_message=True,
            support_proactive_message=True,
        )
```

`commit_event` 自实现（删 `_save_platforms` 依赖后放类内）：

```python
    def commit_event(self, event) -> None:
        self._event_queue.put_nowait(event)
```

`run()` 去掉锁，直接：

```python
    def run(self):
        return self.app.run_task(host=self._host, port=self._port,
                                 shutdown_trigger=self._shutdown.wait)
```

`terminate()` 改 `shutdown()`（去掉全局锁操作）：

```python
    async def shutdown(self) -> None:
        self._shutdown.set()
        runtime().adapter = None
        for token, queues in list(self._sse_clients.items()):
            for q in queues:
                self._put(q, None)
```

删除 `_legacy_port()`、`_read_raw_bindings()`、`_save_platforms()`。

- [ ] **Step 4: 更新 `tests/test_setup_canary.py`**

删除 `from astrbot.api.platform import Platform, register_platform_adapter` 与 `assert Platform is not None`（Platform 不再被 adapter 引用；保留 MessageChain/PlatformStatus import）：

```python
# tests/test_setup_canary.py
def test_astrbot_importable():
    import astrbot
    from astrbot.api.event import MessageChain
    from astrbot.core.platform.platform import PlatformStatus
    assert MessageChain is not None
```

- [ ] **Step 5: 更新 `tests/test_singleton.py`**

删除模块级 `_reset_server_started`/`_reset_server_owner` fixtures 与 `_legacy_port`/单实例锁相关测试。`_adapter` 辅助改新签名：

```python
def _adapter(monkeypatch, plugin_conf):
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    conf_dir = plugin_conf["conf_dir"]
    conf_path = os.path.join(conf_dir, "astrbot_plugin_botapi_config.json")
    os.makedirs(conf_dir, exist_ok=True)
    with open(conf_path, "w", encoding="utf-8-sig") as f:
        json.dump(plugin_conf["conf"], f, ensure_ascii=False)
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: conf_dir)
    a = BotApiAdapter("0.0.0.0", 9000, asyncio.Queue())
    return a, conf_path
```

更新 `test_init_reads_host_port_from_plugin_config`：断言 `a._host == "0.0.0.0"`、`a._port == 9000`。删除 `test_legacy_port_*`、`test_run_*_lock`、`test_run_single_server_module_lock`、`test_terminate_*`、`test_star_injects_active_platforms`（移到 Task 6 生命周期测试）、`test_init_migrates_platform_tokens_then_populates_auth_cache`（迁移测试移到 Task 5）。保留 `test_conf_schema_declares_host_port`、`test_init_plugin_config_overrides_platform_config`（改新签名传 host/port 仍走 get_host/get_port）、`test_init_missing_plugin_config_file_uses_defaults`、`test_run_uses_plugin_config_host_port`、`test_run_returns_coroutine_without_binding`、`test_init_non_numeric_legacy_port_does_not_crash`（删，无 _legacy_port）。

- [ ] **Step 6: 运行全部测试确认通过**

Run: `pytest tests/test_adapter_init.py tests/test_singleton.py tests/test_adapter_core.py tests/test_adapter_lifecycle.py tests/test_setup_canary.py -q`
Expected: 通过（test_adapter_core/lifecycle 用 `object.__new__` 不再需要 abstractmethods hack，见 Step 7 清理）

- [ ] **Step 7: 清理 `test_adapter_core.py`/`test_adapter_lifecycle.py` 的 abstractmethods hack**

`test_adapter_core.py` 的 `_make_adapter()` 与 `test_adapter_lifecycle.py` 的 `_make_adapter()` 中删除 `__abstractmethods__` 清空逻辑，直接 `BotApiAdapter.__new__(BotApiAdapter)`：

```python
def _make_adapter():
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    ...
```

- [ ] **Step 8: 运行全部测试确认无回归**

Run: `pytest tests/ -q`
Expected: 通过（此时绑定相关测试因 Task 3 未做可能失败，见 Task 3 Step 2 说明）

- [ ] **Step 9: 提交**

```bash
git add adapter.py tests/test_adapter_init.py tests/test_singleton.py tests/test_adapter_core.py tests/test_adapter_lifecycle.py tests/test_setup_canary.py
git commit -m "refactor(server): adapter 纯插件自管 — 去 Platform 继承/注册与单实例锁，新签名 (host, port, event_queue)"
```

---

### Task 3: bindings 表语义的 binding_platform_for/bind_token/unbind_token

**Files:**
- Modify: `adapter.py`（绑定段）
- Test: `tests/test_binding_storage.py`

**Interfaces:**
- Consumes: `plugin_conf.get_bindings/set_bindings`（Task 1）
- Produces: `adapter.binding_platform_for(token) -> str|None`（查 bindings 表 + 活跃校验）、`adapter.bind_token(token, platform_id)`、`adapter.unbind_token(token)`（清 bindings 条目）

- [ ] **Step 1: 重写 `tests/test_binding_storage.py` 为 bindings 表语义**

替换 `_adapter` 辅助：不再 monkeypatch `astrbot_config` 平台 tokens，改用 `pc.set_bindings` 预置 bindings；`a._active_platforms` 控制活跃集。

```python
def _adapter(monkeypatch, bindings, active=None, platforms=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a.cfg = SimpleNamespace(tokens=list(pc.get_tokens()))
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = set(active if active is not None else {"aiocqhttp_main"})
    pc.set_bindings(list(bindings))
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "astrbot_config",
                        {"platform": platforms if platforms is not None else
                         [{"id": "aiocqhttp_main", "enable": True}]})
    return a
```

测试用例：

```python
def test_binding_platform_for_returns_platform(monkeypatch):
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}])
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_unbound_returns_none(monkeypatch):
    a = _adapter(monkeypatch, [{"token": "tok2", "platform_id": "aiocqhttp_main"}])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_inactive_returns_none(monkeypatch):
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}],
                 active={"telegram_x"})
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_active_empty_fallback_enabled(monkeypatch):
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}],
                 active=set(),
                 platforms=[{"id": "aiocqhttp_main", "enable": True}])
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_active_empty_fallback_disabled(monkeypatch):
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}],
                 active=set(),
                 platforms=[{"id": "aiocqhttp_main", "enable": False}])
    assert a.binding_platform_for("tok1") is None


def test_bind_token_one_to_one_switches_platform(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}])
    a.bind_token("tok1", "aiocqhttp_backup")
    binds = pc.get_bindings()
    assert len(binds) == 1                      # 一对一：旧条目清除
    assert binds[0] == {"token": "tok1", "platform_id": "aiocqhttp_backup"}


def test_unbind_token_removes_entry(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"},
                               {"token": "tok2", "platform_id": "aiocqhttp_backup"}])
    a.unbind_token("tok1")
    binds = pc.get_bindings()
    assert all(b.get("token") != "tok1" for b in binds)
    assert len(binds) == 1


def test_unbind_token_no_change_no_save(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}])
    orig = list(pc.get_bindings())
    a.unbind_token("tok_absent")
    assert pc.get_bindings() == orig          # 无变更不落盘
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_binding_storage.py -q`
Expected: 失败（`binding_platform_for` 仍扫描平台 tokens 而非 bindings；`bind_token` 不存在）

- [ ] **Step 3: 实现 `adapter.py` 绑定段**（替换现有 token→platform 绑定段）

```python
    # ── token→platform 绑定（多机器人）──
    # 绑定存插件配置 bindings 列表（[{token, platform_id}]，一对一）。全局数据不存
    # 平台条目（update_bot 会整体覆盖导致丢失）。插件做 token→平台索引，
    # 平台→abconf 路由交给 AstrBot（用户在配置文件管理页配 umop_config_routing）。

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
        """绑定 token → 目标平台：先移除旧条目（一对一），再追加。botapi 类型目标忽略。"""
        from .plugin_conf import get_bindings, set_bindings, save
        for p in (astrbot_config.get("platform") or []):
            if p.get("id") == platform_id and p.get("type") == "botapi":
                return
        binds = [b for b in get_bindings() if b.get("token") != token]
        binds.append({"token": token, "platform_id": platform_id})
        set_bindings(binds)
        save()
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_binding_storage.py -q`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add adapter.py tests/test_binding_storage.py
git commit -m "feat(server): binding_platform_for 查插件配置 bindings 表（一对一），bind/unbind 写 bindings"
```

---

### Task 4: main.py 恢复 bind/platforms/unbind Web API + 绑定逻辑

**Files:**
- Modify: `main.py`（注册 Web API、`_do_platforms`/`_do_bind`/`_do_unbind`、`_bind`/`_unbind`/`_platforms` handlers）
- Test: `tests/test_binding_handlers.py`（恢复）、`tests/test_admin_routing.py`、`tests/test_star.py`

**Interfaces:**
- Consumes: `adapter.bind_token/unbind_token`（Task 3）
- Produces: Web API `/platforms` GET、`/accounts/<hash>/bind` POST、`/accounts/<hash>/unbind` POST

- [ ] **Step 1: 写失败测试**（新建 `tests/test_binding_handlers.py`）

```python
# tests/test_binding_handlers.py — bind/unbind/platforms Web API（纯插件自管后绑定回归插件配置）
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar
from astrbot_plugin_botapi.runtime import runtime as _get_runtime


@pytest.fixture(autouse=True)
def _cleanup_runtime():
    rt = _get_runtime()
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None
    yield
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [],
                   "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def _fake_context():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()

        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, handler, methods, desc))

    return FakeContext(), registered


def _make_star(monkeypatch, tokens=None, active=None):
    """真实 BotApiAdapter（免 __init__）+ 插件配置注入 tokens/bindings。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    from astrbot_plugin_botapi import runtime as rt_mod
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a.cfg = SimpleNamespace(tokens=list(tokens or []))
    a._sse_clients = {}
    a._disabled_tokens = set()
    a._last_active = {}
    a._active_platforms = set(active if active is not None else {"aiocqhttp_main"})
    pc.set_tokens(list(tokens or []))
    pc.save()
    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    rt_mod.runtime().adapter = a
    return star, a, registered


@pytest.mark.asyncio
async def test_bind_writes_bindings(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"], active={"aiocqhttp_main"})
    res = await star._do_bind(_hash("tok"), "aiocqhttp_main")
    assert res["status"] == "ok"
    assert pc.get_bindings() == [{"token": "tok", "platform_id": "aiocqhttp_main"}]


@pytest.mark.asyncio
async def test_bind_rejects_inactive_platform(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"], active={"telegram_x"})
    res = await star._do_bind(_hash("tok"), "aiocqhttp_main")
    assert res["status"] == "error"          # 目标不在活跃集合
    assert pc.get_bindings() == []


@pytest.mark.asyncio
async def test_unbind_clears_bindings(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_bindings([{"token": "tok", "platform_id": "aiocqhttp_main"}])
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"], active={"aiocqhttp_main"})
    res = await star._do_unbind(_hash("tok"))
    assert res["status"] == "ok"
    assert pc.get_bindings() == []


@pytest.mark.asyncio
async def test_do_platforms_lists_active(monkeypatch):
    from astrbot_plugin_botapi import runtime as rt_mod
    star, adapter, _ = _make_star(monkeypatch, tokens=[], active={"aiocqhttp_main", "telegram_x"})
    res = await star._do_platforms()
    assert res["status"] == "ok"
    assert res["data"]["platforms"] == ["aiocqhttp_main", "telegram_x"]


@pytest.mark.asyncio
async def test_delete_unbinds(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_bindings([{"token": "tok", "platform_id": "aiocqhttp_main"}])
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"], active={"aiocqhttp_main"})
    await star._do_delete(_hash("tok"))
    assert pc.get_bindings() == []
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_binding_handlers.py -q`
Expected: FAIL（`_do_bind`/`_do_unbind`/`_do_platforms` not defined；`/bind` 路由未注册）

- [ ] **Step 3: 实现 `main.py`**

注册 Web API（`__init__` 中 `_delete` 注册后加）：

```python
        context.register_web_api(
            f"/{P}/accounts/<token_hash>/bind", self._bind, ["POST"], "绑定机器人"
        )
        context.register_web_api(
            f"/{P}/accounts/<token_hash>/unbind", self._unbind, ["POST"], "解绑机器人"
        )
```

末尾加：

```python
        context.register_web_api(
            f"/{P}/platforms", self._platforms, ["GET"], "平台列表"
        )
```

`_do_platforms`（v3.0.1 版本，依赖 adapter._active_platforms）：

```python
    async def _do_platforms(self):
        """返回当前可绑定的活跃平台 id 列表（绑定下拉用）。"""
        self._refresh_active_platforms()
        rt = runtime()
        adapter = rt.adapter
        self_id = adapter.platform_id if adapter is not None else None
        platforms = []
        if adapter is not None:
            active = getattr(adapter, "_active_platforms", None)
            if active:
                platforms = [p for p in sorted(active) if p != self_id]
        return Response().ok({"platforms": platforms}).__dict__
```

`_do_bind`/`_do_unbind`（v3.0.1 版本，绑定校验活跃）：

```python
    async def _do_bind(self, token_hash, platform_id):
        """绑定 token → 目标平台（多机器人路由）。校验目标在活跃集合。"""
        self._refresh_active_platforms()
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        from .plugin_conf import get_tokens
        target = next(
            (t for t in get_tokens() if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        if not platform_id:
            return Response().error("platform_id 不能为空").__dict__
        active = getattr(adapter, "_active_platforms", None)
        if active and platform_id not in active:
            return Response().error("目标平台不在活跃集合").__dict__
        adapter.bind_token(target, platform_id)
        return Response().ok({"message": "绑定成功"}).__dict__

    async def _do_unbind(self, token_hash):
        """解绑 token（恢复默认单机路由）。"""
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        from .plugin_conf import get_tokens
        target = next(
            (t for t in get_tokens() if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        adapter.unbind_token(target)
        return Response().ok({"message": "已解绑"}).__dict__
```

handlers：

```python
    async def _platforms(self):
        return await self._do_platforms()

    async def _bind(self, token_hash):
        data = await request.get_json()
        platform_id = (data or {}).get("platform_id", "")
        return await self._do_bind(token_hash, platform_id)

    async def _unbind(self, token_hash):
        return await self._do_unbind(token_hash)
```

- [ ] **Step 4: 更新 `tests/test_star.py` / `tests/test_admin_routing.py` 路由断言**

`test_star.py` 的 registered 集合加：

```python
    assert "/astrbot_plugin_botapi/accounts/<token_hash>/bind" in routes
    assert "/astrbot_plugin_botapi/accounts/<token_hash>/unbind" in routes
    assert "/astrbot_plugin_botapi/platforms" in routes
```

`test_admin_routing.py` 的 `assert len(registered) >= 7` 改 `>= 10`。

- [ ] **Step 5: 运行全部测试确认通过**

Run: `pytest tests/test_binding_handlers.py tests/test_star.py tests/test_admin_routing.py tests/test_admin_handlers.py -q`
Expected: 通过

- [ ] **Step 6: 提交**

```bash
git add main.py tests/test_binding_handlers.py tests/test_star.py tests/test_admin_routing.py
git commit -m "feat(web): 恢复 bind/unbind/platforms Web API（绑定校验活跃平台）"
```

---

### Task 5: 迁移精简（账户收敛 + 清理旧键，删 bindings→平台展开）

**Files:**
- Modify: `adapter.py`（`_migrate_accounts` 精简）
- Test: `tests/test_migration.py`（重写）

**Interfaces:**
- Consumes: `plugin_conf.get_tokens/set_tokens/save`、`astrbot_config`
- Produces: `adapter._migrate_accounts()`（只做账户收敛 + 清理旧键，幂等）

- [ ] **Step 1: 重写 `tests/test_migration.py`**

删除 bindings→平台 tokens 迁移测试（`test_migrate_bindings_to_platform_tokens`/`test_migrate_bindings_merge_dedup`/`test_migrate_legacy_botapi_bindings_dict_to_platform_tokens`/`test_migrate_skips_botapi_type_target`）。保留/更新账户收敛测试：

```python
def test_migrate_platform_tokens_to_plugin(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1", "t2"], "enable": True},
    ], {"id": "botapi"})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_tokens() == ["t1", "t2"]
    assert "tokens" not in fake.platforms[0]
    assert fake.saved is True


def test_migrate_plugin_tokens_already_present_is_noop(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"}, plugin_conf={"host": "0.0.0.0", "port": 9000, "tokens": ["t1"], "sessions": []})
    a._migrate_accounts()
    assert fake.platforms[0].get("tokens") == ["t1"]
    assert fake.saved is None


def test_migrate_cleans_legacy_keys(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True,
         "botapi_bindings": {"t1": "aiocqhttp_main"}, "nicknames": {"t1": "x"},
         "host": "0.0.0.0", "port": 9000, "sessions": {"t1": []}},
    ], {"id": "botapi", "botapi_bindings": {"t1": "aiocqhttp_main"}})
    a._migrate_accounts()
    for key in ("tokens", "botapi_bindings", "nicknames", "host", "port", "sessions"):
        assert key not in fake.platforms[0], f"botapi 条目残留 {key}"
    assert fake.saved is True


def test_migrate_keeps_bindings_intact(monkeypatch):
    """bindings 表已存在 → 迁移不动它（后台重建策略，不迁移平台 tokens）。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "aiocqhttp_main", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"}, plugin_conf={"host": "0.0.0.0", "port": 9000,
                                       "tokens": ["t1"], "bindings": [{"token": "t1", "platform_id": "aiocqhttp_main"}],
                                       "sessions": []})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == [{"token": "t1", "platform_id": "aiocqhttp_main"}]
    assert fake.platforms[0].get("tokens") == ["t1"]   # 平台 tokens 不迁移/不删
```

`_adapter` 辅助更新：删除 `a._legacy_bindings` 相关（Task 2 已删 `_read_raw_bindings`），改新 adapter 构造：

```python
def _adapter(monkeypatch, platforms, self_config, plugin_conf=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    import astrbot_plugin_botapi.adapter as adapter_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.config = dict(self_config)
    a.platform_id = "botapi"
    if plugin_conf is not None:
        import json as _json
        import astrbot.core.utils.astrbot_path as astrbot_path_mod
        conf_path = os.path.join(astrbot_path_mod.get_astrbot_config_path(),
                                 "astrbot_plugin_botapi_config.json")
        with open(conf_path, "w", encoding="utf-8") as f:
            _json.dump(plugin_conf, f)
        pc.reset_plugin_conf()
    fake = _FakeCfg(platforms)
    monkeypatch.setattr(adapter_mod, "astrbot_config", fake)
    return a, fake
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_migration.py -q`
Expected: FAIL（`_migrate_accounts` 仍做 bindings→平台展开，保留旧测试语义）

- [ ] **Step 3: 精简 `adapter.py` `_migrate_accounts`**

删除 v3.0.2 的 bindings→平台 tokens 展开（第 156-175 行的 `binds`/`legacy_binds`/`to_add` 逻辑）、`load_plugin_conf()` 里 `if "bindings" in conf` 删除键逻辑。保留：

```python
    def _migrate_accounts(self):
        """把平台条目残留的账户数据收敛进插件配置（全局）。

        1. 插件 tokens 为空且 botapi 平台条目 tokens 非空 → 迁到插件（剥离条目 tokens）。
        2. 清理 botapi 平台条目的 botapi_bindings/nicknames/host/port/sessions。
        幂等：插件 tokens 非空即视为已迁移，跳过 1（2 仍执行，无键即 no-op）；
        已迁移的 botapi 条目 tokens 保留不动（只经 1 剥离一次）。
        """
        from .plugin_conf import get_tokens, set_tokens, save
        try:
            platforms = astrbot_config.get("platform")
            if not isinstance(platforms, list):
                return
            changed = False
            plugin_tokens = get_tokens()
            if not plugin_tokens:
                # 1. botapi 平台条目 tokens → 插件 tokens
                for p in platforms:
                    if p.get("id") == self.config.get("id"):
                        pt = p.get("tokens") or []
                        if pt:
                            set_tokens(list(pt))
                            p.pop("tokens", None)
                            changed = True
                        break
            # 2. 清理 botapi 平台条目旧键（tokens 仅经 1 剥离，幂等场景保留）
            for p in platforms:
                if p.get("id") == self.config.get("id"):
                    for key in ("botapi_bindings", "nicknames", "host", "port", "sessions"):
                        if key in p:
                            p.pop(key, None)
                            changed = True
                    break
            for key in ("botapi_bindings", "nicknames", "host", "port", "sessions"):
                if key in self.config:
                    self.config.pop(key, None)
                    changed = True
            if changed:
                save()
                try:
                    _save = getattr(astrbot_config, "save_config", None)
                    if _save:
                        _save()
                except Exception:
                    pass
        except Exception:
            pass
```

注意：`self.config` 在新 adapter 里已不存在（Task 2 删了 platform_config）。此方法需在 Task 2 后调整——`self.config` 引用删除，改用常量 `"botapi"` 比对平台条目 id：

```python
            botapi_id = "botapi"
            if not plugin_tokens:
                for p in platforms:
                    if p.get("id") == botapi_id:
                        pt = p.get("tokens") or []
                        if pt:
                            set_tokens(list(pt))
                            p.pop("tokens", None)
                            changed = True
                        break
            # 2. 清理 botapi 平台条目旧键（tokens 仅经 1 剥离，幂等场景保留）
            for p in platforms:
                if p.get("id") == botapi_id:
                    for key in ("botapi_bindings", "nicknames", "host", "port", "sessions"):
                        if key in p:
                            p.pop(key, None)
                            changed = True
                    break
            # self.config 已不存在（新 adapter 无 platform_config），清理 self.config 旧键段删除
            if changed:
                save()
                try:
                    _save = getattr(astrbot_config, "save_config", None)
                    if _save:
                        _save()
                except Exception:
                    pass
        except Exception:
            pass
```

（Step 3 内嵌此修正；同时删掉原实现里 `for key in (...): if key in self.config` 的 self.config 清理段。）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_migration.py tests/test_singleton.py -q`
Expected: 通过

- [ ] **Step 5: 提交**

```bash
git add adapter.py tests/test_migration.py
git commit -m "refactor(server): 迁移精简 — 只账户收敛+清理旧键，删 bindings→平台展开"
```

---

### Task 6: Star 类级 initialize/terminate 自起/自停服务器

**Files:**
- Modify: `main.py`（`BotApiStar` 加 `@classmethod async initialize/terminate`）
- Test: `tests/test_lifecycle.py`（新建）、`tests/test_singleton.py`（保留 `test_star_injects_active_platforms` 移到这）

**Interfaces:**
- Consumes: `BotApiAdapter(host, port, event_queue)`（Task 2）、`context.get_event_queue()`、`runtime()`
- Produces: 类级 `_server_task`；`Star.initialize()` 建 adapter+起服务器；`Star.terminate()` 停服务器+复位 runtime

- [ ] **Step 1: 写失败测试**（新建 `tests/test_lifecycle.py`）

```python
# tests/test_lifecycle.py — Star 类级 initialize/terminate 自起/自停服务器（纯插件自管）
import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar
from astrbot_plugin_botapi.runtime import runtime as _get_runtime


@pytest.fixture(autouse=True)
def _cleanup_runtime():
    rt = _get_runtime()
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None
    yield
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None
    BotApiStar._server_task = None


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [],
                   "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _fake_context():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()
        _event_queue = asyncio.Queue()

        def register_web_api(self, route, handler, methods, desc):
            registered.append(route)

        def get_event_queue(self):
            return self._event_queue

    return FakeContext(), registered


@pytest.mark.asyncio
async def test_initialize_builds_adapter_and_starts_server(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    ctx, _ = _fake_context()
    star = BotApiStar(ctx, None)
    started = []

    def fake_run(*a, **k):
        async def _never():
            await asyncio.Event().wait()
        started.append(True)
        return _never()

    monkeypatch.setattr(BotApiAdapter, "run", fake_run)
    await BotApiStar.initialize()
    rt = _get_runtime()
    assert rt.adapter is not None          # adapter 已建
    assert rt.adapter is not ctx._event_queue is not None
    assert started == [True]               # 服务器已起
    await BotApiStar.terminate()


@pytest.mark.asyncio
async def test_initialize_idempotent(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    ctx, _ = _fake_context()
    star = BotApiStar(ctx, None)
    started = []

    def fake_run(*a, **k):
        async def _never():
            await asyncio.Event().wait()
        started.append(True)
        return _never()

    monkeypatch.setattr(BotApiAdapter, "run", fake_run)
    await BotApiStar.initialize()
    await BotApiStar.initialize()          # 二次调用不重建
    assert started == [True]
    await BotApiStar.terminate()


@pytest.mark.asyncio
async def test_terminate_stops_server_and_resets_runtime(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    ctx, _ = _fake_context()
    star = BotApiStar(ctx, None)
    cancelled = []

    class _Task:
        def cancel(self):
            cancelled.append(True)

    BotApiStar._server_task = _Task()
    await BotApiStar.terminate()
    rt = _get_runtime()
    assert rt.adapter is None              # runtime 复位
    assert cancelled == [True]             # 服务器 task 被取消
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_lifecycle.py -q`
Expected: FAIL（`BotApiStar.initialize`/`terminate` 不存在或非类方法）

- [ ] **Step 3: 实现 `main.py` `BotApiStar` 类方法**

```python
    @classmethod
    async def initialize(cls):
        """插件激活时由 AstrBot 调用（star_cls.initialize()）。自建 adapter + 自起服务器。"""
        if runtime().adapter is not None:
            return                          # 已起过（重载等）
        rt = runtime()
        from .plugin_conf import get_host, get_port
        from .adapter import BotApiAdapter
        adapter = BotApiAdapter(get_host() or "0.0.0.0", get_port() or 9000,
                                rt.context.get_event_queue())
        rt.adapter = adapter
        cls._server_task = asyncio.create_task(adapter.run())

    @classmethod
    async def terminate(cls):
        """插件禁用/重载时由 AstrBot 调用。停服务器 + 复位 runtime。"""
        adapter = runtime().adapter
        if adapter is not None:
            await adapter.shutdown()
        task = getattr(cls, "_server_task", None)
        if task is not None:
            task.cancel()
            try:
                await asyncio.gather(task, return_exceptions=True)
            except Exception:
                pass
            cls._server_task = None
```

类级属性初始化（`__init_subclass__` 前或类体）：

```python
    _server_task = None
```

注意：`initialize` 里 `rt.context` 需由 `__init__` 设置。当前 `BotApiStar.__init__` 只设 `self._plugin_conf`。需在 `__init__` 加 `rt.context = context`（把现 `__init__` 顶部的 runtime 注入逻辑移回）：

```python
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        rt = runtime()
        rt.context = context
        rt.conversation_manager = context.conversation_manager
        rt.message_history_manager = context.message_history_manager
        self._plugin_conf = config if isinstance(config, dict) else {}
        ...
```

（当前 `__init__` 已注入 conversation_manager/message_history_manager，补 `rt.context = context`。）

- [ ] **Step 4: 更新 `test_singleton.py` 的 `test_star_injects_active_platforms`**

移到 `test_lifecycle.py`（保持 `sync_active_platforms` 测试），删 `test_singleton.py` 里该测试。

- [ ] **Step 5: 运行全部测试确认通过**

Run: `pytest tests/test_lifecycle.py tests/test_singleton.py tests/test_star.py -q`
Expected: 通过

- [ ] **Step 6: 提交**

```bash
git add main.py tests/test_lifecycle.py tests/test_singleton.py
git commit -m "feat(server): Star 类级 initialize/terminate 自起/自停服务器（插件 enable 即运作）"
```

---

### Task 7: 前端恢复绑定 UI

**Files:**
- Modify: `pages/dashboard/index.html`、`pages/dashboard/app.js`、`pages/dashboard/style.css`

**Interfaces:**
- Consumes: Web API `/platforms`、`/accounts/<hash>/bind`、`/accounts/<hash>/unbind`（Task 4）
- Produces: 绑定/解绑按钮 + modal-bind 下拉

- [ ] **Step 1: `index.html` 恢复 modal-bind**（`modal-export` 后、`toast` 前）

```html
    <!-- 绑定机器人（替代 prompt，sandbox 无 allow-modals；下拉选活跃平台） -->
    <div id="modal-bind" class="modal hidden">
      <div class="modal-content">
        <h3>绑定机器人</h3>
        <p id="bind-msg" class="confirm-msg">选择该账户使用的机器人（绑定后账户对话走该平台的 LLM 配置）：</p>
        <select id="select-bind-platform" class="bind-select"></select>
        <div class="modal-actions">
          <button id="btn-bind-save" class="btn btn-primary">绑定</button>
          <button id="btn-bind-cancel" class="btn btn-secondary">取消</button>
        </div>
      </div>
    </div>
```

- [ ] **Step 2: `style.css` 恢复 `.bind-select`**（`.modal-content input` 后）

```css
.bind-select { display:block; width:100%; margin-top:6px; padding:8px 12px; border:1px solid var(--border); border-radius:8px; font-size:13px; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: `app.js` 恢复绑定/解绑按钮与逻辑**

`renderAccounts` 操作列加（`chat` 前）：

```js
        <button class="btn btn-sm btn-secondary" data-action="bind" data-hash="${esc(a.token_hash)}">绑定</button>
        ${a.bound_platform ? `<button class="btn btn-sm btn-secondary" data-action="unbind" data-hash="${esc(a.token_hash)}">解绑</button>` : ''}
```

`wireDelegation` 加分支：

```js
    else if (action === "bind") openBind(hash);
    else if (action === "unbind") await unbindAccount(hash);
```

`setupToolbar` 加绑定模态监听：

```js
  // 绑定模态
  document.getElementById("btn-bind-save").addEventListener("click", bindAccount);
  document.getElementById("btn-bind-cancel").addEventListener("click", () =>
    document.getElementById("modal-bind").classList.add("hidden"));
```

`deleteAccount` 后加绑定逻辑（v3.0.1 版本）：

```js
// ── 绑定机器人（下拉选活跃平台，页内模态替代 prompt）──

let bindTarget = { hash: "" };

async function openBind(tokenHash) {
  bindTarget = { hash: tokenHash };
  const select = document.getElementById("select-bind-platform");
  select.innerHTML = '<option value="">加载中...</option>';
  document.getElementById("modal-bind").classList.remove("hidden");
  let platforms = [];
  try {
    const res = await bridge.apiGet("platforms");
    platforms = (res.platforms && res.platforms.length) ? res.platforms : [];
  } catch (err) {
    log("load platforms ERR", err);
  }
  if (!platforms.length) {
    document.getElementById("bind-msg").textContent =
      "没有可绑定的活跃平台（需在 AstrBot 中启用其他平台）。";
  } else {
    document.getElementById("bind-msg").textContent =
      `选择「${bindTarget.hash}」使用的机器人（绑定后对话走该平台的 LLM 配置）：`;
  }
  select.innerHTML = platforms.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
}

async function bindAccount() {
  const platformId = document.getElementById("select-bind-platform").value;
  if (!platformId) { toast("没有可绑定的平台"); return; }
  try {
    await bridge.apiPost(`accounts/${bindTarget.hash}/bind`, { platform_id: platformId });
    document.getElementById("modal-bind").classList.add("hidden");
    toast(`已绑定 ${platformId}`);
    await refresh();
  } catch (err) { toast("绑定失败: " + (err?.message || err)); }
}

async function unbindAccount(tokenHash) {
  if (!(await confirmDialog(`确定解绑该账户？解绑后回到默认单机路由。`))) return;
  try {
    await bridge.apiPost(`accounts/${tokenHash}/unbind`, {});
    await refresh();
  } catch (err) { toast("解绑失败: " + (err?.message || err)); }
}
```

- [ ] **Step 4: 手动验证前端**（无单测，靠浏览器/管理页）

在 AstrBot WebUI 插件页打开管理面板，确认：账户表格显示「绑定」按钮；点绑定弹出下拉（列活跃平台）；选平台绑定后徽标显示目标平台 + 出现「解绑」；解绑恢复「未绑定」。

- [ ] **Step 5: 提交**

```bash
git add pages/dashboard/index.html pages/dashboard/app.js pages/dashboard/style.css
git commit -m "feat(dashboard): 恢复绑定/解绑 UI（下拉选活跃平台）"
```

---

### Task 8: 更新绑定相关测试的 fixture（sessions_storage / binding_history / binding_routing / binding_proactive / platforms_web）

**Files:**
- Modify: `tests/test_sessions_storage.py`、`tests/test_binding_history.py`、`tests/test_binding_routing.py`、`tests/test_binding_proactive.py`、`tests/test_platforms_web.py`

**Interfaces:**
- Consumes: 无（纯测试 fixture 适配新 adapter 构造）
- Produces: 全量测试通过

- [ ] **Step 1: `test_binding_routing.py`** `_adapter` 改 bindings 表

`_adapter` 里删 `astrbot_config` monkeypatch 平台 tokens，改 `pc.set_bindings([{"token":"tok","platform_id":"aiocqhttp_main"}])`；`test_submit_inbound_unbound_keeps_botapi_umo` 删 monkeypatch，改 `pc.set_bindings([])`；`test_submit_inbound_bound_inactive_platform_fallback_botapi` 改 `a._active_platforms = set()` + `pc.set_bindings([])` 或用 platforms enable=False：

```python
def _adapter(monkeypatch, bindings=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a.cfg = SimpleNamespace(tokens=["tok"])
    a._sse_clients = {}
    a._token_to_origin = {}
    a.client_self_id = "self"
    a._uploaded_files = {}
    a._serializer = SimpleNamespace()
    a.commit_event = lambda e: None
    a._active_platforms = {"aiocqhttp_main"}
    pc.set_bindings(list(bindings if bindings is not None else
                          [{"token": "tok", "platform_id": "aiocqhttp_main"}]))
    return a
```

- [ ] **Step 2: `test_binding_history.py`** `_adapter`/`_make_star` 改 bindings 表

`_adapter` 删平台 tokens monkeypatch，改 `pc.set_bindings([{"token":"tok","platform_id":"aiocqhttp_main"}])`。`_make_star` 的 `bound_to` 改为写 bindings + 活跃校验：

```python
    if bound_to:
        pc.set_bindings([{"token": t, "platform_id": bound_to} for t in (tokens or [])])
    def binding_platform_for(t):
        if bound_to and t in (tokens or []):
            return bound_to if bound_to in adapter._active_platforms else None
        return None
```

（绑定语义由 bindings 表驱动，`binding_platform_for` 假实现不变；平台条目不再含 tokens。）

- [ ] **Step 3: `test_sessions_storage.py`** `_adapter` 改 bindings 表

`bound` 参数改为 `pc.set_bindings([{"token":"tok","platform_id":bound}])`，删 `astrbot_config` monkeypatch：

```python
def _adapter(sessions_map=None, active=None, bound=None, monkeypatch=None, bound_enabled=True):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = set(active or ())
    if sessions_map:
        pc.set_sessions_map(sessions_map)
    if bound:
        pc.set_bindings([{"token": "tok", "platform_id": bound}])
    if monkeypatch is not None:
        import astrbot_plugin_botapi.adapter as adapter_mod
        monkeypatch.setattr(adapter_mod, "astrbot_config",
                            {"platform": [{"id": bound, "enable": bound_enabled}]})
    return a
```

`test_delete_session_bound_inactive_uses_botapi_umo` 的 `bound_enabled=False` → 用 platforms enable=False 覆盖回退路径（`binding_platform_for` 空 active + enable=False → None）。

- [ ] **Step 4: `test_binding_proactive.py`** 删 abstractmethods hack（`_adapter` 用 `BotApiAdapter.__new__`），其余不变。

- [ ] **Step 5: `test_platforms_web.py`** 重写绑定相关测试为 bindings 表语义

`_make_real_adapter` 删 `a.config`/`a.cfg` 平台 tokens 依赖（新 adapter 无 config），`binding_platform_for` 改查 bindings：

```python
def _make_real_adapter(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a.cfg = SimpleNamespace(tokens=[], sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    a._disabled_tokens = set()
    a._last_active = {}
    a._uploaded_files = {}
    a._active_platforms = set()
    a._serializer = SimpleNamespace()
    a.commit_event = lambda e: None
    return a
```

`_make_star` 的 `bound_to` 改 `pc.set_bindings([{"token": t, "platform_id": bound_to} for t in (tokens or [])])`；`test_stats_refreshes_active_platforms_for_binding` 的 `config_platforms` 不再含 aiocqhttp tokens，改 `real` adapter + `pc.set_bindings`。

- [ ] **Step 6: 运行全部测试**

Run: `pytest tests/ -q`
Expected: 全部通过（此 Task 目标是消除 v3.0.2 绑定测试残留，使全量绿）

- [ ] **Step 7: 提交**

```bash
git add tests/test_binding_routing.py tests/test_binding_history.py tests/test_sessions_storage.py tests/test_binding_proactive.py tests/test_platforms_web.py
git commit -m "test: 绑定相关 fixture 适配 bindings 表语义（去平台 tokens）"
```

---

### Task 9: README / CHANGELOG / metadata 版本更新到 v3.0.3

**Files:**
- Modify: `metadata.yaml`、`README.md`、`CHANGELOG.md`

- [ ] **Step 1: `metadata.yaml` 版本改 `3.0.3`**

- [ ] **Step 2: `CHANGELOG.md` 加 `[3.0.3]` 条目**（`[Unreleased]` 与 `[3.0.2]` 之间）

```markdown
## [3.0.3] - 2026-08-05

### Changed

- **纯插件自管**：插件 enable 即自起服务器，不再依赖 botapi 平台条目 enable；适配器不再继承
  Platform / 不注册为平台适配器，botapi 从「机器人/平台」配置页消失。
- **绑定回归插件配置**：删除平台/机器人配置的「绑定 token 列表」字段，绑定由插件后台 WebUI
  维护（bindings 表，一对一 token→平台）。插件做 token→平台索引，平台→配置文件(abconf)
  路由交给 AstrBot（在配置文件管理页配置 umop 路由）。
- **修复路由错乱（v3.0.2 回归）**：v3.0.2 把绑定承载在平台 tokens，但 AstrBot 的 LLM 配置
  只由 umop_config_routing 路由表决定，两套索引不对齐导致 tokenA 会话走到 tokenB 配置。
  现绑定由插件 bindings 表决定 UMO 覆写前缀，AstrBot 按该前缀路由到 abconf。
- 旧 v3.0.2 平台 tokens 里的绑定数据不迁移，需在插件后台重新绑定。
```

更新底部链接区：`[Unreleased]` compare → v3.0.3；加 `[3.0.3]` release link；`[3.0.2]` link 保留。

- [ ] **Step 3: `README.md` 配置部分更新**

替换「绑定」段（第 75 行附近）为：

```markdown
**绑定**：在插件后台管理页为账户选择目标机器人（平台）。绑定后该账户的会话（收发消息、历史、
清空、统计）以 `{平台}:FriendMessage:botapi_*` 前缀路由到该平台对应配置文件（abconf）的 LLM 配置；
平台→abconf 的映射在 AstrBot「配置文件」管理页配置（umop 路由）。未绑定账户走默认单机路由。
插件 enable 即启动内置服务器（host/port 在插件配置页设置），无需在「机器人/平台」配置 botapi 条目。
```

- [ ] **Step 4: 运行测试确认无回归**

Run: `pytest tests/ -q`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add metadata.yaml README.md CHANGELOG.md
git commit -m "docs: v3.0.3 — 纯插件自管 + 绑定回归插件配置"
```

---

### Task 10: 全量测试 + 自检

**Files:**
- Test: 全部 `tests/`

- [ ] **Step 1: 全量测试**

Run: `pytest tests/ -q`
Expected: 全部通过（预计 ~215-220 passed）

- [ ] **Step 2: 检查未提交改动**

Run: `git status`
Expected: 工作区干净（除未提交的 spec/plan 文档）

- [ ] **Step 3: 提交 spec/plan 文档**（若未在开发中提交）

```bash
git add docs/superpowers/specs/2026-08-05-botapi-plugin-self-managed-design.md docs/superpowers/plans/2026-08-05-botapi-plugin-self-managed.md
git commit -m "docs: v3.0.3 spec + plan"
```

- [ ] **Step 4: 汇报**（给用户：测试数、关键改动、已知边界）
