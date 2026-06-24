# BotAPI 手机端接口文档

> BotAPI 适配器插件暴露给手机 App 的 HTTP API。App 经 REST 发消息/传文件、SSE 长连接收回复，断连重连自动补消息。

- **Base URL**：`https://<your-domain>/api/v1/botapi`（经 nginx 反代到适配器端口，默认 9000）；也可直连 `http://<host>:9000`，端点路径相同（`selfcheck.sh` 即直连）。
- **认证**：除 `/auth` 外，所有请求须带 `Authorization: Bearer <token>`
- **Content-Type**：`application/json`（普通请求）/ `multipart/form-data`（上传）

---

## 目录

1. [认证 /auth](#1-认证-auth)
2. [发消息 /message](#2-发消息-message)
3. [上传文件 /upload](#3-上传文件-upload)
4. [SSE 流 /stream](#4-sse-流-stream)
5. [历史 /history](#5-历史-history)
6. [SSE 事件类型](#6-sse-事件类型)
7. [聚合约定](#7-聚合约定)
8. [错误码](#8-错误码)
9. [完整流程示例](#9-完整流程示例)

---

## 1. 认证 /auth

校验 token，返回会话标识。App 启动时调用一次即可（token 本身即凭证，后续请求带 Bearer）。

```
POST /auth
Content-Type: application/json

{ "token": "your-secret-token" }
```

**响应 200**：
```json
{ "user_id": "your-secret-token", "session_id": "botapi:FriendMessage:your-secret-token" }
```
**响应 401**：
```json
{ "error": "invalid_token" }
```
> 注意：`/auth` 的 401 形态是 `{"error":"invalid_token"}`（无 `code`）；其余端点的 401 由鉴权中间件统一返回 `{"error":"unauthorized","code":"INVALID_TOKEN"}`（见 [§8](#8-错误码)）。

> `session_id` 即 `unified_msg_origin`（`{platform_id}:FriendMessage:{token}`），App 可不关心，仅用于调试。会话与连接解耦：token 绑定会话，SSE 断连重连同 token 即续上。

---

## 2. 发消息 /message

发送一条消息，触发 AstrBot LLM 处理。**纯 SSE 模式**：本接口仅返回 `message_id`，回复（含首条）一律经 `/stream` SSE 推送。

```
POST /message
Authorization: Bearer <token>
Content-Type: application/json

{
  "text": "你好",                       // 文本（与 file_ids 可同时存在）
  "file_ids": ["f_001", "f_002"],      // 可选，先 /upload 得到的文件 ID
  "reply_to": "msg_042"                // 可选，引用回复的消息 ID（预留）
}
```

**响应 200**：
```json
{ "message_id": "botapi_a1b2c3d4e5f6" }
```
**响应 401**：`{"error":"unauthorized","code":"INVALID_TOKEN"}`

> 发完立即返回 `message_id`（毫秒级），App 转而监听 `/stream` 收回复。若 `/stream` 未连接，回复会进入该 token 的 SSE 队列，重连后补推。

---

## 3. 上传文件 /upload

上传图片/音频/文件，得到 `file_id`，再在 `/message` 引用。文件类型由 mime 决定消息组件（image/audio/file）。

```
POST /upload
Authorization: Bearer <token>
Content-Type: multipart/form-data

file: <binary>
```

**响应 200**：
```json
{
  "file_id": "f_a1b2c3d4e5",
  "name": "photo.jpg",
  "mime_type": "image/jpeg",
  "size": 102400
}
```
**响应 400**：`{"error":"no_file"}`

> `file_id` 仅进程生命周期有效，重启后失效。约定上传后立即 `/message` 引用。`path` 不返回（避免泄露服务器路径）。

---

## 4. SSE 流 /stream

长连接，实时接收该 token 的所有回复。支持自动重连 + 断连补消息。

```
GET /stream?since=<last_id>
Authorization: Bearer <token>
Accept: text/event-stream
```

**响应**：`Content-Type: text/event-stream`，持续推送 SSE 事件（见 [§6](#6-sse-事件类型)）。

**Query 参数**：
- `since`（可选）：从指定消息 ID **之后**补推漏掉的消息（断连补消息）。**此 ID 是 `platform_message_history` 表的整数行 id**（即 `/history` 返回的 `message_id`，或上次 catchup 回放事件携带的 id）。注意：live 实时 `message` 事件携带的 `message_id` 形如 `botapi_xxx`，**不能**作为 `since`——服务端会以 `int(since)` 解析，传 `botapi_*` 会报错。App 应通过 `/history?since=0` 取得整数游标后再带入 `since`。

**保活**：30 秒无消息推送 `event: ping`，App 忽略即可。

**断连补推**：连接建立后，若带 `since`，先补推 `id > since` 的文本消息（媒体不补推——服务端不持久化媒体），再进入实时推送。

**事件格式**（SSE 标准）：
```
event: <类型>
data: <JSON>

```
（每个事件以空行结束）

---

## 5. 历史 /history

拉取历史消息（断连补消息的 REST 版本，或翻页）。

```
GET /history?since=msg_042&before=msg_100&limit=50
Authorization: Bearer <token>
```

**Query 参数**：
- `since`：拉此 ID 之后的消息（补消息）
- `before`：翻页，此 ID 之前的消息
- `limit`：每页条数，默认 50，最大 200

**响应 200**：
```json
{
  "messages": [
    {
      "message_id": "1",
      "role": "user",
      "type": "text",
      "content": "你好",
      "timestamp": 1719234567
    },
    {
      "message_id": "2",
      "role": "assistant",
      "type": "text",
      "content": "你好！有什么可以帮你？",
      "timestamp": 1719234568
    },
    {
      "message_id": "3",
      "role": "assistant",
      "type": "thinking",
      "content": "用户问的是...",
      "timestamp": 1719234570
    }
  ],
  "has_more": false
}
```

> `message_id` 是 `platform_message_history` 表的稳定自增 int（字符串化）。`type`：`text` / `thinking` / `tool_status`。媒体不在历史中回放（仅推送时投递）。`has_more` 表示是否还有更早的消息。

---

## 6. SSE 事件类型

### event: message

文本/图片/音频/文件回复。

```json
{
  "message_id": "botapi_xxx",
  "type": "text",                 // text | image | audio | file
  "content": "回复内容",          // text: 字符串；image/audio: URL；file: {name,url,size}
  "subtype": "tool_status",       // 可选，工具活动文本（不并入答案）
  "streaming": true,              // true=流式增量片段；false/无=完整
  "final": true,                  // true=本轮最终文本（携带完整文本供自纠正）
  "segment_end": true,            // 可选，流式分段边界
  "timestamp": 1719234567
}
```

**type 取值**：
- `text`：`content` 是字符串。
- `image`：`content` 是图片 URL（仪表盘 `/api/file/<token>`，免认证，**单次有效 + 300s 过期**，收到立即下载缓存）。
- `audio`：`content` 是音频 URL。
- `file`：`content` 是 `{"name":"...", "url":"..."}`。

### event: thinking

模型思考过程（reasoning_content，DeepSeek R1 / Claude thinking 等）。

```json
{
  "message_id": "botapi_xxx",
  "content": "嗯，用户问的是...",
  "streaming": true,              // 流式片段
  "timestamp": 1719234573
}
```

### event: error

错误。当前仅一种：

```json
{ "code": "SESSION_KICKED", "message": "管理员已断开此会话" }
```

> `SESSION_KICKED` 在管理员于管理页"强制断开"该会话时推送。`RATE_LIMITED` / `PUSH_FAILED` 暂未实现（保留）。

### event: ping

30 秒保活心跳，App 忽略。

```
event: ping
data: {}
```

> **关于工具调用**：结构化 `tool_call`/`tool_result`（含 `arguments`）对本适配器不可达，工具活动以 `event: message` + `subtype:"tool_status"` 文本到达（如 "🔨 调用工具: web_search"），受 AstrBot `show_tool_use` 控制。App 渲染为系统提示气泡，**不并入答案文本**。

---

## 7. 聚合约定

一次 `/message` 触发一轮回复，`message_id` 全程不变，但 SSE 可能推送多个事件（工具状态、流式增量、分段、final）。App 聚合规则：

| 事件 | App 处理 |
|:--|:--|
| `message` + `subtype:"tool_status"` | 独立系统提示气泡，**不并入答案** |
| `message` + `streaming:true` | 按 `message_id` 追加到该答案气泡的增量缓冲 |
| `message` + `segment_end:true` | 流式分段边界，可据此切段 |
| `message` + `final:true` | 用 `content` 完整文本**自纠正**该答案气泡（防增量丢包） |
| `thinking` | 可折叠的思考气泡（默认折叠） |
| `image`/`audio`/`file` | 收到 URL 立即下载缓存（URL 单次有效） |
| `error` | 错误气泡 |

**流式时序**：`thinking(streaming)*` → `message(streaming:true)*` → `message(final:true)`。

**断连补消息**：App 离线期间错过的事件，重连 `?since=<最大 id>` 补推（仅文本，媒体不补）。

---

## 8. 错误码

| HTTP | code / error | 说明 |
|:--|:--|:--|
| 401 | `{"error":"invalid_token"}` | `/auth` 专用：token 无效/被禁用（无 code） |
| 401 | `INVALID_TOKEN` | 其余端点：鉴权中间件返回 `{"error":"unauthorized","code":"INVALID_TOKEN"}` |
| 400 | `no_file` | `/upload` 未带文件 |
| - | `SESSION_KICKED` | SSE error 事件：管理员强制断开该会话 |

---

## 9. 完整流程示例

```bash
TOKEN="your-secret-token"
BASE="https://your.domain/api/v1/botapi"

# 1. 认证
curl -s -X POST $BASE/auth -H "Content-Type: application/json" -d "{\"token\":\"$TOKEN\"}"

# 2. 开 SSE 流（终端1，挂起收回复）
curl -N $BASE/stream -H "Authorization: Bearer $TOKEN"

# 3. 发消息（终端2）→ 回复出现在终端1的 SSE 流
curl -s -X POST $BASE/message -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"text":"你好"}'

# 4. 上传图片再发
FID=$(curl -s -X POST $BASE/upload -H "Authorization: Bearer $TOKEN" \
  -F "file=@photo.jpg" | python -c "import sys,json;print(json.load(sys.stdin)['file_id'])")
curl -s -X POST $BASE/message -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "{\"file_ids\":[\"$FID\"]}"

# 5. 断连补消息：先用 /history 取整数游标，断开 /stream 后重连 ?since=<整数 id>
curl -N "$BASE/stream?since=2" -H "Authorization: Bearer $TOKEN"

# 6. 拉历史
curl -s "$BASE/history?since=0&limit=50" -H "Authorization: Bearer $TOKEN"
```

**App 端最小实现**：
1. 启动 → `POST /auth`。
2. 开 `GET /stream`（持久），按 §7 聚合事件渲染。
3. 发消息 → `POST /message`，回复从 /stream 收。
4. 切后台断开 /stream；回前台先 `GET /history?since=0` 取最新整数 id，再 `?since=<该 id>` 重连补漏（注意 `since` 是整数行 id，非 `botapi_*`）。
5. 首次/重装 → `GET /history?since=0` 拉文本历史（媒体不在历史中）。
