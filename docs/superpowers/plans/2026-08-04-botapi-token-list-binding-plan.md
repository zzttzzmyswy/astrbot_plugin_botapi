# 绑定存储改造（token 列表即绑定）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 botapi 账户→机器人的绑定关系从独立的 `botapi_bindings` 映射表改为「token 出现在目标平台 `tokens` 列表即绑定」，并清理 botapi 平台条目的 host/port/nicknames，auth 严格化。

**Architecture:** 绑定数据源从 `self.config["botapi_bindings"]` 改为模块级 `astrbot_config["platform"]`（非 botapi 条目的 `tokens` 列表，一对一）；`bind_token`/`unbind_token` 直接写平台条目并 `save_config`。迁移在 adapter `__init__` 一次性把旧 `botapi_bindings` 展开到目标平台 tokens 并清掉旧键。UMO 路由机制（`{bound}:FriendMessage:botapi_{scoped_key}`）完全不变。

**Tech Stack:** Python 3.10+ / pytest-asyncio / Quart；前端为静态 HTML/JS（AstrBot Plugin bridge）。

## Global Constraints

- 绑定后 UMO 格式不变：`{bound}:FriendMessage:botapi_{scoped_key}`（routes.py `submit_inbound` 覆写，本计划不触碰）。
- 绑定数据源唯一为 `astrbot_config["platform"]`：非 botapi 条目（`type != "botapi"` 且 `id != botapi条目id`）的 `tokens` 列表；一个 token 只出现在一个平台的 tokens 里（一对一）。
- 账户注册表 = botapi 平台条目 `tokens`；`_is_valid_token` 严格列表（`token in (adapter.cfg.tokens or [])`，空=拒连）。
- botapi 平台条目最终只剩 `tokens`（+`sessions`）；插件配置 `_conf_schema.json` 只剩 `host`/`port`。
- `astrbot_config["platform"]` 是纯列表，`AstrBotConfig.check_config_integrity` 不递归列表项，新增 `tokens` 键安全。
- 版本保持 `3.0.0`（未发布，无 tag）。
- 全量测试：`python -m pytest tests/ -q`（基线 209 通过）。

---

## File Structure

- Modify: `models.py`（BotApiConfig 删 host/port/nicknames）
- Modify: `adapter.py`（binding_platform_for/bind_token/unbind_token 重写；default_config_tmpl/config_metadata 清理；__init__ 迁移）
- Modify: `main.py`（_persist_tokens；删昵称端点/handler；bound_platform 改 binding_platform_for；_do_delete 解绑）
- Modify: `routes.py`（_is_valid_token 严格化）
- Modify: `history.py`（to_markdown 去 nickname）
- Modify: `_conf_schema.json`（删 botapi_bindings）
- Modify: `pages/dashboard/index.html`、`pages/dashboard/app.js`（删昵称列/改名/输入框）
- Modify: `README.md`、`CHANGELOG.md`
- Test: `tests/test_binding_storage.py`（重写）、`tests/test_binding_handlers.py`、`tests/test_binding_routing.py`、`tests/test_binding_history.py`、`tests/test_platforms_web.py`、`tests/test_admin_handlers.py`、`tests/test_export.py`、`tests/test_sessions_admin.py`、`tests/test_models.py`、`tests/test_adapter_init.py`、`tests/test_singleton.py`、`tests/test_routes_message.py`
- Create: `tests/test_migration.py`

---

### Task 1: 移除昵称功能（main.py / history.py / dashboard / models / adapter 模板）

**Files:**
- Modify: `models.py`、`adapter.py`、`main.py`、`history.py`、`pages/dashboard/index.html`、`pages/dashboard/app.js`
- Test: `tests/test_admin_handlers.py`、`tests/test_export.py`、`tests/test_sessions_admin.py`、`tests/test_models.py`

**Interfaces:**
- Consumes: 无（独立于绑定改造）。
- Produces: `main.BotApiStar._persist_tokens(adapter, new_tokens)`（替代 `_persist_account_state`）；`main._do_create(token=None)`（无 nickname 参数）；`main` 删除 `_do_set_nickname`/`_set_nickname`/`/nickname` 路由；`adapter` 的 `default_config_tmpl`/`config_metadata` 不再含 nicknames；`BotApiConfig` 删 `nicknames` 字段。

- [ ] **Step 1: 删昵称前端（index.html + app.js）**

`pages/dashboard/index.html`:
- thead `<th>昵称</th>` 删除（9 列 → 8 列）。
- `modal-add` 内昵称 label（`<label>昵称/备注（可选，便于区分）:<input type="text" id="input-nickname" .../></label>`）删除。
- 整个 `modal-nickname` div（改名模态）删除。

