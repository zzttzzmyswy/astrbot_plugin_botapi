# botapi v3.0.4 重新注册为绑定目标 + 双模式适配器 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 botapi 重新成为平台适配器（可作绑定目标），服务器仍插件自起，修复 v3.0.3 绑定下拉找不到 botapi 的问题。

**Architecture:** `BotApiAdapter` 重新 `@register_platform_adapter` + 继承 `Platform`，恢复 3 参签名 `(platform_config, platform_settings, event_queue)`，双模式：条目实例（`_star_managed` 缺省）= 极简占位壳；插件实例（`_star_managed=True`）= 完整功能 + 服务器。迁移反转把 botapi 条目恢复 enable=True。`bind_token` 允许 botapi 类型目标。

**Tech Stack:** Python, AstrBot Star/Context/Platform/event_queue, Quart, pytest, 原生 JS 前端

## Global Constraints

- 账户数据（tokens/bindings/sessions）必须存插件配置，动态键用 list 包裹。
- `AstrMessageEvent.session_id` 传裸 scoped key；绑定后 UMO = `{bound}:FriendMessage:botapi_{scoped}`；未绑定回退 `botapi:FriendMessage:{scoped}`。
- `adapter.cfg.tokens` auth 缓存，写后同步。
- **条目实例**（`_star_managed` 缺省）：只设 `platform_id` = 条目 id + `_is_entry=True`；**不设** `runtime().adapter`、不建 Quart app、不跑 `_migrate_accounts`、不跑服务器。
- **插件实例**（`_star_managed=True`）：设 runtime().adapter、建 app、跑迁移、起服务器；`platform_id="botapi"` 常量。
- 迁移反转：所有 `type==botapi` 条目恢复 `enable=True`（反转 v3.0.3 禁用）。
- `bind_token` 允许 botapi 类型目标（删除 `type=="botapi"` 跳过）。
- `_refresh_active_platforms` 排除 `"botapi"` 常量（插件实例），保留 botapi_a/botapi_b（条目实例）。
- 重新继承 `Platform` 后须实现 `run`/`meta`（abstractmethod），使类不抽象、测试 `BotApiAdapter.__new__` 可用。
- `run()`：条目实例返回 `self._shutdown.wait()`（永不完成，PlatformManager 驻留）；插件实例 `app.run_task`。
- 测试纪律：`_conf` fixture 隔离 get_astrbot_config_path → tmp_path。
- 版本 v3.0.4；metadata.yaml + README + CHANGELOG 同步。

---

### Task 1: adapter 重新注册 + 双模式构造

**Files:**
- Modify: `adapter.py`（顶部注册、`__init__`、`meta`、`run`、`commit_event`）
- Test: `tests/test_adapter_init.py`、`tests/test_setup_canary.py`

**Interfaces:**
- Consumes: 无
- Produces: `BotApiAdapter(platform_config, platform_settings, event_queue)` 3 参签名；`self._is_entry`、`self.platform_id`；`_star_managed` 标记；条目/插件双模式

- [ ] **Step 1: 写失败测试**（`test_adapter_init.py` 替换为双模式测试）

