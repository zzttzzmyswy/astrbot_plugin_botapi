# 账户数据迁移到插件配置（全局）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 v3.0.0 升级后账户丢失事故：把账户数据（tokens/sessions/bindings）从 botapi 平台条目迁入插件配置 `astrbot_plugin_botapi_config.json`（全局），botapi 平台条目清空为路由占位，启动自动迁移存量。

**Architecture:** 新增 `plugin_conf.py` 模块级全局单例（`AstrBotConfig` 包装），adapter/main/sessions 读写同一实例并 `save_config`。账户数据用 list 承载（tokens=`list[str]`、bindings=`list[{token,platform_id}]`、sessions=`list[{token,list}]`），绕开 `check_config_integrity` 对 dict 动态键的剔除。绑定解析从「扫描目标平台 tokens」改回「查插件 bindings 映射」。启动迁移把平台条目 tokens/bindings 收敛进插件配置。

**Tech Stack:** Python 3.10+ / pytest-asyncio / Quart；AstrBot 插件配置 `AstrBotConfig`。

## Global Constraints

- 账户数据唯一来源 = 插件配置 `astrbot_plugin_botapi_config.json`（`get_astrbot_config_path()` 下），经 `plugin_conf.load_plugin_conf()` 单例读写。
- 动态键映射（bindings/sessions）必须用 **list 包裹**（`[{token,...}]`），schema 声明 `{"type":"list"}` —— dict 动态键会被 `check_config_integrity` 剔除（已实测）。
- 绑定 UMO 格式不变：`{bound}:FriendMessage:botapi_{scoped_key}`；未绑定回退 botapi UMO。
- auth 严格列表不变：`_is_valid_token` = `token in (adapter.cfg.tokens or [])`（`adapter.cfg.tokens` 为运行时缓存，写操作后同步）。
- 版本 → **v3.0.1**（修复事故，接口不变）。
- 全量测试：`python -m pytest tests/ -q`（基线 212 通过）。

---

## File Structure

- Create: `plugin_conf.py`（全局插件配置单例 + tokens/bindings/sessions/主机读写 helper）
- Create: `tests/test_plugin_conf.py`
- Modify: `_conf_schema.json`（声明 host/port/tokens/bindings/sessions，全 list 承载）
- Modify: `adapter.py`（__init__ 用单例；binding_platform_for/bind/unbind 改读写插件 bindings；迁移重写）
- Modify: `sessions.py`（sessions_list/save_sessions 数据源改插件配置）
- Modify: `main.py`（_persist_tokens/_do_create/_do_delete/_do_stats/_accounts 读插件配置；删除联动清理 bindings/sessions）
- Modify: `models.py`（BotApiConfig 删 tokens/sessions 或保留为缓存——见 Task 4）
- Modify: `routes.py`（若 cfg 缓存移除则改读 get_tokens()）
- Modify: `metadata.yaml`（3.0.0 → 3.0.1）、`CHANGELOG.md`
- Test（重写/更新）：`test_binding_storage.py`、`test_binding_handlers.py`、`test_binding_routing.py`、`test_binding_history.py`、`test_platforms_web.py`、`test_sessions_storage.py`、`test_admin_handlers.py`、`test_migration.py`、`test_singleton.py`、`test_adapter_init.py`、`test_chat.py`、`test_export.py`、`test_sessions_admin.py`、`test_sessions_api.py`、`test_sessions_routing.py`、`test_binding_proactive.py`

---

### Task 1: 全局插件配置单例（plugin_conf.py + schema + 测试）

**Files:**
- Create: `plugin_conf.py`
- Create: `tests/test_plugin_conf.py`
- Modify: `_conf_schema.json`

**Interfaces:**
- Consumes: `astrbot.core.utils.astrbot_path.get_astrbot_config_path()`、`astrbot.core.config.astrbot_config.AstrBotConfig`、插件目录 `_conf_schema.json`。
- Produces:
  - `plugin_conf.load_plugin_conf() -> AstrBotConfig`（模块级单例）
  - `plugin_conf.reset_plugin_conf()`（测试/重载用）
  - `plugin_conf.get_tokens() -> list` / `set_tokens(list)` / `get_bindings() -> list` /
    `set_bindings(list)` / `get_sessions_map() -> dict[token, list]` / `set_sessions_map(dict)` /
    `save()`（转发 `save_config`）/ `get_host()`/`get_port()`

