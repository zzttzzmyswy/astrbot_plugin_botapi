# 管理页直接对话 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 inline TDD 执行（无人值守）。Steps 用 `- [ ]` 跟踪。完整实现代码见 spec `docs/superpowers/specs/2026-06-28-dashboard-chat-design.md`。

**Goal:** 管理页加「对话」按钮，admin 以 token 身份在同一会话发话，轮询历史收回复。

**Architecture:** 抽 `submit_inbound` 共享 helper（手机/admin 共用注入逻辑）；main.py 加 2 个管理端点（`sessions/<hash>/chat` POST、`sessions/<hash>/history` GET）；前端加整页聊天视图 + 1200ms 轮询。回复只读历史表，不碰 9000 SSE。

**Tech Stack:** Python（AstrBot plugin）、pytest-asyncio、原生 JS + bridge.apiGet/apiPost、CSS 变量。

---

## 文件结构

- Modify: `routes.py` — 抽 `submit_inbound`，`send_message` 复用。
- Modify: `main.py` — 加 `_do_chat/_chat`、`_do_history/_history` + 注册。
- Modify: `pages/dashboard/index.html` — 账户行「对话」按钮 + `chat-view`/`main-view` section。
- Modify: `pages/dashboard/app.js` — openChat/closeChat/loadHistory/poll/send/renderBubble。
- Modify: `pages/dashboard/style.css` — 聊天视图样式。
- Create: `tests/test_chat.py` — submit_inbound + _do_chat + _do_history 测试。
- Modify: `metadata.yaml` → 1.2.0；`CHANGELOG.md` → [1.2.0]。

---

### Task 1: 抽 `submit_inbound` 共享 helper（routes.py）

**Files:** Modify `routes.py`; Test `tests/test_chat.py`

- [ ] **Step 1: 写 submit_inbound 测试（失败）**

`tests/test_chat.py`:
```python
import asyncio, pytest
from astrbot_plugin_botapi import routes as R

def _fake_adapter(token="t1"):
    class Comp:  # 捕获 commit 的事件
        def __init__(self): self.committed = []
        self.client_self_id = "botapi"
        self._uploaded_files = {}
        self.platform_id = "botapi"
        def meta(self):
            class M: id = "botapi"
            return M()
        def commit_event(self, ev): self.committed.append(ev)
    return Comp()

@pytest.mark.asyncio
async def test_submit_inbound_builds_and_commits(monkeypatch):
    calls = {}
    async def fake_persist(token, mid, text): calls["persist"] = (token, mid, text)
    monkeypatch.setattr(R, "persist_inbound_text", fake_persist)
    adapter = _fake_adapter()
    mid = await R.submit_inbound(adapter, "t1", "你好")
    assert mid.startswith("botapi_")
    assert calls["persist"] == ("t1", mid, "你好")
    assert adapter.committed and adapter.committed[0].session_id == "t1"
    assert adapter.committed[0].sender.user_id == "t1"
```

- [ ] **Step 2: 跑测试确认失败**

`pytest tests/test_chat.py::test_submit_inbound_builds_and_commits -v` → FAIL（`submit_inbound` 未定义）。

- [ ] **Step 3: 实现 submit_inbound（按 spec §组件1 代码），把 send_message 改为调它**

routes.py：新增 `async def submit_inbound(adapter, token, text, file_ids=None) -> str:`（spec 全文代码），`send_message` 路由改为：
```python
async def send_message():
    token = _extract_token(adapter)
    data = await request.get_json()
    text = (data or {}).get("text", "")
    file_ids = (data or {}).get("file_ids", [])
    message_id = await submit_inbound(adapter, token, text, file_ids)
    return jsonify({"message_id": message_id})
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

`pytest tests/test_chat.py tests/test_routes_message.py -v` → 全绿。

- [ ] **Step 5: Commit**

`git add routes.py tests/test_chat.py && git commit -m "refactor(routes): 抽 submit_inbound 共享 helper（手机/admin 注入统一）"`

---

### Task 2: admin `_do_chat` / `_chat` 端点（main.py）

**Files:** Modify `main.py`; Test `tests/test_chat.py`

- [ ] **Step 1: 写 _do_chat 测试（失败）**

`tests/test_chat.py` 追加：
```python
from astrbot_plugin_botapi.main import BotApiStar