`pages/dashboard/app.js`:
- `renderAccounts()`：删 `<td>${esc(a.nickname || "-")}</td>`；`empty-row` colspan `9` → `8`；按钮 `data-nickname` 属性全部删除（绑定/对话/导出/改名按钮）。
- `showStatus` colspan 保持 `8`（现为 8，正确）。
- `wireDelegation()`：删 `const nick = btn.dataset.nickname || "";` 与 `if (action === "nickname") await setNickname(hash, nick);` 分支；`openExport(hash)`、`openSessions(hash)`、`openBind(hash)` 去掉 nickname 参数。
- `setupToolbar()`：`btn-create` handler 删 `const nickname = document.getElementById("input-nickname").value.trim();` 与 body 里 `nickname` 字段及清空语句。
- 删除 `promptDialog`、`setNickname`、`exportTarget = { hash, nickname }` → `exportTarget = { hash: "" }`；`openExport` 用 `tokenHash` 拼文案；`exportTarget = { hash: tokenHash }`。
- `openSessions(tokenHash)`/`openChatSession(tokenHash, sid)`：标题用 `tokenHash`（`sessions.nick`/`chat.nick` 不再存在，用 hash 兜底，如 `sessions.title = 会话：${tokenHash}`）。
- `bindTarget = { hash: "", nick: "" }` → `bindTarget = { hash: "" }`；`openBind(tokenHash)` 文案用 `tokenHash`。

- [ ] **Step 2: 删昵称服务端（main.py）**

```python
# _persist_account_state → _persist_tokens（只写 tokens）
def _persist_tokens(self, adapter, new_tokens):
    for p in _cfg_singleton.get("platform", []):
        if p.get("id") == adapter.config.get("id"):
            p["tokens"] = list(new_tokens)
            break
    adapter.config["tokens"] = list(new_tokens)
    adapter.cfg.tokens = list(new_tokens)
    _cfg_singleton.save_config()
```

- `__init__`：删 `context.register_web_api(f"/{P}/accounts/<token_hash>/nickname", self._set_nickname, ["POST"], "设置昵称")`。
- `_do_stats`：删 `"nickname": adapter.cfg.nicknames.get(token, "")` 行。
- `_do_create(self, token=None, nickname="")` → `_do_create(self, token=None)`：删 `nicks` 读取与 `if nickname: nicks[token]=...` 分支，`changed` 只在 token 新增时置 True，落盘改 `self._persist_tokens(adapter, toks)`。
- `_do_delete`：删 `nicks = {...}` 行；`self._persist_account_state(adapter, toks, nicks)` → `self._persist_tokens(adapter, toks)`。
- 删除 `_do_set_nickname` 整个方法；删 `_set_nickname` handler；`_create` handler 删 `nickname` 读取与传参。
- `_do_export`：`meta = {"token_preview": self._preview(target), "exported_at": ...}`；`safe_title = meta["token_preview"] or target[:8]`。
- `_accounts`：删 `"nickname": adapter.cfg.nicknames.get(t, "")` 行。

- [ ] **Step 3: 删昵称模型 + adapter 模板**

`models.py`（本 Task 只删 nicknames，host/port 留到 Task 4 移除）：
```python
@dataclass
class BotApiConfig:
    host: str = "0.0.0.0"
    port: int = 9000
    tokens: list = field(default_factory=list)
    sessions: dict = field(default_factory=dict)   # {token: [{id,name,created_at}, ...]}
```

`adapter.py`：
- `default_config_tmpl={"host": "0.0.0.0", "port": 9000, "tokens": []}`（删 nicknames）。
- `config_metadata` 删 `nicknames` 条目。
- `__init__` 的 `BotApiConfig(...)` 删 `nicknames=dict(platform_config.get("nicknames", {}))`。
- 删 `from .models import BotApiConfig` 之后无其他引用检查（`cfg.nicknames` 仅 main.py 用过，Step 2 已清）。

- [ ] **Step 4: 删昵称导出（history.py）**

`history.py` `to_markdown`：
```python
def to_markdown(rows: list, meta: dict) -> str:
    token_preview = meta.get("token_preview") or ""
    exported_at = meta.get("exported_at", "")
    title = token_preview or "未知账户"

    lines = [f"# BotAPI 对话记录 — {title}", ""]
    if token_preview:
        lines.append(f"> 账户：`{token_preview}`")
    else:
        lines.append("> 账户：（未知）")
    lines.append(f"> 导出时间：{exported_at}")
    lines.append(f"> 消息数：{len(rows)}")
    lines += ["", "---", ""]
    # （消息渲染部分不变）
```

- [ ] **Step 5: 更新受影响测试**

`tests/test_admin_handlers.py`：
- `_make_star` 删 `nicknames=None` 参数与 `nicks` 全部引用（`cfg`/`config`/`fake_cfg["platform"][0]` 里都去掉 `nicknames`）。
- 删 `test_create_with_nickname`、`test_set_nickname`、`test_delete_removes_nickname`、`test_stats_includes_nickname`。
- `test_create_account_persists` 改 `res = await star._do_create("newtok")`（无 nickname）。

`tests/test_export.py`：
- `to_markdown` 调用全部改 `{"token_preview": ..., "exported_at": ...}`（删 nickname 键）。
- `test_markdown_renders_user_and_assistant`：`assert "# BotAPI 对话记录 — Alice"` → `assert "# BotAPI 对话记录 — abc...1234"`；`assert "> 账户：Alice"` 改 `assert "> 账户：`abc...1234`"`。
- `_make_star` 删 `nicknames` 参数；`test_export_markdown` 删 `nicknames={"tok": "Alice"}`，`assert "Alice" in filename` → `assert "tok" in filename`。