```python
import asyncio

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi.runtime import runtime


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
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
async def test_plugin_mode_full_init(monkeypatch):
    """_star_managed=True → 插件模式：完整初始化，设 runtime().adapter，platform_id=botapi。"""
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config",
                        {"data_path": str(tmp_path := "/tmp/botapi-test"), "callback_api_base": ""})
    import os
    os.makedirs(tmp_path, exist_ok=True)
    adapter = BotApiAdapter(
        {"id": "botapi", "_star_managed": True, "host": "0.0.0.0", "port": 9000},
        {}, asyncio.Queue())
    assert adapter._is_entry is False
    assert adapter.platform_id == "botapi"
    assert runtime().adapter is adapter
    assert adapter.app is not None          # 建了 Quart
    assert adapter.cfg.tokens == []
    # cleanup
    runtime().adapter = None


@pytest.mark.asyncio
async def test_entry_mode_minimal(monkeypatch):
    """无 _star_managed → 条目模式：极简壳，不设 runtime().adapter、不建 app、platform_id=条目id。"""
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config",
                        {"data_path": "/tmp/botapi-test2", "callback_api_base": ""})
    import os
    os.makedirs("/tmp/botapi-test2", exist_ok=True)
    adapter = BotApiAdapter({"id": "botapi_a", "type": "botapi"}, {}, asyncio.Queue())
    assert adapter._is_entry is True
    assert adapter.platform_id == "botapi_a"
    assert runtime().adapter is None        # 不设 runtime
    assert not hasattr(adapter, "app")      # 不建 app
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_adapter_init.py tests/test_setup_canary.py -q`
Expected: FAIL —— 当前 `__init__(host, port, event_queue)` 3 参位置签名不匹配，或 `BotApiAdapter` 无 `_is_entry`/`_star_managed`

- [ ] **Step 3: 实现 `adapter.py`**

顶部加注册（第 1 行 import 区后）：

```python
from astrbot.api.platform import (register_platform_adapter, Platform,
    PlatformMetadata, AstrBotMessage, MessageMember, MessageType)
from astrbot.api.event import MessageChain
from astrbot.core import astrbot_config
```

类定义替换为：

```python
@register_platform_adapter(
    "botapi",
    "BotAPI 自定义移动端适配器 — 一人一 Bot 极简移动端接入，支持弱网断连恢复",
    default_config_tmpl={},   # 无 tokens 字段（账户注册表在插件配置）
    adapter_display_name="BotAPI 移动端",
    support_streaming_message=True,
)
class BotApiAdapter(Platform):
    def __init__(self, platform_config, platform_settings, event_queue):
        super().__init__(platform_config, event_queue)
        self.platform_id = platform_config.get("id", "botapi")
        self._is_entry = not platform_config.get("_star_managed")
        if self._is_entry:
            # 条目模式：极简占位壳，仅记录 id 作为绑定目标。
            return
        # 插件模式：完整初始化
        self._event_queue = event_queue
        self.client_self_id = uuid.uuid4().hex
        self.cfg = BotApiConfig(tokens=[], sessions={})
        self._token_to_origin = {}
        self._sse_clients = defaultdict(list)
        self._active_platforms = set()
        self._disabled_tokens = set()
        self._last_active = {}
        self._uploaded_files = {}
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

`meta()` 改 id 用 `self.platform_id`：

```python
    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="botapi",
            description="BotAPI 自定义移动端适配器",
            id=self.platform_id,
            adapter_display_name="BotAPI 移动端",
            support_streaming_message=True,
            support_proactive_message=True,
        )
```

`run()` 双模式：

```python
    def run(self):
        if self._is_entry:
            return self._shutdown.wait()
        return self.app.run_task(host=self._host, port=self._port,
                                 shutdown_trigger=self._shutdown.wait)
```

`commit_event`（插件模式用，`_is_entry` 时无 `_event_queue` 但不会被调）：

```python
    def commit_event(self, event) -> None:
        self._event_queue.put_nowait(event)
```

注意 `_shutdown`：条目实例也需 `self._shutdown = asyncio.Event()`（`run()` 返回 `_shutdown.wait()` 用）。在 `super().__init__` 后、`_is_entry` 分支前设置。

- [ ] **Step 4: 更新 `test_setup_canary.py`**

重新加回 Platform import 断言（去注册后又需要）：

```python
def test_astrbot_importable():
    import astrbot
    from astrbot.api.event import MessageChain
    from astrbot.core.platform.platform import PlatformStatus
    assert MessageChain is not None