- [ ] **Step 1: 写失败测试**

`tests/test_plugin_conf.py`：

```python
# tests/test_plugin_conf.py — 全局插件配置单例（账户数据源 + host/port）
import json
import os
import pytest


@pytest.fixture(autouse=True)
def _reset_conf(monkeypatch, tmp_path):
    """每个测试前重置单例，patch get_astrbot_config_path → tmp_path。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    yield
    pc.reset_plugin_conf()


def _conf_path():
    from astrbot_plugin_botapi import plugin_conf as pc
    import astrbot.core.utils.astrbot_path as p
    return os.path.join(p.get_astrbot_config_path(), "astrbot_plugin_botapi_config.json")


def test_load_creates_file_with_defaults():
    from astrbot_plugin_botapi import plugin_conf as pc
    conf = pc.load_plugin_conf()
    assert conf.get("host") == "0.0.0.0"
    assert conf.get("port") == 9000
    assert conf.get("tokens") == []
    assert conf.get("bindings") == []
    assert conf.get("sessions") == []
    assert os.path.exists(_conf_path())


def test_load_is_singleton():
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.load_plugin_conf() is pc.load_plugin_conf()


def test_reset_clears_singleton():
    from astrbot_plugin_botapi import plugin_conf as pc
    first = pc.load_plugin_conf()
    pc.reset_plugin_conf()
    assert pc.load_plugin_conf() is not first


def test_tokens_roundtrip_persists(tmp_path):
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_tokens(["t1", "t2"])
    pc.save()
    # 重新加载（reset 后从磁盘读）
    pc.reset_plugin_conf()
    assert pc.get_tokens() == ["t1", "t2"]


def test_bindings_roundtrip_persists(tmp_path):
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_bindings([{"token": "t1", "platform_id": "aiocqhttp_xxx"}])
    pc.save()
    pc.reset_plugin_conf()
    assert pc.get_bindings() == [{"token": "t1", "platform_id": "aiocqhttp_xxx"}]


def test_sessions_map_roundtrip_persists(tmp_path):
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_sessions_map({"t1": [{"id": "abc", "name": "x", "created_at": 1}]})
    pc.save()
    pc.reset_plugin_conf()
    assert pc.get_sessions_map()["t1"][0]["id"] == "abc"


def test_schema_declares_account_keys():
    from pathlib import Path
    import json as _json
    schema = _json.loads(
        (Path(__file__).resolve().parent.parent / "_conf_schema.json").read_text(encoding="utf-8-sig")
    )
    for key in ("host", "port", "tokens", "bindings", "sessions"):
        assert key in schema, f"schema 缺 {key}"
    assert schema["tokens"]["type"] == "list"
    assert schema["bindings"]["type"] == "list"
    assert schema["sessions"]["type"] == "list"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_plugin_conf.py -q`
Expected: FAIL（`plugin_conf` 模块不存在 → ModuleNotFoundError）。

- [ ] **Step 3: 写 plugin_conf.py**

`plugin_conf.py`：

```python
# plugin_conf.py — 全局插件配置单例（账户数据源 + host/port）
# 账户数据（tokens/bindings/sessions）必须存这里（全局），不能存 botapi 平台条目——
# AstrBot BotConfigService.update_bot 会用 WebUI 表单整体覆盖平台条目，导致账户丢失。
# 动态键映射（bindings/sessions）用 list 包裹：check_config_integrity 会剔除 dict 动态键，
# 但 list 元素完全保留（已实测）。
import json
import os
from pathlib import Path

_conf = None


def _load_schema():
    p = Path(__file__).parent / "_conf_schema.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8-sig"))
    return {}


def load_plugin_conf():
    """返回全局插件配置单例（AstrBotConfig 是 dict 子类）。首次调用创建并读盘。"""
    global _conf
    if _conf is None:
        from astrbot.core.utils.astrbot_path import get_astrbot_config_path
        from astrbot.core.config.astrbot_config import AstrBotConfig
        _conf = AstrBotConfig(
            config_path=os.path.join(get_astrbot_config_path(),
                                     "astrbot_plugin_botapi_config.json"),
            schema=_load_schema(),
        )
    return _conf


def reset_plugin_conf():
    """清空单例（测试/配置重载用）。"""
    global _conf
    _conf = None


def save():
    try:
        load_plugin_conf().save_config()
    except Exception:
        pass


def get_host():
    return load_plugin_conf().get("host") or "0.0.0.0"


def get_port():
    return load_plugin_conf().get("port") or 9000


def get_tokens():
    return list(load_plugin_conf().get("tokens") or [])


def set_tokens(tokens):
    load_plugin_conf()["tokens"] = list(tokens)


def get_bindings():
    return list(load_plugin_conf().get("bindings") or [])


def set_bindings(bindings):
    load_plugin_conf()["bindings"] = list(bindings)


def get_sessions_map():
    """返回 {token: [会话对象]}。"""
    out = {}
    for item in load_plugin_conf().get("sessions") or []:
        if isinstance(item, dict) and item.get("token"):
            out[item["token"]] = list(item.get("list") or [])
    return out


def set_sessions_map(sessions_map):
    load_plugin_conf()["sessions"] = [
        {"token": tok, "list": list(lst)} for tok, lst in sessions_map.items()
    ]
```