`tests/test_sessions_admin.py`：`_make_star` 删 `nicknames` 参数与 `nicks` 引用（cfg/config/fake_cfg 去掉 nicknames 键）。

`tests/test_models.py`：`test_botapi_config_defaults` 删 `nicknames` 相关断言（保留 host/port/tokens，host/port 在 Task 4 移除）；`test_botapi_config_from_dict` 删 nicknames。

- [ ] **Step 6: 运行全量测试确认绿**

Run: `python -m pytest tests/ -q`
Expected: 全绿（209 减 4 个删除的昵称测试；`test_sessions_api.py`/`test_chat.py`/`test_binding_*` 等 fixture 里的 `nicknames: {}` 键无害，不需改）。

- [ ] **Step 7: Commit**

```bash
git add models.py adapter.py main.py history.py pages/dashboard/index.html pages/dashboard/app.js tests/
git commit -m "feat(server): 移除账户昵称/备注功能（平台配置与 UI 不再有 nicknames）"
```

---

### Task 2: 绑定存储改为「平台 tokens 列表」（adapter.py / main.py + 绑定测试）

**Files:**
- Modify: `adapter.py`（`binding_platform_for`/`bind_token`/`unbind_token` 重写 + `_save_platforms` helper；删 `self.config.setdefault("botapi_bindings", {})`）
- Modify: `main.py`（删 `_persist_bindings`；`_do_bind`/`_do_unbind` 去 persist 调用；`_do_delete` 调 `unbind_token`；`_do_stats`/`_accounts` bound_platform 用 `binding_platform_for`）
- Test: `tests/test_binding_storage.py`（重写）、`tests/test_binding_routing.py`、`tests/test_binding_handlers.py`、`tests/test_binding_history.py`、`tests/test_platforms_web.py`、`tests/test_sessions_storage.py`

**Interfaces:**
- Consumes: `astrbot_config`（模块级，与 `_legacy_port` 同一引用）、`self.config["id"]`、`self._active_platforms`。
- Produces:
  - `BotApiAdapter.binding_platform_for(token) -> str | None`：扫描 `astrbot_config["platform"]` 非 botapi 条目 `tokens`，命中且（`_active_platforms` 非空时）平台活跃 → 返回 pid；否则 None。
  - `BotApiAdapter.unbind_token(token) -> None`：从所有非 botapi 平台 tokens 移除 token，有变更才 `_save_platforms()`。
  - `BotApiAdapter.bind_token(token, platform_id) -> None`：先 `unbind_token(token)`（一对一），再把 token 追加到目标平台（非 botapi）tokens，落盘。
  - `BotApiAdapter._save_platforms()`：`getattr(astrbot_config, "save_config", None)` 存在则调用。
  - `main._do_bind`/`_do_unbind` 不再调 `_persist_bindings`；`_do_delete` 末尾调 `adapter.unbind_token(target)`；`_do_stats`/`_accounts` 的 `bound_platform` 来自 `adapter.binding_platform_for(token)`。

- [ ] **Step 1: 写失败测试（重写 test_binding_storage.py）**

`tests/test_binding_storage.py` 整体替换为：