```

（v3.0.3 删了 Platform import，现在 adapter 重新 import Platform，canary 保持简单——不需要加回 Platform 断言，维持现状即可。）

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_adapter_init.py tests/test_setup_canary.py tests/test_adapter_core.py tests/test_adapter_lifecycle.py -q`
Expected: 通过（test_adapter_core/lifecycle 用 `__new__`，类实现了 run/meta 不抽象）

- [ ] **Step 6: 全量确认过渡状态**

Run: `python -m pytest tests/ -q`
Expected: 部分失败可预期（`__init__` 签名变化导致 test_singleton 等用旧 host/port 签名失败；绑定/迁移测试因条目 enable 反转失败）——Task 2/3 处理。本任务目标是 adapter_init/setup_canary/core/lifecycle 绿。

- [ ] **Step 7: commit**

```bash
git add adapter.py tests/test_adapter_init.py
git commit -m "feat(server): botapi 重新注册为平台适配器 + 双模式构造（条目壳/插件全量）"
```

---

### Task 2: Star 生命周期适配 + 迁移反转

**Files:**
- Modify: `main.py`（initialize 用伪 config）、`adapter.py`（`_migrate_accounts` 反转 enable）
- Test: `tests/test_lifecycle.py`、`tests/test_migration.py`、`tests/test_singleton.py`

**Interfaces:**
- Consumes: `BotApiAdapter({"id":"botapi","_star_managed":True,...}, {}, queue)`（Task 1）
- Produces: 插件实例构造正确；迁移把 botapi 条目 enable=True

- [ ] **Step 1: 写失败测试**（`test_migration.py` 反转）

```python
def test_migrate_reenables_botapi_entries(monkeypatch):
    """v3.0.3 禁用的 botapi 条目 → 迁移恢复 enable=True（作为绑定目标）。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "enable": False},
        {"id": "botapi_a", "type": "botapi", "enable": False},
    ], {"id": "botapi"})
    a._migrate_accounts()
    assert fake.platforms[0]["enable"] is True
    assert fake.platforms[1]["enable"] is True
```

`test_migrate_cleans_legacy_keys` 的 `enable is False` 断言反转回 `True`（原来测禁用，现在测恢复）。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_migration.py -q`
Expected: FAIL（迁移仍设 enable=False）

- [ ] **Step 3: 反转 `_migrate_accounts`**（adapter.py step 2）

```python
            for p in platforms:
                if p.get("type") == "botapi":
                    for key in ("botapi_bindings", "nicknames", "host", "port", "sessions"):
                        if key in p:
                            p.pop(key, None)
                            changed = True
                    # 反转 v3.0.3：重新启用 botapi 条目（作为绑定目标）。
                    # 重新注册后 enable 不再触发 adapter not found。
                    if p.get("enable") is not True:
                        p["enable"] = True
                        changed = True
```

- [ ] **Step 4: 更新 `test_lifecycle.py`** 插件实例构造

`test_lifecycle.py` 里 `initialize` 的断言（adapter 是插件实例）——若原测试用 `BotApiAdapter.__new__` 或直接断言 runtime，需适配 `_star_managed` 构造。检查：`test_initialize_builds_adapter_and_starts_server` 的 `rt.adapter` 断言应保持，`initialize` 实现改成伪 config（见 Step 5）。

- [ ] **Step 5: 改 `main.py` initialize** 用伪 config

```python
    @classmethod
    async def initialize(cls):
        if runtime().adapter is not None:
            return
        rt = runtime()
        from .plugin_conf import get_host, get_port
        from .adapter import BotApiAdapter
        adapter = BotApiAdapter(
            {"id": "botapi", "_star_managed": True, "host": get_host() or "0.0.0.0",
             "port": get_port() or 9000},
            {}, rt.context.get_event_queue())
        rt.adapter = adapter
        cls._server_task = asyncio.create_task(adapter.run())
```

- [ ] **Step 6: 更新 `test_singleton.py`** 3 参签名

`test_singleton.py` 的 `_adapter` 辅助改用 3 参签名（platform_config 含 id/_star_managed）：

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
    a = BotApiAdapter({"id": "botapi", "_star_managed": True}, {}, asyncio.Queue())
    return a, conf_path
```

