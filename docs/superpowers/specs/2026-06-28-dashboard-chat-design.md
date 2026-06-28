# 管理页直接对话 — 设计 spec

> 管理页（Dashboard）新增"直接与某账户会话对话"功能。管理员以该账户（token）身份在同一会话里发话，与手机端 API 共享同一会话上下文与历史。回复经轮询历史表获取，不碰 9000 的 SSE。

## 目标与非目标

**目标**
- 管理页账户行加「对话」按钮，进入整页聊天视图。
- admin 发消息 → 以该 token 身份注入同一会话（`{platform_id}:FriendMessage:{token}`），手机端 / 管理端共享 LLM 上下文与历史。
- admin 通过轮询历史表收 bot 回复（final 文本 / thinking / tool_status）。
- 手机端 / 管理端注入逻辑统一（DRY），避免双份实现漂移。

**非目标**
- 不做逐 token 流式（轮询天然只有 final）。
- 不在管理页上传文件（仅文本）。
- 不做乐观渲染（避免 live `botapi_*` id 与历史整数行 id 两套命名空间去重）。
- 不碰 9000 的 SSE / 不改 botapi 鉴权模型。

## 背景 / 约束

- 管理页 iframe 跑在 AstrBot 仪表盘（6185），经 `bridge.apiGet/apiPost` + 仪表盘鉴权调用 `context.register_web_api` 注册的端点，**只有 token_hash，无 raw token**。bridge 自动解包响应信封 `data`。
- botapi 的 SSE 流在独立 Quart（9000），Bearer raw token 鉴权。两套隔离。
- 会话由 `unified_msg_origin = {platform_id}:FriendMessage:{token}` 标识。手机 `/message` 构造 `AstrBotMessage(session_id=token, sender=token)` 并 `commit_event`，bot 回复经 `BotApiMessageEvent.send/send_streaming` 推 SSE + 写 `platform_message_history`。
- `platform_message_history` 行：稳定自增整数 `id`；`content` dict `{role, kind, text, message_id}`，`kind ∈ {user, final, thinking, tool_status}`；`created_at` naive datetime（1.1.4 已修读回补 UTC）。
- `row_to_sse(row)` → `{message_id: str(row.id), role, type, content, timestamp}`，`type ∈ {text, thinking, tool_status}`。
- `history.get_history(platform_id, token, since, before, limit)`：取最近 200 行内、按 `int(since)/int(before)` 过滤、末尾 `limit` 条；返回 `(msgs, has_more)`。

## 架构 / 数据流

```
管理页「对话」按钮
   │
   ├─ openChat(hash, nick)
   │    ├─ GET sessions/<hash>/history?limit=50  → 渲染气泡，记 maxId
   │    └─ 起轮询：每 1200ms GET sessions/<hash>/history?since=maxId
   │
   ├─ 发送：POST sessions/<hash>/chat {text}
   │    └─ 服务端 submit_inbound(adapter, token, text)  ← 共享 helper
   │         ├─ 构造 AstrBotMessage(session_id=token, sender=token, role=user)
   │         ├─ set_extra("enable_streaming", True)
   │         ├─ await persist_inbound_text(token, message_id, text)
   │         ├─ adapter.commit_event(event)
   │         └─ return message_id  (botapi_xxx)
   │    → 立即触发一次轮询，用户行 ~1200ms 内由历史带回
   │
   └─ bot 异步处理 → event.py send/send_streaming
        ├─ 推 SSE（手机端实时收到回复）   ← 同一会话固有副作用
        └─ persist_assistant_text/thinking → 历史表
              ↑ 管理页轮询取回 final/thinking/tool_status
```

**同一会话**：admin 注入与手机 `/message` 走同一 `submit_inbound`，`session_id=token` 相同 → ConversationManager 同一对话 → LLM 上下文共享。bot 回复照常推该 token 的 SSE 队列（手机端实时可见）+ 写历史（管理端轮询可见）。

## 组件

### 后端

**1. `routes.py::submit_inbound(adapter, token, text, file_ids=None) -> str`（新增共享 helper）**

把现有 `send_message` 路由里的"构造入站事件 + persist + commit"逻辑抽出。手机 `send_message` 改为调它；admin `_do_chat` 也调它。签名/行为：