def _star_with_tokens(tokens):
    class A:
        def __init__(self): self.cfg = type("c",(),{"tokens":tokens,"nicknames":{}})()
        self.platform_id = "botapi"
    from astrbot_plugin_botapi.runtime import runtime
    rt = runtime(); rt.adapter = A()
    s = BotApiStar.__new__(BotApiStar)  # 不跑 __init__（避免注册 web_api）
    return s

@pytest.mark.asyncio
async def test_do_chat_happy(monkeypatch):
    s = _star_with_tokens(["t1"])
    async def fake_submit(adapter, token, text): return "botapi_xxx"
    monkeypatch.setattr("astrbot_plugin_botapi.routes.submit_inbound", fake_submit)
    res = await s._do_chat(_hash("t1"), "你好")
    assert res["status"] == "ok" and res["data"]["message_id"] == "botapi_xxx"

@pytest.mark.asyncio
async def test_do_chat_unknown_account():
    s = _star_with_tokens(["t1"])
    res = await s._do_chat("deadbeef", "x")
    assert res["status"] == "error" and res["message"] == "未找到账户"

@pytest.mark.asyncio
async def test_do_chat_empty_text():
    s = _star_with_tokens(["t1"])
    res = await s._do_chat(_hash("t1"), "   ")
    assert res["message"] == "消息不能为空"
```
（`_hash` = `BotApiStar._hash_tok` 静态方法；测试里 `from astrbot_plugin_botapi.main import BotApiStar; _hash = BotApiStar._hash_tok`。）

- [ ] **Step 2: 跑确认失败**

`pytest tests/test_chat.py -k do_chat -v` → FAIL（`_do_chat` 不存在）。

- [ ] **Step 3: 实现 `_do_chat`/`_chat`（spec §组件2 代码）+ 注册**

main.py `BotApiStar` 加 `_do_chat`/`_chat` 方法；`__init__` 加：
```python
context.register_web_api(f"/{P}/sessions/<token_hash>/chat", self._chat, ["POST"], "会话对话")
```

- [ ] **Step 4: 跑通过**

`pytest tests/test_chat.py -k do_chat -v` → 全绿。

- [ ] **Step 5: Commit**

`git add main.py tests/test_chat.py && git commit -m "feat(admin): 会话对话端点 sessions/<hash>/chat"`

---

### Task 3: admin `_do_history` / `_history` 端点（main.py）

**Files:** Modify `main.py`; Test `tests/test_chat.py`

- [ ] **Step 1: 写 _do_history 测试（失败）**

```python
@pytest.mark.asyncio
async def test_do_history_happy(monkeypatch):
    s = _star_with_tokens(["t1"])
    async def fake_get(pid, tok, since, limit):
        assert tok == "t1" and since == "5" and limit == 50
        return [{"message_id":"6","role":"assistant","type":"text","content":"hi","timestamp":1}], False
    monkeypatch.setattr("astrbot_plugin_botapi.history.get_history", fake_get)
    res = await s._do_history(_hash("t1"), since="5", limit=50)
    assert res["data"]["messages"][0]["message_id"] == "6" and res["data"]["has_more"] is False

@pytest.mark.asyncio
async def test_do_history_unknown_account():
    s = _star_with_tokens(["t1"])
    res = await s._do_history("deadbeef")
    assert res["message"] == "未找到账户"

@pytest.mark.asyncio
async def test_do_history_limit_capped(monkeypatch):
    s = _star_with_tokens(["t1"])
    seen = {}
    async def fake_get(pid, tok, since, limit): seen["limit"]=limit; return [], False
    monkeypatch.setattr("astrbot_plugin_botapi.history.get_history", fake_get)
    await s._do_history(_hash("t1"), limit="9999")
    assert seen["limit"] == 200
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现 `_do_history`/`_history`（spec §组件2 代码）+ 注册** `sessions/<token_hash>/history` GET。