- [ ] **Step 4: 更新 `_conf_schema.json`**

```json
{
  "host": {
    "description": "botapi 监听地址",
    "type": "string",
    "default": "0.0.0.0"
  },
  "port": {
    "description": "botapi 监听端口",
    "type": "int",
    "default": 9000
  },
  "tokens": {
    "description": "账户注册表（token 列表，auth 严格校验，空=拒连）",
    "type": "list",
    "default": []
  },
  "bindings": {
    "description": "账户→机器人绑定（[{token, platform_id}]，一对一）",
    "type": "list",
    "default": []
  },
  "sessions": {
    "description": "账户会话（[{token, list:[会话]}]）",
    "type": "list",
    "default": []
  }
}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_plugin_conf.py -q`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add plugin_conf.py tests/test_plugin_conf.py _conf_schema.json
git commit -m "feat(server): 全局插件配置单例（账户数据源 tokens/bindings/sessions）"
```

---

### Task 2: adapter 绑定改读写插件配置（binding_platform_for/bind/unbind + __init__）

**Files:**
- Modify: `adapter.py`
- Test: `tests/test_binding_storage.py`（重写）

**Interfaces:**
- Consumes: `plugin_conf.get_bindings()`/`set_bindings()`/`save()`、`plugin_conf.get_tokens()`/`get_host()`/`get_port()`、`self._active_platforms`。
- Produces:
  - `BotApiAdapter.__init__`：`self._conf = load_plugin_conf()`；`_host`/`_port` 读插件配置（保留 `_legacy_port` 回退）；`self.cfg.tokens = plugin_conf.get_tokens()`（运行时缓存）。
  - `binding_platform_for(token) -> str | None`：查插件 bindings 得 pid → 活跃校验（`_active_platforms` 非空须含，为空回退 enable=True）。
  - `bind_token(token, platform_id)`：从插件 bindings 移除该 token 旧条目，追加 `{token, platform_id}`，`save()`。
  - `unbind_token(token)`：从插件 bindings 移除该 token 条目（有变更才 save）。
  - `_migrate_legacy_bindings()` 重写为 `_migrate_accounts()`（见 Task 5）。

- [ ] **Step 1: 重写失败测试 `tests/test_binding_storage.py`**

```python
# tests/test_binding_storage.py — 绑定=插件配置 bindings 列表（token→platform_id 一对一）
import json
import os
from types import SimpleNamespace
import pytest


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """重置插件配置单例 → tmp_path，预写初始配置（tokens + bindings）。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": ["tok1"],
                   "bindings": [{"token": "tok1", "platform_id": "aiocqhttp_main"}],
                   "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _adapter(active=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "type": "botapi"}
    a.cfg = SimpleNamespace(tokens=list(pc.get_tokens()))
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = set(active if active is not None else {"aiocqhttp_main"})
    return a


def _set_platform_config(monkeypatch, platforms):
    """monkeypatch adapter 模块的 astrbot_config（绑定回退 enable 判定用）。"""
    import astrbot_plugin_botapi.adapter as adapter_mod

    class _FakeCfg(dict):
        def __init__(self, plats):
            super().__init__({"platform": plats})

        def save_config(self):
            self.saved = True

    fake = _FakeCfg(platforms)
    monkeypatch.setattr(adapter_mod, "astrbot_config", fake)
    return fake


def test_binding_platform_for_returns_platform():
    a = _adapter()
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_unbound_returns_none():
    a = _adapter()
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_bindings([])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_inactive_returns_none():
    a = _adapter(active={"telegram_x"})
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_active_empty_fallback_enabled(monkeypatch):
    """_active_platforms 为空 → 回退该平台 enable=True。"""
    a = _adapter(active=set())
    _set_platform_config(monkeypatch, [{"id": "aiocqhttp_main", "enable": True}])
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_active_empty_fallback_disabled(monkeypatch):
    a = _adapter(active=set())
    _set_platform_config(monkeypatch, [{"id": "aiocqhttp_main", "enable": False}])
    assert a.binding_platform_for("tok1") is None


def test_unbind_token_removes_from_bindings():
    a = _adapter()
    a.unbind_token("tok1")
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == []


def test_unbind_token_no_change_no_save():
    a = _adapter()
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_bindings([])
    calls = []
    orig_save = pc.save
    pc.save = lambda: calls.append(True)
    try:
        a.unbind_token("tok1")
        assert calls == []          # 无变更不落盘
    finally:
        pc.save = orig_save


def test_bind_token_writes_bindings_one_to_one():
    a = _adapter()
    a.bind_token("tok1", "telegram_x")
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == [{"token": "tok1", "platform_id": "telegram_x"}]


def test_bind_token_moves_existing_binding():
    a = _adapter()
    a.bind_token("tok1", "telegram_x")
    a.bind_token("tok1", "discord_y")
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == [{"token": "tok1", "platform_id": "discord_y"}]


def test_bind_token_skips_botapi_target():
    a = _adapter()
    a.bind_token("tok1", "other_botapi")
    from astrbot_plugin_botapi import plugin_conf as pc
    # 目标为 botapi 类型平台条目 → 不写入，保留原绑定
    assert pc.get_bindings() == [{"token": "tok1", "platform_id": "aiocqhttp_main"}]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_binding_storage.py -q`
Expected: FAIL（`binding_platform_for` 仍扫描平台条目 tokens → 返回 None；`bind_token` 写平台条目）。

- [ ] **Step 3: 改 adapter.py**

`__init__` 里替换 host/port 读取块（**迁移方法仍调现有的 `_migrate_legacy_bindings`**，Task 5 才改名重写）：

```python
        # 插件配置单例：host/port + 账户数据（tokens/bindings/sessions）全局唯一。
        from .plugin_conf import load_plugin_conf, get_tokens, get_host, get_port
        self._conf = load_plugin_conf()
        legacy_host, legacy_port = self._legacy_port()
        self._host = get_host() or legacy_host or "0.0.0.0"
        self._port = get_port() or legacy_port or 9000
        self.cfg.tokens = get_tokens()   # 运行时缓存（auth 用）
        self._migrate_legacy_bindings()
        self._server_started = False
```

替换绑定方法（`binding_platform_for`/`unbind_token`/`bind_token`）；**保留 `_save_platforms` 不动**（仅旧 `_migrate_legacy_bindings` 用它，Task 5 重写后删除）：

```python
    # ── token→platform 绑定（多机器人）──
    # 绑定存插件配置 bindings 列表（[{token, platform_id}]，一对一）。全局数据不存
    # 平台条目（update_bot 会整体覆盖导致丢失）。

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
        from .plugin_conf import get_bindings, set_bindings
        binds = [b for b in get_bindings() if b.get("token") != token]
        if len(binds) != len(get_bindings()):
            set_bindings(binds)
            self._save_plugin_conf()

    def bind_token(self, token: str, platform_id: str) -> None:
        """绑定 token → 目标平台：先移除旧条目（一对一），再追加。botapi 类型目标忽略。"""
        from .plugin_conf import get_bindings, set_bindings
        if platform_id == self.config.get("id"):
            return
        for p in (astrbot_config.get("platform") or []):
            if p.get("id") == platform_id and p.get("type") == "botapi":
                return
        binds = [b for b in get_bindings() if b.get("token") != token]
        binds.append({"token": token, "platform_id": platform_id})
        set_bindings(binds)
        self._save_plugin_conf()

    def _save_plugin_conf(self):
        """落盘插件配置单例（绑定/账户变更后调用）。"""
        from .plugin_conf import save
        save()
```

- [ ] **Step 4: 修正测试的 monkeypatch 用法后运行确认通过**

Run: `pytest tests/test_binding_storage.py -q`
Expected: PASS（把 `monkeypatch_config` 改为真正的 `monkeypatch.setattr(adapter_mod, "astrbot_config", fake)`，测试函数签名带 `monkeypatch`）。

- [ ] **Step 5: Commit**

```bash
git add adapter.py tests/test_binding_storage.py
git commit -m "feat(server): 绑定改读写插件配置 bindings（一对一映射）"
```

---

### Task 3: sessions 数据源改插件配置（sessions.py + 测试）

**Files:**
- Modify: `sessions.py`
- Test: `tests/test_sessions_storage.py`（更新 fixture/断言）

**Interfaces:**
- Consumes: `plugin_conf.get_sessions_map()`/`set_sessions_map()`/`save()`。
- Produces: `sessions_list(adapter, token)` / `save_sessions(adapter, token, sessions)` 数据源改插件配置；`delete_session` 的存储移除改插件配置。

- [ ] **Step 1: 更新失败测试 `tests/test_sessions_storage.py`**

`_adapter` 改为不依赖 `a.config["sessions"]`/`a.cfg.sessions`，改用插件配置单例（autouse fixture 重置 + tmp_path）。要点：

```python
@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    import json, os
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [], "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _adapter(sessions_map=None, active=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "type": "botapi"}
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = set(active or ())
    if sessions_map:
        pc.set_sessions_map(sessions_map)
    return a
```

更新断言：
- `test_sessions_list_derives_default_first`：`assert pc.get_sessions_map() == {}`（不再 `a.config["sessions"]`）。
- `test_save_sessions_persists_config_cfg_global` → 改名 `test_save_sessions_persists_plugin_conf`：`S.save_sessions(a, "tok", [...])` 后 `pc.get_sessions_map()["tok"][0]["id"] == "abc"`。
- `test_delete_session_removes_and_saves`：`pc.get_sessions_map()["tok"]` 不含 abc。
- `test_delete_default_session_guard`：`pc.get_sessions_map()["tok"]` 含 abc。
- 绑定相关（`test_delete_session_bound_*`）：`active`/bindings 通过 `pc.set_bindings([{"token":"tok","platform_id":"aiocqhttp_main"}])` 注入，不再用平台条目 tokens。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_sessions_storage.py -q`
Expected: FAIL（`sessions_list` 仍读 `adapter.config`）。

- [ ] **Step 3: 改 sessions.py**

```python
def sessions_list(adapter, token: str) -> list:
    """某 token 的会话列表（含默认在最前）。只读派生，不改存储。"""
    from .plugin_conf import get_sessions_map
    raw = get_sessions_map().get(token, [])
    lst = list(raw)
    if not lst or lst[0].get("id") != DEFAULT_SESSION_ID:
        lst = [{
            "id": DEFAULT_SESSION_ID,
            "name": DEFAULT_SESSION_NAME,
            "created_at": 0,
        }] + [x for x in lst if x.get("id") != DEFAULT_SESSION_ID]
    return lst


def save_sessions(adapter, token: str, sessions: list) -> None:
    """写插件配置 sessions（token→list）+ 落盘。"""
    from .plugin_conf import get_sessions_map, set_sessions_map, save
    all_s = get_sessions_map()
    all_s[token] = list(sessions)
    set_sessions_map(all_s)
    save()
```

`delete_session` 里 `save_sessions` 调用不变（已走插件配置）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_sessions_storage.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add sessions.py tests/test_sessions_storage.py
git commit -m "feat(server): sessions 数据源改插件配置（全局）"
```

---

### Task 4: main.py 账户读写改插件配置（+ 删除联动清理 bindings/sessions）

**Files:**
- Modify: `main.py`
- Test: `tests/test_admin_handlers.py`、`tests/test_export.py`、`tests/test_sessions_admin.py`

**Interfaces:**
- Consumes: `plugin_conf.get_tokens()`/`set_tokens()`/`save()`、`adapter.binding_platform_for`、`plugin_conf.get_sessions_map()`/`set_sessions_map()`。
- Produces: `_persist_tokens(new_tokens)`（写插件配置 + 更新 `adapter.cfg.tokens`）；`_do_create`/`_do_delete`/`_do_stats`/`_accounts`/`_do_export` 读插件 tokens；`_do_delete` 联动清 bindings + sessions。

- [ ] **Step 1: 更新失败测试 `tests/test_admin_handlers.py`**

加 autouse fixture（重置插件配置 → tmp_path，创建空配置文件），`_make_star` 改为 real adapter + 显式 `pc.set_tokens()` 注入：

```python
@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    import json, os
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [],
                   "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _make_star(monkeypatch, tokens=None):
    """真实 BotApiAdapter（免 __init__）+ 插件配置注入 tokens。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    from astrbot_plugin_botapi import runtime as rt_mod
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "type": "botapi"}
    a.cfg = SimpleNamespace(tokens=list(tokens or []))
    a._sse_clients = {}
    a._disabled_tokens = set()
    a._last_active = {}
    a._active_platforms = {"aiocqhttp_main"}
    pc.set_tokens(list(tokens or []))
    pc.save()

    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    rt_mod.runtime().adapter = a
    return star, a, registered