```python
async def submit_inbound(adapter, token, text, file_ids=None) -> str:
    """构造入站 AstrBotMessage + BotApiMessageEvent，persist + commit。
    手机 /message 与管理页 /chat 共用，保证同一会话。返回 message_id。"""
    origin = _get_or_create_origin(adapter, token)
    msg = AstrBotMessage()
    msg.type = MessageType.FRIEND_MESSAGE
    msg.self_id = adapter.client_self_id
    msg.session_id = token
    msg.message_id = f"botapi_{uuid.uuid4().hex[:12]}"
    msg.sender = MessageMember(user_id=token, nickname="User")
    msg.timestamp = int(time.time())
    components = []
    if text:
        components.append(Plain(text))
    if file_ids:
        for fid in file_ids:
            info = adapter._uploaded_files.get(fid)
            if info:
                components.append(_file_info_to_component(info))
    msg.message = components
    msg.message_str = text or "[消息]"
    msg.raw_message = {"text": text, "file_ids": file_ids or []}
    event = BotApiMessageEvent(message_str=msg.message_str, message_obj=msg,
                               platform_meta=adapter.meta(), session_id=token, adapter=adapter)
    event.set_extra("enable_streaming", True)
    await persist_inbound_text(token, msg.message_id, text)
    adapter.commit_event(event)
    return msg.message_id
```

`send_message` 路由瘦身为取参 + `await submit_inbound(...)` + `jsonify({"message_id": ...})`。

**2. `main.py` 新增两个管理端点（`register_web_api`，6185 鉴权，hash 定位）**

| 端点 | 方法 | handler | 说明 |
|:--|:--|:--|:--|
| `sessions/<token_hash>/history` | GET | `_history(token_hash)` | query `since`(int, 可选)、`limit`(≤200, 默认 50) |
| `sessions/<token_hash>/chat` | POST | `_chat(token_hash)` | body `{text}` |

纯逻辑（可测）：

```python
async def _do_history(self, token_hash, since=None, limit=50):
    rt = runtime(); adapter = rt.adapter
    if not adapter:
        return Response().error("适配器未就绪").__dict__
    target = next((t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash), None)
    if not target:
        return Response().error("未找到账户").__dict__
    from .history import get_history
    limit = min(int(limit), 200) if limit else 50
    msgs, has_more = await get_history(adapter.platform_id, target, since, limit)
    return Response().ok({"messages": msgs, "has_more": has_more}).__dict__

async def _do_chat(self, token_hash, text):
    rt = runtime(); adapter = rt.adapter
    if not adapter:
        return Response().error("适配器未就绪").__dict__
    target = next((t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash), None)
    if not target:
        return Response().error("未找到账户").__dict__
    if not (text and text.strip()):
        return Response().error("消息不能为空").__dict__
    from .routes import submit_inbound
    message_id = await submit_inbound(adapter, target, text)
    return Response().ok({"message_id": message_id}).__dict__
```

HTTP 薄封装：

```python
async def _history(self, token_hash):
    since = request.args.get("since")
    limit = request.args.get("limit", 50)
    return await self._do_history(token_hash, since, limit)

async def _chat(self, token_hash):
    data = await request.get_json()
    text = (data or {}).get("text", "")
    return await self._do_chat(token_hash, text)
```

`__init__` 注册：
```python
context.register_web_api(f"/{P}/sessions/<token_hash>/history", self._history, ["GET"], "会话历史")
context.register_web_api(f"/{P}/sessions/<token_hash>/chat", self._chat, ["POST"], "会话对话")
```

### 前端（`pages/dashboard/`）

**`index.html`**：账户行操作列加「对话」按钮；新增整页聊天区：

```html
<section id="chat-view" class="hidden">
  <div class="chat-header">
    <button id="btn-chat-back" class="btn btn-sm btn-secondary">← 返回</button>
    <span id="chat-title">对话</span>
  </div>
  <div id="chat-messages" class="chat-messages"></div>
  <div class="chat-input-row">
    <textarea id="chat-input" rows="1" placeholder="输入消息..."></textarea>
    <button id="btn-chat-send" class="btn btn-primary">发送</button>
  </div>
</section>
```