`test_init_reads_host_port_from_plugin_config` 断言 `a._host == "0.0.0.0"`、`a._port == 9000`（get_host/get_port 权威）。

- [ ] **Step 7: 运行确认通过**

Run: `python -m pytest tests/test_lifecycle.py tests/test_migration.py tests/test_singleton.py -q`
Expected: 通过

- [ ] **Step 8: 全量确认过渡状态**

Run: `python -m pytest tests/ -q`
Expected: 绑定相关测试可能仍失败（bind_token 跳过 botapi / platforms 排除）——Task 3 处理。

- [ ] **Step 9: commit**

```bash
git add main.py adapter.py tests/test_lifecycle.py tests/test_migration.py tests/test_singleton.py
git commit -m "feat(server): Star 插件实例用伪 config + 迁移反转恢复 botapi 条目 enable"
```

---

### Task 3: 绑定目标允许 botapi + 绑定测试适配

**Files:**
- Modify: `adapter.py`（`bind_token` 删 botapi 跳过）
- Test: `tests/test_binding_storage.py`、`tests/test_binding_routing.py`、`tests/test_binding_history.py`、`tests/test_platforms_web.py`

**Interfaces:**
- Consumes: `binding_platform_for`（Task 1 保留）、`_active_platforms`（含 botapi 条目 id）
- Produces: `bind_token` 允许 botapi 目标；绑定到 botapi 条目 id 生效

- [ ] **Step 1: 写失败测试**（`test_binding_storage.py`）

```python
def test_bind_token_allows_botapi_target(monkeypatch):
    """绑定到 botapi 条目（botapi_a）→ 允许（v3.0.3 禁止，现允许）。"""
    from astrbot_plugin_botapi import plugin_conf as pc
    a = _adapter(monkeypatch, [], active={"botapi_a", "aiocqhttp_main"})
    a.bind_token("tok1", "botapi_a")
    assert pc.get_bindings() == [{"token": "tok1", "platform_id": "botapi_a"}]


def test_binding_platform_for_botapi_entry(monkeypatch):
    """token 绑到 botapi_a 且活跃 → 返回 botapi_a。"""
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "botapi_a"}],
                 active={"botapi_a"})
    assert a.binding_platform_for("tok1") == "botapi_a"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_binding_storage.py -q`
Expected: FAIL（`bind_token` 仍跳过 botapi 类型）

- [ ] **Step 3: 改 `adapter.py` `bind_token`** 删 botapi 跳过

```python
    def bind_token(self, token: str, platform_id: str) -> None:
        """绑定 token → 目标平台（含 botapi 条目）：先移除旧条目（一对一），再追加。"""
        from .plugin_conf import get_bindings, set_bindings, save
        binds = [b for b in get_bindings() if b.get("token") != token]
        binds.append({"token": token, "platform_id": platform_id})
        set_bindings(binds)
        save()
```

- [ ] **Step 4: 更新绑定测试的 active 集合含 botapi 条目**

`test_binding_routing.py` 的 `_adapter` 默认 `_active_platforms = {"aiocqhttp_main"}` → 加 botapi_a 作为可绑目标（绑定到 botapi_a 的用例）或保持现状（绑定到 aiocqhttp）。新增一个绑定到 botapi_a 的用例：

```python
@pytest.mark.asyncio
async def test_submit_inbound_bound_to_botapi_entry(monkeypatch):
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch, bindings=[{"token": "tok", "platform_id": "botapi_a"}])
    a._active_platforms = {"botapi_a", "aiocqhttp_main"}
    committed = []
    async def fake_persist(key, mid, text):
        pass
    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)
    a.commit_event = lambda e: committed.append(e)
    await routes_mod.submit_inbound(a, "tok", "hi")
    assert committed[0].unified_msg_origin == "botapi_a:FriendMessage:botapi_tok"
```