```python
# tests/test_binding_storage.py — 绑定=token 出现在目标平台 tokens 列表
from types import SimpleNamespace
import pytest


def _adapter(monkeypatch, platforms=None, active=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": ["tok1"]}
    a.cfg = SimpleNamespace(tokens=["tok1"])
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = set(active if active is not None else {"aiocqhttp_main"})
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "astrbot_config", {
        "platform": list(platforms if platforms is not None else [
            {"id": "botapi", "type": "botapi", "tokens": ["tok1"], "enable": True},
            {"id": "aiocqhttp_main", "type": "aiocqhttp", "tokens": ["tok1"], "enable": True},
        ]),
    })
    return a


def test_binding_platform_for_returns_platform(monkeypatch):
    a = _adapter(monkeypatch)
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_unbound_returns_none(monkeypatch):
    a = _adapter(monkeypatch, platforms=[
        {"id": "botapi", "type": "botapi", "tokens": ["tok1"], "enable": True},
        {"id": "aiocqhttp_main", "tokens": [], "enable": True},
    ])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_inactive_returns_none(monkeypatch):
    """绑定的平台不在活跃列表 → 回退。"""
    a = _adapter(monkeypatch, active={"telegram_x"}, platforms=[
        {"id": "aiocqhttp_main", "tokens": ["tok1"], "enable": True},
    ])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_active_empty_fallback_enabled(monkeypatch):
    """_active_platforms 为空（重启后平台注入前）→ 回退 enable=True。"""
    a = _adapter(monkeypatch, active=set(), platforms=[
        {"id": "botapi", "type": "botapi", "enable": True},
        {"id": "aiocqhttp_main", "tokens": ["tok1"], "enable": True},
        {"id": "telegram_disabled", "tokens": ["tok1"], "enable": False},
    ])
    # 一对一配置：tok1 在 aiocqhttp_main（enable）+ telegram_disabled（disable）→ 第一个命中优先
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_active_empty_fallback_disabled(monkeypatch):
    a = _adapter(monkeypatch, active=set(), platforms=[
        {"id": "telegram_disabled", "tokens": ["tok1"], "enable": False},
    ])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_skips_botapi_type(monkeypatch):
    """type=botapi 平台条目（回环）不参与绑定。"""
    a = _adapter(monkeypatch, platforms=[
        {"id": "other_botapi", "type": "botapi", "tokens": ["tok1"], "enable": True},
    ])
    assert a.binding_platform_for("tok1") is None


def test_unbind_token_removes_from_all_platforms(monkeypatch):
    fake = _FakeCfg([{"id": "botapi", "type": "botapi"},
                     {"id": "a", "tokens": ["tok1", "tok2"], "enable": True},
                     {"id": "b", "tokens": ["tok1"], "enable": True}])
    a = _adapter(monkeypatch)
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config", fake)
    a.unbind_token("tok1")
    assert fake.platforms[1]["tokens"] == ["tok2"]
    assert fake.platforms[2]["tokens"] == []
    assert fake.saved is True


def test_unbind_token_no_change_no_save(monkeypatch):
    fake = _FakeCfg([{"id": "botapi", "type": "botapi"},
                     {"id": "a", "tokens": [], "enable": True}])
    a = _adapter(monkeypatch)
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config", fake)
    a.unbind_token("tok1")
    assert fake.saved is None   # 无变更不落盘


def test_bind_token_writes_target_and_unbinds_old(monkeypatch):
    fake = _FakeCfg([{"id": "botapi", "type": "botapi"},
                     {"id": "a", "tokens": ["tok1"], "enable": True},
                     {"id": "b", "tokens": [], "enable": True}])
    a = _adapter(monkeypatch)
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config", fake)
    a.bind_token("tok1", "b")
    assert fake.platforms[1]["tokens"] == []    # 从旧平台移除（一对一）
    assert fake.platforms[2]["tokens"] == ["tok1"]
    assert fake.saved is True


def test_bind_token_skips_botapi_target(monkeypatch):
    fake = _FakeCfg([{"id": "botapi", "type": "botapi"},
                     {"id": "other_botapi", "type": "botapi", "tokens": []}])
    a = _adapter(monkeypatch)
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config", fake)
    a.bind_token("tok1", "other_botapi")   # 目标为 botapi 类型 → 不写入
    assert fake.platforms[1]["tokens"] == []


class _FakeCfg(dict):
    """模拟 astrbot_config：带 platform 列表 + save_config 计数。"""
    def __init__(self, platforms):
        super().__init__({"platform": platforms})
        self.platforms = platforms
        self.saved = None

    def save_config(self):
        self.saved = True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_binding_storage.py -q`
Expected: FAIL（`binding_platform_for` 仍读 `botapi_bindings` → 返回 None；`bind_token`/`unbind_token` 仍写 `self.config["botapi_bindings"]` → `_FakeCfg` 平台列表未变）。

- [ ] **Step 3: 重写 adapter 绑定方法**

`adapter.py` 中替换整个 `# ── token→platform 绑定（多机器人）──` 段（含 `binding_platform_for`/`bind_token`/`unbind_token`）为：

```python
    # ── token→platform 绑定（多机器人）──
    # 绑定 = token 出现在非 botapi 平台条目的 tokens 列表（一对一）。数据源是模块级
    # astrbot_config（与 _legacy_port 同一引用），不存 adapter.config，避免被
    # check_config_integrity 剔除任意键；平台条目是列表项，不被 schema 检查。

    def _save_platforms(self):
        try:
            save = getattr(astrbot_config, "save_config", None)
            if save:
                save()
        except Exception:
            pass

    def binding_platform_for(self, token: str) -> str | None:
        """返回 token 绑定的 platform_id；未绑定或平台不活跃返回 None。

        扫描 astrbot_config["platform"] 非 botapi 条目 tokens。命中后校验活跃：
        _active_platforms 非空 → 须在集合内；为空（重启后平台注入前）→ 回退该条目
        enable=True。
        """
        pid = None
        try:
            for p in astrbot_config.get("platform", []):
                if p.get("id") == self.config.get("id") or p.get("type") == "botapi":
                    continue
                if token in (p.get("tokens") or []):
                    pid = p.get("id")
                    break
        except Exception:
            pass
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
        """把 token 从所有非 botapi 平台 tokens 移除；有变更才落盘。"""
        changed = False
        try:
            for p in astrbot_config.get("platform", []):
                if p.get("id") == self.config.get("id") or p.get("type") == "botapi":
                    continue
                toks = p.get("tokens") or []
                if token in toks:
                    p["tokens"] = [t for t in toks if t != token]
                    changed = True
        except Exception:
            pass
        if changed:
            self._save_platforms()

    def bind_token(self, token: str, platform_id: str) -> None:
        """绑定 token → 目标平台：先从所有平台移除（一对一），再追加到目标 tokens。"""
        self.unbind_token(token)
        try:
            for p in astrbot_config.get("platform", []):
                if p.get("id") != platform_id:
                    continue
                if p.get("type") == "botapi":
                    break
                toks = [t for t in (p.get("tokens") or []) if t != token]
                toks.append(token)
                p["tokens"] = toks
                self._save_platforms()
                break
        except Exception:
            pass
```