- [ ] **Step 4: 跑通过 + 全量回归** `pytest -q` → 全绿。

- [ ] **Step 5: Commit** `feat(admin): 会话历史端点 sessions/<hash>/history`

---

### Task 4: 前端聊天视图

**Files:** `pages/dashboard/index.html`、`app.js`、`style.css`

- [ ] **Step 1: index.html** — 主视图包 `<section id="main-view">`；操作列加「对话」按钮 `<button data-action="chat" data-hash=... data-nickname=...>对话</button>`；新增 `<section id="chat-view" class="hidden">`（spec §前端 HTML）。

- [ ] **Step 2: style.css** — 加 `.chat-view`/`.chat-header`/`.chat-messages`（flex 列、`overflow-y:auto`、`max-height:60vh`）/ `.bubble` + `.bubble-user`/`.bubble-bot` / `.chat-input-row` + `textarea`。复用 CSS 变量 + `[data-theme="dark"]`。

- [ ] **Step 3: app.js** — 加模块变量 `chatState={hash,nick,maxId,timer,active}`；实现 `openChat/closeChat/loadHistory/pollOnce/startPoll/stopPoll/sendChat/renderBubble/appendBubble`。`wireDelegation` 加 chat 分支。`btn-chat-back`→closeChat，`btn-chat-send`+回车→sendChat。轮询 1200ms 递归 setTimeout，`document.hidden` 跳过。发送成功立即 `pollOnce`。

- [ ] **Step 4: 手动验证**（无 JS 测试框架）— 起 AstrBot，开管理页：拉历史 → 发消息 → ~1s 见用户行 + bot 回复 → thinking 折叠 → 返回停轮询。若起不了服务，至少 `python -c` 静态检查 app.js 语法无意义（JS），改为浏览器 devtools 验；无环境则跳过手动步，靠后端测试 + 代码审查。

- [ ] **Step 5: Commit** `git add pages/dashboard && git commit -m "feat(pages): 管理页直接对话视图（整页 + 轮询）"`

---

### Task 5: 发版 1.2.0

**Files:** `metadata.yaml`、`CHANGELOG.md`

- [ ] **Step 1: metadata.yaml** `version: 1.1.5` → `1.2.0`。
- [ ] **Step 2: CHANGELOG.md** `[Unreleased]` 下加：
```markdown
## [1.2.0] - 2026-06-28

### Added

- 管理页直接对话：账户行「对话」按钮进入整页聊天视图，admin 以该账户身份在同一会话发话（与手机端共享上下文/历史），轮询历史收回复（final/thinking/tool_status），不碰 SSE。
- 后端 `submit_inbound` 共享 helper：手机 `/message` 与管理页 `/chat` 注入逻辑统一。
```
加 `[1.2.0]: ...releases/tag/v1.2.0` 链接；`[Unreleased]: ...compare/v1.2.0...HEAD`。
- [ ] **Step 3: 全量回归** `pytest -q` → 全绿。
- [ ] **Step 4: Commit** `chore: bump 1.2.0（管理页直接对话）`
- [ ] **Step 5: push + tag + release** `git push ...main`、`git tag v1.2.0`、`gh release create v1.2.0`。

---

## 自审

- **Spec 覆盖**：spec §组件1→Task1，§组件2 chat→Task2，§组件2 history→Task3，§前端→Task4，§发版→Task5。全覆盖。
- **占位符**：无；测试与实现代码均引自 spec 全文。
- **类型一致**：`submit_inbound` 签名三处一致；`_do_chat(token_hash,text)`/`_do_history(token_hash,since,limit)` 与测试一致。
- **回归**：Task1 改 routes.py 后跑 test_routes_message；Task3/5 跑全量。