```

- `test_create_account_persists`：`token = await star._do_create("newtok")` 后断言 `"newtok" in pc.get_tokens()`、`"newtok" in adapter.cfg.tokens`、配置已落盘（`pc.get_tokens()` 持久）。
- `test_delete_account`：`_do_delete` 后 `"a" not in pc.get_tokens()`。
- `test_stats_envelope`：`_do_stats` 后 `total_accounts == 2`。
- 确认无 `test_create_with_nickname`/`test_set_nickname`/`test_delete_removes_nickname`/`test_stats_includes_nickname` 残留（Task 1 已删）。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_admin_handlers.py tests/test_export.py tests/test_sessions_admin.py -q`
Expected: FAIL（`_persist_tokens` 仍写平台条目）。

- [ ] **Step 3: 改 main.py**

```python
    def _persist_tokens(self, adapter, new_tokens):
        """写插件配置 tokens + 更新运行时缓存 + 落盘。"""
        from .plugin_conf import set_tokens, save
        set_tokens(new_tokens)
        adapter.cfg.tokens = list(new_tokens)
        save()
```

`_do_stats`：`for token in adapter.cfg.tokens or []` 改为 `for token in (plugin_conf.get_tokens() or [])`（或依赖 `adapter.cfg.tokens` 缓存——保持用 `adapter.cfg.tokens`，因为 `_persist_tokens` 已同步缓存）。