主视图（stats/toolbar/table）包进 `<section id="main-view">`，切换 `hidden` 实现整页替换。

**`app.js`**：
- `wireDelegation` 加 `data-action="chat"` 分支 → `openChat(hash, nick)`。
- `openChat`：`main-view` 加 `hidden`、`chat-view` 去 `hidden`、设标题、`loadHistory(hash)`、起轮询 `startPoll(hash)`。
- `closeChat`：停轮询、`chat-view` 加 `hidden`、`main-view` 去 `hidden`、清空消息容器。
- `loadHistory(hash)`：`bridge.apiGet(\`sessions/${hash}/history?limit=50\`)` → 渲染气泡、`maxId = max(int(m.message_id))`。
- `pollOnce(hash)`：`bridge.apiGet(\`sessions/${hash}/history?since=${maxId}\`)` → 追加新行、更新 `maxId`。
- 轮询：递归 `setTimeout(pollOnce, 1200)`；`document.hidden` 时跳过本次；`chatPolling=true` 控制；`closeChat` 置 false。
- 发送：`bridge.apiPost(\`sessions/${hash}/chat\`, {text})` → 成功后立即 `pollOnce(hash)`（不等下个周期）；失败 toast。回车发送（Shift+Enter 换行）。
- 气泡渲染 `renderBubble(m)`：按 `role`/`type` 分类——`role=user` 👤；`type=thinking` 💭 `<details>` 折叠；`type=tool_status` 🔨 小字 monospace；其余 🤖 助手。`esc()` 转义内容。时间 `new Date(m.timestamp*1000).toLocaleTimeString('zh-CN')`。

**`style.css`**：`.chat-view` / `.chat-header` / `.chat-messages`（flex 列、`overflow-y:auto`、固定高度）/ 气泡（`.bubble-user` 右对齐 primary、`.bubble-bot` 左对齐 card-bg）/ `.chat-input-row`（textarea 自适应 + 发送按钮）。复用现有 CSS 变量与 dark 主题。

## 错误处理

- 适配器未就绪 → `error("适配器未就绪")`。
- 未知 hash → `error("未找到账户")`。
- 空文本 → `error("消息不能为空")`。
- `since` 非整数：admin 端不主动传非法值；`get_history` 内 `int(since)` 会抛 → 500（与手机 /history 现状一致，不在本特性范围内修）。
- 轮询失败：toast 一次错误后继续轮询（不静默吞，不连环弹）。

## 测试

新增 `tests/test_chat.py`（pytest-asyncio）：

1. `submit_inbound` 构造正确：mock adapter，断言生成的 event `session_id==token`、`sender.user_id==token`、`enable_streaming` 已设、`persist_inbound_text` 被调、`adapter.commit_event` 被调、返回 `botapi_*` id。
2. `_do_chat` happy path：注入 runtime（带 tokens 的假 adapter + 假 commit），断言返回 `{message_id}` 且 `submit_inbound` 被调（mock 替换）。
3. `_do_chat` 未知账户 → `res["message"]=="未找到账户"`。
4. `_do_chat` 空文本 → `res["message"]=="消息不能为空"`。
5. `_do_chat` 适配器未就绪 → `"适配器未就绪"`。
6. `_do_history` happy path：mock `get_history` 返回固定行 + `maxId` 过滤断言 since 透传、limit 上限 200。
7. `_do_history` 未知账户 → `"未找到账户"`。

`submit_inbound` 改造后，现有 `tests/test_routes_message.py` 仍须全绿（行为不变）。

前端无 JS 测试框架（与现状一致），手动验证：开对话 → 拉历史 → 发消息 → ~1s 见用户行 + bot 回复 → 折叠思考 → 返回停轮询。

## 边界 / 已知含义

- **手机端实时看到 admin 发起的回复**：同一会话固有副作用，已确认符合预期。
- 禁用账户：admin 仍可对话（会话不依赖 SSE/启停）。
- 无流式：admin 仅见 final 文本。
- 仅文本输入。
- 轮询只在聊天视图打开且页面可见时进行，关闭即停。

## 发版

`metadata.yaml` → `1.2.0`（新功能，minor）。CHANGELOG `[Unreleased]` → `[1.2.0] - 2026-06-28` Added 条目。tag `v1.2.0` + GitHub release。