- [ ] **Step 5: 更新 `test_platforms_web.py`** 绑定下拉含 botapi 条目

`_make_star` 的 `binding_platform_for` 假实现：bound_to 现在可以是 botapi_a。`test_stats_refreshes_active_platforms_for_binding` 的 `platform_insts` 含 botapi_a 实例 → `_active_platforms` 含它。检查 `_refresh_active_platforms` 排除逻辑：`if pid and pid != "botapi"`——botapi_a 不匹配常量，被保留。✅

- [ ] **Step 6: 运行全量确认通过**

Run: `python -m pytest tests/ -q`
Expected: 全部通过（绑定到 botapi 条目的新用例绿，旧用例不回归）

- [ ] **Step 7: commit**

```bash
git add adapter.py tests/test_binding_storage.py tests/test_binding_routing.py tests/test_platforms_web.py
git commit -m "feat(server): bind_token 允许 botapi 条目目标 + 绑定测试适配"
```

---

### Task 4: README / CHANGELOG / metadata 更新到 v3.0.4

**Files:**
- Modify: `metadata.yaml`、`README.md`、`CHANGELOG.md`

- [ ] **Step 1: metadata.yaml version → 3.0.4**

- [ ] **Step 2: CHANGELOG 加 `[3.0.4]` 条目**

```markdown
## [3.0.4] - 2026-08-05

### Changed

- **botapi 重新注册为平台适配器**：可作为绑定目标。用户可把账户 token 绑定到某个 botapi
  条目（或其他真实平台），UMO 前缀用该条目 id，经 AstrBot 路由表路由到对应 abconf。
- **双模式适配器**：PlatformManager 实例化的 botapi 条目为占位壳（不跑服务器），服务器仍
  由插件 enable 自起（host/port 插件配置）。
- **迁移反转**：v3.0.3 禁用的 botapi 条目自动恢复 enable（作为绑定目标）。
- **修复 v3.0.3 绑定下拉找不到 botapi**：绑定目标现含 botapi 条目 id。
```

更新链接区（[Unreleased] compare → v3.0.4、加 [3.0.4] link）。

- [ ] **Step 3: README 绑定段更新**

替换「插件 enable 即启动内置服务器，无需在机器人/平台配置 botapi 条目」为说明 botapi 条目可作绑定目标：

```markdown
**绑定**：在插件后台管理页为账户选择目标机器人（平台）。绑定目标含 botapi 条目（可建多个，
对应不同 abconf）与真实平台。绑定后会话以 `{目标id}:FriendMessage:botapi_*` 前缀路由，
AstrBot 路由表（配置文件管理页）把该前缀映射到 abconf。未绑定账户走默认路由。
插件 enable 即启动内置服务器；botapi 条目 enable 表示「可作为绑定目标」，不决定服务器。
```

- [ ] **Step 4: 运行测试确认无回归**

Run: `python -m pytest tests/ -q`
Expected: 全部通过

- [ ] **Step 5: commit**

```bash
git add metadata.yaml README.md CHANGELOG.md
git commit -m "docs: v3.0.4 — botapi 重新注册为绑定目标 + 双模式适配器"
```

---

### Task 5: 全量测试 + 自检

**Files:**
- Test: 全部 `tests/`

- [ ] **Step 1: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: 全部通过（预计 ~215-220）

- [ ] **Step 2: 检查未提交改动**

Run: `git status`
Expected: 工作区干净（除未提交 spec/plan 文档）

- [ ] **Step 3: 提交 spec/plan 文档**

```bash
git add docs/superpowers/specs/2026-08-05-botapi-re-register-bind-target-design.md docs/superpowers/plans/2026-08-05-botapi-re-register-bind-target.md
git commit -m "docs: v3.0.4 spec + plan"
```

- [ ] **Step 4: 汇报**（给用户：测试数、关键改动、已知边界）