`_do_create`：
```python
        from .plugin_conf import get_tokens, set_tokens, save
        token = token or uuid.uuid4().hex[:16]
        toks = list(get_tokens())
        if token not in toks:
            toks.append(token)
            self._persist_tokens(adapter, toks)
```

`_do_delete`：
```python
        from .plugin_conf import get_tokens, get_bindings, set_bindings, set_sessions_map, get_sessions_map, save
        target = next((t for t in get_tokens() if self._hash_tok(t) == token_hash), None)
        if not target:
            return Response().error("未找到账户").__dict__
        toks = [t for t in get_tokens() if t != target]
        self._persist_tokens(adapter, toks)
        adapter.unbind_token(target)          # 清 bindings
        all_s = get_sessions_map()
        all_s.pop(target, None)               # 清 sessions
        set_sessions_map(all_s)
        save()
        # ...（SSE 清理与 _token_to_origin 不变）
```

`_do_bind`/`_do_unbind`：`target` 从 `get_tokens()` 找；`adapter.bind_token`/`unbind_token` 不变。

`_accounts`：`for t in (adapter.cfg.tokens or [])` 保持（缓存已同步）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_admin_handlers.py tests/test_export.py tests/test_sessions_admin.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_admin_handlers.py tests/test_export.py tests/test_sessions_admin.py
git commit -m "feat(server): main 账户读写改插件配置（删除联动清理 bindings/sessions）"
```

---

### Task 5: 迁移重写（adapter._migrate_accounts + test_migration.py 重写）

**Files:**
- Modify: `adapter.py`（`_migrate_legacy_bindings` → `_migrate_accounts`）
- Test: `tests/test_migration.py`（整体重写）

**Interfaces:**
- Consumes: `plugin_conf.get_tokens()`/`set_tokens()`/`get_bindings()`/`set_bindings()`/`save()`、`astrbot_config["platform"]`、`self.config["id"]`。
- Produces: `BotApiAdapter._migrate_accounts()`：平台条目账户数据收敛进插件配置，幂等。

- [ ] **Step 1: 重写失败测试 `tests/test_migration.py`**

```python
# tests/test_migration.py — 平台条目账户数据 → 插件配置（全局）
import json
import os
import pytest


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
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