同时删 `__init__` 里 `self.config.setdefault("botapi_bindings", {})` 及注释。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_binding_storage.py -q`
Expected: PASS。

- [ ] **Step 5: 更新 main.py handler**

`main.py`：
- 删 `_persist_bindings` 方法。
- `_do_bind`：删 `self._persist_bindings(adapter)` 调用（`adapter.bind_token` 自带落盘）。
- `_do_unbind`：删 `self._persist_bindings(adapter)` 调用。
- `_do_delete`：`self._persist_tokens(adapter, toks)` 之后加 `adapter.unbind_token(target)`（从所有平台 tokens 移除）。
- `_do_stats`：删 `bindings = adapter.config.get("botapi_bindings") or {}`；`"bound_platform": adapter.binding_platform_for(token)`。
- `_accounts`：删 `bindings = adapter.config.get("botapi_bindings") or {}`；`"bound_platform": adapter.binding_platform_for(t)`。

- [ ] **Step 6: 更新绑定相关测试 fixture**

`tests/test_binding_routing.py`：
- `_adapter`：删 `a.config["botapi_bindings"] = {...}`（改为在 astrbot_config platform 里让 `aiocqhttp_main` 的 tokens 含 "tok"）；`monkeypatch.setattr(S, "astrbot_config", {"platform": []})` 保留（sessions 用），另加 `import astrbot_plugin_botapi.adapter as adapter_mod; monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [{"id": "botapi","type":"botapi","enable":True},{"id":"aiocqhttp_main","tokens":["tok"],"enable":True}]})`。
- `test_submit_inbound_bound_uses_platform_umo`：删 `a.config["botapi_bindings"] = ...`。
- `test_submit_inbound_bound_scoped_sid`：同上。
- `test_submit_inbound_bound_inactive_platform_fallback_botapi`：把 `a.config["botapi_bindings"] = {"tok": "aiocqhttp_main"}` 删，`a._active_platforms = set()`（空集 → 回退该条目 enable；让 aiocqhttp_main enable=False → 回退 botapi）。

`tests/test_binding_handlers.py`：
- 重写 `_make_star` 用**真实 BotApiAdapter**（`object.__new__` 绕过 `__init__`）承载真实 `bind_token`/`unbind_token`/`binding_platform_for`；`adapter_mod.astrbot_config` 与 `main_mod._cfg_singleton` monkeypatch 为**同一个** `FakeAstrbotConfig`（同一 `fake_cfg["platform"]` 列表，`save_config` 置 `_saved`）。完整替换 `_make_star` 为：

```python
def _make_star(monkeypatch, tokens=None, platforms=None):
    """真实 BotApiAdapter（免 __init__）+ adapter 与 main 共享同一 FakeAstrbotConfig。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import adapter as adapter_mod
    from astrbot_plugin_botapi import runtime as rt_mod
    import astrbot_plugin_botapi.main as main_mod

    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "type": "botapi", "tokens": list(tokens or [])}
    a.cfg = SimpleNamespace(tokens=list(tokens or []), sessions={})
    a._sse_clients = {}
    a._disabled_tokens = set()
    a._last_active = {}
    a._active_platforms = {"aiocqhttp_main"}

    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    rt_mod.runtime().adapter = a

    fake_cfg = {"platform": list(platforms if platforms is not None else [
        {"id": "botapi", "type": "botapi", "tokens": list(tokens or []), "enable": True},
        {"id": "aiocqhttp_main", "type": "aiocqhttp", "tokens": [], "enable": True},
    ])}

    class FakeAstrbotConfig:
        def __getitem__(self, k):
            return fake_cfg[k]

        def get(self, k, d=None):
            return fake_cfg.get(k, d)

        def save_config(self):
            fake_cfg["_saved"] = True

    fake = FakeAstrbotConfig()
    monkeypatch.setattr(adapter_mod, "astrbot_config", fake)
    monkeypatch.setattr(main_mod, "_cfg_singleton", fake)
    return star, a, fake_cfg, registered
```

- `test_bind_persists_to_platform_subtree`：`res = await star._do_bind(_hash("a"), "aiocqhttp_main")`；断言 `res["status"] == "ok"`、`fake_cfg["platform"][1]["tokens"] == ["a"]`（aiocqhttp_main 条目，index 1）、`fake_cfg.get("_saved") is True`。
- `test_bind_empty_platform_id_rejected`：断言 `fake_cfg["platform"][1]["tokens"] == []`（错误路径不落盘）。
- `test_bind_unknown_account_rejected`：保持（`res["status"] == "error"`、`"未找到账户" in res["message"]`）。
- `test_unbind_removes_binding`：`_make_star(monkeypatch, tokens=["a"], platforms=[botapi 条目, {"id": "aiocqhttp_main", "tokens": ["a"], "enable": True}])`；`_do_unbind` 后断言 `fake_cfg["platform"][1]["tokens"] == []`、`fake_cfg.get("_saved") is True`。
- `test_routes_registered`：保持（bind/unbind 路由注册断言）。
- `test_bind_adapter_not_ready`：保持。
- 删除 `test_bind_with_stubbed_persist_fails`（`_persist_bindings` 已不存在；真实 bind_token 自带落盘，无需负向测试）。

`tests/test_binding_history.py`：
- `_adapter`：删 `a.config["botapi_bindings"]`；改 `monkeypatch.setattr(S, "astrbot_config", {"platform": []})` 之外，另 `monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform":[{"id":"botapi","type":"botapi","enable":True},{"id":"aiocqhttp_main","tokens":["tok"],"enable":True}]})`。
- `_make_star`：fake adapter 的 `binding_platform_for` closure 保留（由 `bindings` dict 驱动），`config` 里删 `"botapi_bindings": binds`；`fake_cfg["platform"]` botapi 条目删 `botapi_bindings`。
- 各用例的 `bindings={"tok": "aiocqhttp_main"}` 传参不变（closure 仍读 binds）。

`tests/test_platforms_web.py`：
- `_make_real_adapter`：`config` 删 `"botapi_bindings": {}`；`cfg` 删 `botapi_bindings`。
- `_make_star`：SimpleNamespace `config` 删 `botapi_bindings`；`binding_platform_for` closure 保留（binds 驱动）。
- `test_bind_refresh_makes_binding_platform_for_effective` / `test_bind_to_inactive_platform_stays_inactive` / `test_stats_refreshes_active_platforms_for_binding`：真实 adapter 路径需 `monkeypatch.setattr(adapter_mod, "astrbot_config", fake)`（fake 与 `main_mod._cfg_singleton` 同一对象，含 `aiocqhttp_main` 条目，enable 由用例定）；删 `real.config["botapi_bindings"] = ...`（改 `_do_bind` 落盘后 token 进平台 tokens）。
- `test_bind_to_inactive_platform_stays_inactive`：断言 `fake_cfg["platform"]` 中 `aiocqhttp_main` 条目 tokens 含 "a"（绑定已持久化）+ `real.binding_platform_for("a") is None`（未生效）。

`tests/test_sessions_storage.py`：`_adapter` 删 `bindings` 参数与 `a.config["botapi_bindings"] = bindings`；`test_config_has_sessions_field` 保持。

- [ ] **Step 7: 全量测试确认绿**

Run: `python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 8: Commit**

```bash
git add adapter.py main.py tests/
git commit -m "feat(server): 绑定改为目标平台 tokens 列表（删除 botapi_bindings 映射）"
```

---

### Task 3: auth 严格化（routes.py）

**Files:**
- Modify: `routes.py`（`_is_valid_token`）
- Test: `tests/test_routes_message.py`（fixture 微调 + 新增空列表拒连用例）

**Interfaces:**
- Consumes: `adapter.cfg.tokens`。
- Produces: `_is_valid_token(adapter, token) -> bool`：`token in (adapter.cfg.tokens or [])`（空列表恒 False）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_routes_message.py` 末尾新增：

```python
@pytest.mark.asyncio
async def test_message_rejects_token_not_in_list(monkeypatch):
    """严格列表：token 不在 botapi tokens 里 → 401。"""
    adapter = _make_adapter_with_app(monkeypatch)
    adapter.cfg.tokens = []   # 空列表 = 拒连
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/message", json={"text": "hi"},
                          headers={"Authorization": "Bearer any-token"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_auth_rejects_empty_list(monkeypatch):
    adapter = _make_adapter_with_app(monkeypatch)
    adapter.cfg.tokens = []
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/auth", json={"token": "any-token"})
    assert r.status_code == 401
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes_message.py -q`
Expected: 新增两条 FAIL（当前空列表放行任意 token）。

- [ ] **Step 3: 改 `_is_valid_token`**

```python
def _is_valid_token(adapter, token):
    return token in (adapter.cfg.tokens or [])
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_routes_message.py -q`
Expected: PASS。

- [ ] **Step 5: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: 全绿（既有 routes/upload/stream/history/sessions_api 测试均用列表内 token）。

- [ ] **Step 6: Commit**

```bash
git add routes.py tests/test_routes_message.py
git commit -m "feat(server): auth 严格化 — token 必须显式在 botapi tokens 列表（空=拒连）"
```

---

### Task 4: host/port 配置模板清理 + 旧数据迁移 + schema + README

**Files:**
- Modify: `models.py`（BotApiConfig 删 host/port）、`adapter.py`（default_config_tmpl/config_metadata/__init__/`_legacy_port` 保留 + `_migrate_legacy_bindings`）、`_conf_schema.json`、`README.md`
- Test: `tests/test_models.py`、`tests/test_adapter_init.py`、`tests/test_singleton.py`
- Create: `tests/test_migration.py`

**Interfaces:**
- Consumes: `self.config`（botapi 平台条目）、`astrbot_config`、`get_astrbot_config_path()`、`_conf_schema.json`。
- Produces: `BotApiAdapter._migrate_legacy_bindings()`：把 `self.config["botapi_bindings"]`（及 astrbot_config botapi 条目的同名键）展开为各目标平台 tokens，剥离 botapi 条目 `botapi_bindings`/`nicknames`/`host`/`port`，有变更才 `_save_platforms()`；幂等。

- [ ] **Step 1: 写失败测试（创建 test_migration.py）**

`tests/test_migration.py`：

```python
# tests/test_migration.py — v3.0.0 旧 botapi_bindings/host/port/nicknames → 新布局
import pytest


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
        self.saved = None

    def save_config(self):
        self.saved = True


def test_migrate_expands_bindings_into_platform_tokens(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1", "t2"],
         "botapi_bindings": {"t1": "aiocqhttp_main"}, "nicknames": {"t1": "x"},
         "host": "0.0.0.0", "port": 9000},
        {"id": "aiocqhttp_main", "enable": True},
    ], {"id": "botapi", "botapi_bindings": {"t1": "aiocqhttp_main"}})
    a._migrate_legacy_bindings()
    assert fake.platforms[1]["tokens"] == ["t1"]
    assert "botapi_bindings" not in fake.platforms[0]
    assert "nicknames" not in fake.platforms[0]
    assert "host" not in fake.platforms[0]
    assert "port" not in fake.platforms[0]
    assert fake.saved is True


def test_migrate_idempotent(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"]},
        {"id": "aiocqhttp_main", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"})
    a._migrate_legacy_bindings()
    a._migrate_legacy_bindings()
    assert fake.saved is None            # 无旧键 → 不落盘
    assert fake.platforms[1]["tokens"] == ["t1"]


def test_migrate_missing_target_skipped(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"],
         "botapi_bindings": {"t1": "missing_platform"}},
    ], {"id": "botapi", "botapi_bindings": {"t1": "missing_platform"}})
    a._migrate_legacy_bindings()
    assert fake.saved is True            # 清理键也算变更
    assert "botapi_bindings" not in fake.platforms[0]


def test_migrate_skips_own_botapi_entry(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"],
         "botapi_bindings": {"t1": "other_botapi"}},
        {"id": "other_botapi", "type": "botapi", "tokens": [], "enable": True},
    ], {"id": "botapi", "botapi_bindings": {"t1": "other_botapi"}})
    a._migrate_legacy_bindings()
    assert fake.platforms[1]["tokens"] == []   # botapi 类型平台不承接绑定
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_migration.py -q`
Expected: FAIL（`_migrate_legacy_bindings` 不存在 → AttributeError）。

- [ ] **Step 3: 实现迁移 + 模板清理**

`models.py`：
```python
@dataclass
class BotApiConfig:
    tokens: list = field(default_factory=list)
    sessions: dict = field(default_factory=dict)
```

`adapter.py`：
- `default_config_tmpl={"tokens": []}`；`config_metadata={"tokens": {...}}`（删 host/port/nicknames 条目，tokens 描述改「绑定 token 列表（账户注册表，空=拒连）」）。
- `__init__`：`self.cfg = BotApiConfig(tokens=list(platform_config.get("tokens", [])), sessions=dict(platform_config.get("sessions", {})))`；`_migrate_legacy_bindings()` 调用放 `self._server_started = False` 前。
- 新增方法（放在 `_legacy_port` 之后）：

```python
    def _migrate_legacy_bindings(self):
        """v3.0.0 把绑定存在 botapi 条目 botapi_bindings（或插件配置）。
        迁移：展开为各目标平台 tokens；剥离 botapi 条目 botapi_bindings/nicknames/
        host/port。幂等：无旧键即跳过，不落盘。"""
        try:
            platforms = astrbot_config.get("platform")
            if not isinstance(platforms, list):
                return
            changed = False
            binds = dict(self.config.get("botapi_bindings") or {})
            if binds:
                for tok, pid in binds.items():
                    for p in platforms:
                        if p.get("id") == pid and p.get("type") != "botapi":
                            toks = [t for t in (p.get("tokens") or []) if t != tok]
                            toks.append(tok)
                            p["tokens"] = toks
                            changed = True
                            break
            # 剥离 botapi 条目旧键（self.config 与 astrbot_config 里的条目都可能残留）
            for p in platforms:
                if p.get("id") == self.config.get("id"):
                    for key in ("botapi_bindings", "nicknames", "host", "port"):
                        if key in p:
                            p.pop(key, None)
                            changed = True
                    break
            for key in ("botapi_bindings", "nicknames", "host", "port"):
                if key in self.config:
                    self.config.pop(key, None)
                    changed = True
            if changed:
                self._save_platforms()
        except Exception:
            pass
```

`_conf_schema.json` 整体替换为：

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
  }
}
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_migration.py -q`
Expected: PASS。

- [ ] **Step 5: 更新受影响测试**

`tests/test_models.py`：
- `test_botapi_config_defaults`：删 `cfg.host`/`cfg.port`/`cfg.tokens`？保留 `assert cfg.tokens == []`，删 host/port 断言。
- `test_botapi_config_from_dict`：`BotApiConfig(tokens=["t1"])`，只断言 tokens。

`tests/test_adapter_init.py`：
- `test_init_with_full_platform_config`：`platform_config` 保持（含 host/port/tokens，旧键容忍）；断言改 `adapter.cfg.tokens == ["t1", "t2"]`；删 `cfg.host`/`cfg.port` 断言；`adapter._host == "127.0.0.1"`？不 —— `_host`/`_port` 来自插件配置，这里 monkeypatch 的 `astrbot_config` 无 plugin conf 读取会走 `_legacy_port` 回退。此用例原断言 `cfg.host/port`，改为断言 `adapter._port is not None`（插件配置缺省回退默认 9000，见 test_singleton）。为简洁：删 host/port 断言，保留 tokens/platform_id/_upload_dir/_media_enabled。

`tests/test_singleton.py`：
- `_base_plugin_conf`：`"conf": {"host": "0.0.0.0", "port": 9000}`（删 botapi_bindings）。
- `test_conf_schema_declares_host_port_bindings` → 改名 `test_conf_schema_declares_host_port`：断言 `"host" in schema and "port" in schema`；`assert "botapi_bindings" not in schema`。
- 各 `c["conf"] = {...botapi_bindings...}`（line 87/134/149/173）删 botapi_bindings。
- `BotApiStar(ctx, {"host": ..., "port": ..., "botapi_bindings": {}})`（line 248/274）删 botapi_bindings。
- 若 `test_singleton` 中任何用例断言 `adapter.config` 含 botapi_bindings，删对应断言。

- [ ] **Step 6: 更新 README 配置表**

`README.md` 平台配置表（~line 70-80）改为：

```markdown
> 插件配置（插件配置页 `astrbot_plugin_botapi_config.json`）：
> | `host` | `0.0.0.0` | 监听地址 |
> | `port` | `9000` | 手机 API 端口（nginx 反代） |

机器人（平台）配置 `tokens`：**绑定到该平台的 BotAPI 账户 token 列表**（每个 token 一对一绑定一个平台；空列表则不绑定任何账户）。botapi 平台条目的 `tokens` 是账户注册表（新增账户在这里管理；`auth` 严格校验，token 必须在列表内）。
```

同时更新「多账户」段落与 `docs/API.md` 第 5 行「默认 9000」的表述（API.md 无 tokens 配置表，仅确认不误述绑定/昵称即可）。

- [ ] **Step 7: 全量测试确认绿**

Run: `python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 8: Commit**

```bash
git add models.py adapter.py _conf_schema.json README.md tests/test_migration.py tests/test_models.py tests/test_adapter_init.py tests/test_singleton.py
git commit -m "feat(server): 插件配置只剩 host/port + 迁移旧 botapi_bindings 到平台 tokens"
```

---

### Task 5: CHANGELOG + 全量验证 + selfcheck

**Files:**
- Modify: `CHANGELOG.md`、`metadata.yaml`（确认 3.0.0）
- Test: 全量 + `scripts/selfcheck.sh`

**Interfaces:**
- 无新接口；纯文档/收尾。

- [ ] **Step 1: 更新 CHANGELOG v3.0.0 条目**

`CHANGELOG.md` 的 `[3.0.0]` 条目替换为「token 列表即绑定」模型：

```markdown
## [3.0.0] - 2026-08-04

### 新增

- **单实例 botapi 服务器**：host/port 移入插件配置页（`astrbot_plugin_botapi_config.json`），
  模块级单实例锁保证一个 AstrBot 只绑定一次端口（多 botapi platform 条目共存不冲突）。
- **token→platform 绑定（多机器人）**：绑定即把账户 token 写入目标平台的 `tokens` 列表
  （一对一）；botapi 平台条目的 `tokens` 是账户注册表。绑定后该账户的对话（收发消息、
  历史、清空、统计、会话）路由到绑定平台的 LLM 配置，独立成 `botapi_` 前缀会话；
  管理页账户行「绑定/解绑」维护。解绑恢复默认单机路由。
- **`GET platforms` 端点**：Web 管理页返回当前活跃平台 id 列表（优先 adapter 注入的
  活跃集合，回退 `astrbot_config` 里 enable=True 的平台条目），供绑定 UI 下拉。
- **账户绑定状态展示**：`stats` 与 `accounts` 响应每条账户新增 `bound_platform` 字段。

### Changed

- **auth 严格化**：token 必须显式存在于 botapi 平台条目的 `tokens` 列表（空=拒连）。
- 移除账户昵称/备注（`nicknames`）字段与改名功能；绑定关系不再存独立的
  `botapi_bindings` 映射（启动时自动迁移到目标平台 tokens 列表）。

### 兼容性

- 老客户端/App 无需改动（HTTP API 不变）；绑定表为空时行为不变（默认路由）。
- 存量 `botapi_bindings` 配置启动时自动迁移；未在 botapi tokens 里的旧 token 需
  重新加入（auth 严格化）。
```

- [ ] **Step 2: 确认 metadata.yaml**

`metadata.yaml` 已为 `version: 3.0.0`（确认不改）。

- [ ] **Step 3: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 4: 运行 selfcheck（如有运行环境）**

Run: `./scripts/selfcheck.sh --base http://localhost:9000 --token <test-token>`（若本机无运行中 botapi 可跳过，注明即可）。
Expected: 依赖运行中服务；无服务时跳过。

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: v3.0.0 CHANGELOG 更新为 token 列表即绑定模型"
```