def _adapter(monkeypatch, platforms, self_config):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.config = dict(self_config)
    import astrbot_plugin_botapi.adapter as adapter_mod
    fake = _FakeCfg(platforms)
    monkeypatch.setattr(adapter_mod, "astrbot_config", fake)
    return a, fake


class _FakeCfg(dict):
    def __init__(self, platforms):
        super().__init__({"platform": platforms})
        self.platforms = platforms
        self.saved = None

    def save_config(self):
        self.saved = True


def test_migrate_platform_tokens_to_plugin(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1", "t2"], "enable": True},
    ], {"id": "botapi"})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_tokens() == ["t1", "t2"]          # 平台 tokens → 插件
    assert "tokens" not in fake.platforms[0]         # 平台条目清空


def test_migrate_target_platform_tokens_to_bindings(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
        {"id": "aiocqhttp_main", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_tokens() == ["t1"]
    assert pc.get_bindings() == [{"token": "t1", "platform_id": "aiocqhttp_main"}]
    assert "tokens" not in fake.platforms[1]


def test_migrate_plugin_tokens_already_present_is_noop(monkeypatch):
    """插件 tokens 非空 → 跳过平台条目迁移（幂等）。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"})
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_tokens(["t1"])
    a._migrate_accounts()
    assert fake.platforms[0].get("tokens") == ["t1"]   # 不动平台条目（已迁过）
    assert pc.get_tokens() == ["t1"]


def test_migrate_cleans_botapi_entry_old_keys(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True,
         "botapi_bindings": {"t1": "aiocqhttp_main"}, "nicknames": {"t1": "x"},
         "host": "0.0.0.0", "port": 9000, "sessions": {"t1": []}},
        {"id": "aiocqhttp_main", "tokens": [], "enable": True},
    ], {"id": "botapi", "botapi_bindings": {"t1": "aiocqhttp_main"}})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_tokens() == ["t1"]
    assert pc.get_bindings() == [{"token": "t1", "platform_id": "aiocqhttp_main"}]
    for key in ("tokens", "botapi_bindings", "nicknames", "host", "port", "sessions"):
        assert key not in fake.platforms[0], f"botapi 条目残留 {key}"


def test_migrate_skips_botapi_type_target(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
        {"id": "other_botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == []                    # botapi 类型平台不承接绑定
    assert pc.get_tokens() == ["t1"]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_migration.py -q`
Expected: FAIL（`_migrate_accounts` 不存在 → AttributeError）。

- [ ] **Step 3: 实现 `_migrate_accounts`**

替换 `_migrate_legacy_bindings` 方法体（保留方法名 `_migrate_accounts`，`__init__` 调用处同步改名）：

```python
    def _migrate_accounts(self):
        """把平台条目残留的账户数据收敛进插件配置（全局）。

        1. 插件 tokens 为空且 botapi 平台条目 tokens 非空 → 迁到插件。
        2. 非 botapi 平台条目 tokens → 生成 bindings 条目。
        3. 清理 botapi 平台条目的 tokens/botapi_bindings/nicknames/host/port/sessions。
        幂等：插件 tokens 非空即视为已迁移，跳过 1-2（3 仍执行，无键即 no-op）。
        """
        from .plugin_conf import get_tokens, set_tokens, get_bindings, set_bindings, save
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
            # 2. 非 botapi 平台条目 tokens → bindings
            binds = list(get_bindings())
            existing = {b.get("token") for b in binds}
            for p in platforms:
                if p.get("type") == "botapi" or p.get("id") == self.config.get("id"):
                    continue
                pt = p.get("tokens") or []
                if pt:
                    for tok in pt:
                        if tok not in existing:
                            binds.append({"token": tok, "platform_id": p.get("id")})
                            existing.add(tok)
                    p.pop("tokens", None)
                    changed = True
            if binds:
                set_bindings(binds)
            # 3. 清理 botapi 平台条目旧键
            for p in platforms:
                if p.get("id") == self.config.get("id"):
                    for key in ("tokens", "botapi_bindings", "nicknames", "host", "port", "sessions"):
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
        except Exception:
            pass
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_migration.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add adapter.py tests/test_migration.py
git commit -m "feat(server): 平台条目账户数据自动迁移到插件配置（幂等）"
```

---

### Task 6: 连带测试更新 + 版本 bump + 全量绿

**Files:**
- Modify: `tests/test_binding_routing.py`、`tests/test_binding_history.py`、`tests/test_platforms_web.py`、`tests/test_binding_proactive.py`、`tests/test_binding_handlers.py`、`tests/test_chat.py`、`tests/test_sessions_api.py`、`tests/test_sessions_routing.py`、`tests/test_singleton.py`、`tests/test_adapter_init.py`、`tests/test_routes_message.py`、`metadata.yaml`、`CHANGELOG.md`

**Interfaces:**
- Consumes: 各测试原有 fixture 改插件配置数据源。
- Produces: 全量测试绿。

- [ ] **Step 1: 更新各测试 fixture（数据源 → 插件配置）**

新建 `tests/conftest.py`（若不存在），放共用的 autouse 插件配置 fixture；已有同名本地 fixture 的测试文件改为引用 conftest 的（删除本地重复定义）：

```python
# tests/conftest.py — 全局插件配置 fixture（所有依赖 plugin_conf 的测试共用）
import json
import os
import pytest


@pytest.fixture(autouse=True)
def _plugin_conf(monkeypatch, tmp_path):
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000,
                   "tokens": [], "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()
```

> **注意**：autouse fixture 对所有测试生效（含不需要它的），重置单例 + tmp_path 无副作用。若某测试文件需要**非空**初始 tokens/bindings，在 `_make_star`/`_adapter` 内显式 `pc.set_tokens(...)`/`pc.set_bindings(...)` 注入（conftest 只保证干净空配置）。

各文件改动要点：
- `test_binding_routing.py`：`_adapter` 的 `a.config["botapi_bindings"]`/平台 tokens → `pc.set_bindings([{"token":"tok","platform_id":"aiocqhttp_main"}])`；`monkeypatch.setattr(S, "astrbot_config", ...)` 保留（sessions 用）；`submit_inbound` 绑定 UMO 断言不变。
- `test_binding_history.py`：`_adapter`/`_make_star` 的 bindings 改 `pc.set_bindings`；`binding_platform_for` closure 改读 `pc.get_bindings()`。
- `test_platforms_web.py`：`_make_real_adapter`/`_make_star` 的 `botapi_bindings` 改 `pc.set_bindings`；`binding_platform_for` closure 改读插件配置；`_refresh_active_platforms` 相关不变。
- `test_binding_proactive.py`/`test_chat.py`/`test_sessions_api.py`/`test_sessions_routing.py`：`a.cfg`/`a.config` 的 tokens 改 `pc.set_tokens` 或依赖缓存。
- `test_singleton.py`：`_adapter` 的 `platform_config` 里 `tokens` 移除（账户在插件配置）；`test_init_reads_host_port_from_plugin_config` 断言 `a.cfg.tokens` 从插件读。
- `test_adapter_init.py`：`test_init_with_full_platform_config` 的 `platform_config` 删 tokens；断言 `a.cfg.tokens == []`（插件配置默认）。
- `test_routes_message.py`：`_make_adapter_with_app` 的 `adapter.cfg.tokens` 改 `pc.set_tokens(["secret-tok"])`（或直接设 cfg 缓存）。

- [ ] **Step 2: 版本 bump**

`metadata.yaml`：`version: 3.0.0` → `version: 3.0.1`。

`CHANGELOG.md` 顶部加：

```markdown
## [3.0.1] - 2026-08-05

### 修复

- **升级后账户丢失（v3.0.0 回归）**：账户数据此前存在 botapi 平台条目，WebUI 保存一次
  平台配置即被 AstrBot `update_bot` 整体覆盖 → tokens 清空。账户数据（tokens/bindings/
  sessions）迁入插件配置 `astrbot_plugin_botapi_config.json`（全局），botapi 平台条目
  清空为路由占位；启动自动迁移存量平台条目账户。
```

- [ ] **Step 3: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: 全绿（212 基线 + 新增 plugin_conf/migration 用例，删除的旧断言除外）。

- [ ] **Step 4: 修复任何残留失败**（逐个定位，确保全绿）

- [ ] **Step 5: Commit**

```bash
git add tests/ metadata.yaml CHANGELOG.md
git commit -m "feat(server): 账户数据迁插件配置 — 版本 3.0.1 + 全量测试更新"
```