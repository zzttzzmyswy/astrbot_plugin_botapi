# BotAPI 文档梳理与修复 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 README、校对修复 docs/API.md、新增 CHANGELOG.md、bump metadata 至 1.1.2 / `>=4.25.5`、删除 `docs/superpowers/` 过程产物，打 tag `v1.1.2` 并发 GitHub release。

**Architecture:** 纯文档任务，不改任何 `.py` / `pages/` / `tests/` / `scripts/`。每个任务以可执行的校验命令（grep / 渲染检查 / pytest sanity）代替单元测试。频繁 commit。

**Tech Stack:** Markdown, YAML, git, gh CLI。

**权威源码事实（已核实，供 Task 2 校对用）：**
- `message_id` 生成：`f"botapi_{uuid.uuid4().hex[:12]}"`（routes.py:48）→ 12 位 hex。
- `session_id`：`{platform_id}:FriendMessage:{token}`（routes.py:144），`platform_id` 默认 `"botapi"`（adapter.py:67）。
- `/auth` 401：`{"error": "invalid_token"}`（routes.py:32，**无 code**）。
- 其他端点 401（before_request 中间件）：`{"error": "unauthorized", "code": "INVALID_TOKEN"}`（routes.py:24）。
- `/upload` 400：`{"error": "no_file"}`（routes.py:74）；成功返回 `{file_id, name, mime_type, size}`（routes.py:84，**不含 path**）。
- SSE error 事件：**仅** `SESSION_KICKED` 真实发射（main.py:184，管理员强制断开时）。`RATE_LIMITED` / `PUSH_FAILED` 代码中**不存在**。
- `since` / `before`：`int(since)` 转换（history.py:23,35），必须是 `platform_message_history` 表的整数行 id；catchup/history 回放的 `message` 事件 `message_id = str(row.id)`（整数串），而 live `message` 事件 `message_id = botapi_*`（event.py 全程用 `self.message_obj.message_id`）。两套命名空间，`since` 只接受整数行 id。
- ping：30s（routes.py:120 `timeout=30`）。
- history `type` 映射：`{final→text, thinking→thinking, tool_status→tool_status}`（history.py:11）。

---

## Task 1: 重构 README.md

**Files:**
- Modify: `README.md`（整文件替换）

- [ ] **Step 1: 用下述完整内容替换 `README.md`**

```markdown
# BotAPI 移动端适配器（AstrBot 插件）

> 为 AstrBot 提供一个**自定义移动端 HTTP API**：手机 App 通过 REST 发消息、SSE 长连接收回复，专为弱网/后台断连设计——**断连不丢会话上下文，重连自动补消息**。一人一 Bot 的极简移动端接入方案。
>
> 适用 AstrBot ≥ 4.25.5。

## 它解决什么

| 痛点 | webchat | BotAPI |
|:--|:--|:--|
| 手机切后台断连 | WebSocket 被 OS 杀，session 结束 | SSE 自动重连 + session 绑定 token（不依赖连接） |
| 重连后历史 | 新 session 全丢 | `GET /history?since=<id>` 补全漏掉的消息 |
| 弱网 | TCP 超时触发清理 | REST 消息是离散请求，发完即成功 |
| 流式回复 | 需 WS 双向 | SSE 单向长连，原生支持逐 token 流式 |

## 架构

```
手机 App ──REST+SSE──► BotAPI 适配器插件（AstrBot）
                         │
                         ├─ BotApiAdapter(Platform)  跑 HTTP 服务(端口可配) + SSE 回流
                         │   └─ BotApiMessageEvent 重写 send/send_streaming 推 SSE
                         ├─ BotApiStar(Star)        持 context，注册管理 API + 注入 managers
                         └─ RuntimeState 单例       跨 Platform↔Star 共享状态
                                   │
                                   ▼
                         AstrBot ConversationManager（SQLite，唯一历史真相源）
```

关键设计：
- **Session 与连接解耦**：token 绑定 session，不依赖 SSE 连接状态。断连重连同 token 即续上。
- **纯 SSE 回复**：`POST /message` 只返回 `message_id`，所有回复（含首条）经 `/stream` SSE 推送。
- **逐 token 流式**：入站时 `set_extra("enable_streaming", True)`，`send_streaming` 逐片段推 `thinking` / `message(streaming)` / `message(final)`。
- **断连补消息**：每条文本消息镜像写入 `platform_message_history` 表（稳定自增 int id），重连 `?since=<id>` 补拉。
- **文本持久化、媒体不入库**：服务端只持久化文本（含 thinking/工具状态）；图片/音频/文件仅 SSE 推送一次（单次有效 URL），App 本地缓存。

## 安装

### 方式一：zip 安装

1. 下载 [release zip](https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases)，解压到 AstrBot 的 `data/plugins/`：
   ```bash
   unzip astrbot_plugin_botapi.zip -d /path/to/AstrBot/data/plugins/
   # 生成 data/plugins/astrbot_plugin_botapi/（含 main.py + metadata.yaml + pages/）
   ```
2. 重启 AstrBot。日志应见 `Platform adapter registered: botapi`。
3. WebUI → **插件管理** → 确认 `astrbot_plugin_botapi` 已加载。
4. WebUI → **机器人/平台** → 新增 → 选 type `botapi` → 填配置 → **启用**（`enable` 默认 false，须手动启用）。

### 方式二：git clone

```bash
cd /path/to/AstrBot/data/plugins/
git clone https://github.com/zzttzzmyswy/astrbot_plugin_botapi.git
```
然后同上重启 + 启用。

## 配置

WebUI「机器人/平台」编辑 botapi 实例：

| 字段 | 默认 | 说明 |
|:--|:--|:--|
| `host` | `0.0.0.0` | 监听地址 |
| `port` | `9000` | 手机 API 端口（nginx 反代） |
| `tokens` | `[]` | 允许的 Token 列表（**空则允许所有非空 token**）；每个 token = 一个账户，自动隔离会话 |
| `nicknames` | `{}` | `{token: 昵称}`，仅管理页展示用，不注入对话 |
| `enable` | `false` | **须手动启用** |

另需在 AstrBot 全局配置设 **`callback_api_base`**（仪表盘外部可达地址，如 `http://your-host:6185`）——媒体 URL 依赖它；不配则媒体功能降级（文本不受影响）。

> **多账户**：一个 botapi 实例 + `tokens` 填多个即可，一个端口服务所有账户，每个 token 自动隔离会话/历史/SSE。不要建多个 botapi 实例（每个是独立 Quart，不能共享端口）。

## 手机端接口

完整接口文档见 **[docs/API.md](docs/API.md)**。速览：

| 端点 | 方法 | 说明 |
|:--|:--|:--|
| `/api/v1/botapi/auth` | POST | Token 认证 → `{user_id, session_id}` |
| `/api/v1/botapi/message` | POST | 发消息 → `{message_id}`（纯 SSE，回复走 /stream） |
| `/api/v1/botapi/upload` | POST multipart | 上传文件 → `{file_id, name, mime_type, size}` |
| `/api/v1/botapi/stream?since=` | GET | SSE 流，事件类型 `message` / `thinking` / `error` / `ping` |
| `/api/v1/botapi/history` | GET | 拉历史 / 断连补消息 |

## 管理页

WebUI → 插件管理 → `astrbot_plugin_botapi` → **Dashboard**：
- 账户列表（昵称 / Token 预览 / hash / 在线 / 消息数 / SSE 连接数 / 最后活跃）
- 新增账户（可填昵称 + 自动生成 token）
- 改名 / 删除 / 强制断开 / 清空历史
- 统计卡片（总账户 / 在线 / 总消息数）

## Nginx 反代（SSE 必须）

```nginx
# BotAPI 手机 API + SSE
location /api/v1/botapi/ {
    proxy_pass http://127.0.0.1:9000;
    proxy_http_version 1.1;
    proxy_buffering off;          # SSE 必须关缓冲
    proxy_cache off;
    proxy_read_timeout 86400s;    # 24h 不超时
    proxy_send_timeout 86400s;
    chunked_transfer_encoding on;
}
# 媒体 URL（走仪表盘文件服务 /api/file/<token>，免认证但单次有效）
location /api/file/ { proxy_pass http://127.0.0.1:6185; }
# 仪表盘 + 管理 API
location / { proxy_pass http://127.0.0.1:6185; }
```
对公只开 443，9000/6185 仅本地监听。

## 自检

仓库 `scripts/selfcheck.sh`：
```bash
./scripts/selfcheck.sh --base http://localhost:9000 --token YOUR_TOKEN
./scripts/selfcheck.sh --base https://your.domain --token YOUR_TOKEN --msg "你好"   # 含收发(需 LLM)
```

## 已知限制（设计取舍）

- **工具事件**：结构化 `tool_call`/`tool_result` 对非 webchat 平台不可达，降级为 `message` + `subtype:"tool_status"` 文本（受 `show_tool_use` 控制）。
- **媒体 URL**：走仪表盘 `/api/file/<token>`，**单次有效 + 默认 300s 过期**，App 须收到即下载缓存；服务端不持久化媒体，历史不回放媒体。
- **流式依赖 provider**：BotAPI per-request 强制流式（`set_extra("enable_streaming", True)`），但 provider 端需支持 streaming_response。
- **历史分页**：`/history` 基于 `platform_message_history` 表（最多取最近 200 条），`since` 早于窗口时仅返回窗口内。

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 开发

```bash
git clone https://github.com/zzttzzmyswy/astrbot_plugin_botapi.git
cd astrbot_plugin_botapi
python -m venv .venv --system-site-packages   # 继承系统 astrbot 依赖
.venv/bin/pip install pytest-asyncio
.venv/bin/python -m pytest -q                  # 62 个测试
```

## License

[MIT](LICENSE)。

> 注：AstrBot 本体为 AGPL-3.0。本插件按 MIT 发布；如需与 AstrBot 的 copyleft 完全一致，可改用 AGPL-3.0（替换 LICENSE 即可）。
```

- [ ] **Step 2: 校验 release 链接为绝对 URL**

Run: `grep -n 'releases)' README.md`
Expected: 一行，含 `https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases`，**无** `../../releases`。

- [ ] **Step 3: 校验无 superpowers 引用**

Run: `grep -n 'superpowers\|三轮核实\|设计 spec' README.md`
Expected: 无输出（退出码 1）。

- [ ] **Step 4: 校验更新日志节存在**

Run: `grep -n '## 更新日志' README.md`
Expected: 一行匹配。

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: README 按用户动线重构 + 修 release 链接 + 删 superpowers 引用"
```

---

## Task 2: 校对修复 docs/API.md

**Files:**
- Modify: `docs/API.md`

5 处 drift 修复，逐处 Edit。

- [ ] **Step 1: Base URL 补直连说明（§开头）**

把：
```
- **Base URL**：`https://<your-domain>/api/v1/botapi`（经 nginx 反代到适配器端口，默认 9000）
```
改为：
```
- **Base URL**：`https://<your-domain>/api/v1/botapi`（经 nginx 反代到适配器端口，默认 9000）；也可直连 `http://<host>:9000`，端点路径相同（`selfcheck.sh` 即直连）。
```

- [ ] **Step 2: `/auth` 401 形态补注（§1）**

把 §1 的：
```
**响应 401**：
```json
{ "error": "invalid_token" }
```
```
改为：
```
**响应 401**：
```json
{ "error": "invalid_token" }
```
> 注意：`/auth` 的 401 形态是 `{"error":"invalid_token"}`（无 `code`）；其余端点的 401 由鉴权中间件统一返回 `{"error":"unauthorized","code":"INVALID_TOKEN"}`（见 [§8](#8-错误码)）。
```

- [ ] **Step 3: `since` 语义澄清（§4）**

把 §4 的：
```
- `since`（可选）：从指定消息 ID **之后**补推漏掉的消息（断连补消息）。App 跟踪收到的最大 id，重连时带上。
```
改为：
```
- `since`（可选）：从指定消息 ID **之后**补推漏掉的消息（断连补消息）。**此 ID 是 `platform_message_history` 表的整数行 id**（即 `/history` 返回的 `message_id`，或上次 catchup 回放事件携带的 id）。注意：live 实时 `message` 事件携带的 `message_id` 形如 `botapi_xxx`，**不能**作为 `since`——服务端会以 `int(since)` 解析，传 `botapi_*` 会报错。App 应通过 `/history?since=0` 取得整数游标后再带入 `since`。
```

- [ ] **Step 4: `event: error` 改用真实码（§6.3）**

把 §6.3 的：
```
### event: error

错误。

```json
{ "code": "RATE_LIMITED", "message": "请求过于频繁，请稍后重试" }
```
```
改为：
```
### event: error

错误。当前仅一种：

```json
{ "code": "SESSION_KICKED", "message": "管理员已断开此会话" }
```

> `SESSION_KICKED` 在管理员于管理页"强制断开"该会话时推送。`RATE_LIMITED` / `PUSH_FAILED` 暂未实现（保留）。
```

- [ ] **Step 5: 错误码表对齐代码（§8）**

把 §8 整表：
```
| HTTP | code | 说明 |
|:--|:--|:--|
| 401 | `INVALID_TOKEN` | token 无效/被禁用 |
| 400 | `no_file` | 上传未带文件 |
| - | `RATE_LIMITED` | 请求过频（SSE error 事件） |
| - | `SESSION_KICKED` | 管理员强制断开（SSE error 事件） |
| - | `PUSH_FAILED` | SSE 推送异常 |
```
改为：
```
| HTTP | code / error | 说明 |
|:--|:--|:--|
| 401 | `{"error":"invalid_token"}` | `/auth` 专用：token 无效/被禁用（无 code） |
| 401 | `INVALID_TOKEN` | 其余端点：鉴权中间件返回 `{"error":"unauthorized","code":"INVALID_TOKEN"}` |
| 400 | `no_file` | `/upload` 未带文件 |
| - | `SESSION_KICKED` | SSE error 事件：管理员强制断开该会话 |
```

- [ ] **Step 6: §9 流程示例 prose 修正**

把 §9 的：
```
# 5. 断连补消息：记下 final 的 message_id，断开 /stream，重连 ?since=<id>
curl -N "$BASE/stream?since=2" -H "Authorization: Bearer $TOKEN"
```
改为：
```
# 5. 断连补消息：先用 /history 取整数游标，断开 /stream 后重连 ?since=<整数 id>
curl -N "$BASE/stream?since=2" -H "Authorization: Bearer $TOKEN"
```

并把 §9 末尾"App 端最小实现"第 4 步：
```
4. 切后台断开 /stream；回前台重连 `?since=<最大 id>` 补漏。
```
改为：
```
4. 切后台断开 /stream；回前台先 `GET /history?since=0` 取最新整数 id，再 `?since=<该 id>` 重连补漏（注意 `since` 是整数行 id，非 `botapi_*`）。
```

- [ ] **Step 7: 校验无未实现错误码残留**

Run: `grep -n 'RATE_LIMITED\|PUSH_FAILED' docs/API.md`
Expected: 仅 Step 4 新增的"暂未实现"句中出现 `RATE_LIMITED` / `PUSH_FAILED` 各一次（作为说明文本），**错误码表 §8 中不出现**。再单独核对 §8：`grep -A6 '## 8. 错误码' docs/API.md` 不含这两码。

- [ ] **Step 8: 校验 since 整数说明到位**

Run: `grep -n 'int(since)\|整数行 id' docs/API.md`
Expected: §4 与 §9 各有匹配。

- [ ] **Step 9: Commit**

```bash
git add docs/API.md
git commit -m "docs: API.md 校对——since 整数语义、错误码对齐代码、补直连说明"
```

---

## Task 3: 新增 CHANGELOG.md

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: 写入完整内容**

```markdown
# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.1.2] - 2026-06-24

### Changed

- 梳理项目文档结构：README 按用户动线重排，砍与 API.md 重叠的 SSE 字段细节。
- 移除内部开发过程产物（`docs/superpowers/` 下的 spec 与 plan）。
- 统一 AstrBot 版本声明为 `>=4.25.5`（metadata + README 一致）。
- 校对 `docs/API.md`：澄清 `since` 整数行 id 语义、错误码对齐代码（移除未实现的 `RATE_LIMITED`/`PUSH_FAILED`）、补直连说明。

### Added

- 新增 `CHANGELOG.md`。

## [1.1.1] - 2026-06-24

### Fixed

- 管理页"改名/删除"按钮无响应：iframe sandbox 无 `allow-modals`，原生 `confirm`/`prompt`/`alert` 全被拦截。改用页内模态（`confirmDialog`/`promptDialog`/`toast`）。

## [1.1.0] - 2026-06-24

### Fixed

- 管理页按钮改事件委托，刷新按钮加"刷新中…"可见反馈。

### Changed

- 账户昵称/备注上线（仅管理页展示，不注入对话上下文）。
- 加入 `scripts/selfcheck.sh` 自检脚本。

## [1.0.0] - 2026-06-24

### Added

- BotAPI 适配器插件首个可用版本：`/auth` `/message` `/upload` `/stream` `/history` 五端点，纯 SSE 回复，逐 token 流式，断连重连自动补消息，多账户隔离，Dashboard 管理页。
- 完整手机端 API 文档 `docs/API.md`。

[Unreleased]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/compare/v1.1.2...HEAD
[1.1.2]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.2
[1.1.1]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.1
[1.1.0]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.0
[1.0.0]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.0.0
```

- [ ] **Step 2: 校验五节齐备**

Run: `grep -n '^## \[' CHANGELOG.md`
Expected: 5 行：`[Unreleased]`、`[1.1.2]`、`[1.1.1]`、`[1.1.0]`、`[1.0.0]`。

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: 新增 CHANGELOG.md（Keep a Changelog）"
```

---

## Task 4: bump metadata.yaml

**Files:**
- Modify: `metadata.yaml`

- [ ] **Step 1: 改 version 与 astrbot_version**

把 `version: 1.1.1` 改为 `version: 1.1.2`；把 `astrbot_version: ">=4.25.0"` 改为 `astrbot_version: ">=4.25.5"`。最终文件：

```yaml
name: astrbot_plugin_botapi
desc: BotAPI 自定义移动端适配器 — 一人一 Bot 极简移动端接入，支持弱网断连恢复。
version: 1.1.2
author: zzttzzmyswy
repo: https://github.com/zzttzzmyswy/astrbot_plugin_botapi
astrbot_version: ">=4.25.5"
```

- [ ] **Step 2: 校验**

Run: `grep -E 'version:|astrbot_version:' metadata.yaml`
Expected:
```
version: 1.1.2
astrbot_version: ">=4.25.5"
```

- [ ] **Step 3: Commit**

```bash
git add metadata.yaml
git commit -m "chore: bump 1.1.2 + astrbot_version >=4.25.5"
```

---

## Task 5: 删除 docs/superpowers/ 过程产物

**Files:**
- Delete: `docs/superpowers/specs/2026-06-24-botapi-astrbot-plugin-design.md`
- Delete: `docs/superpowers/plans/2026-06-24-botapi-astrbot-plugin.md`
- Delete: `docs/superpowers/specs/2026-06-24-botapi-docs-cleanup-design.md`（本任务的设计 spec）
- Delete: `docs/superpowers/plans/2026-06-24-botapi-docs-cleanup.md`（**本计划自身**——此时所有任务已执行完，删除安全）

> ⚠️ 本步骤会删除你正在读的这个计划文件。所有 Task 1–4、6 已完成后再执行。删除后 Task 6 的校验仍可凭已记录的命令运行。

- [ ] **Step 1: git rm 整个 superpowers 目录**

```bash
git rm -r docs/superpowers
```
Expected: 删除 4 个文件（2 旧 spec/plan + 本轮 spec/plan），无报错。

- [ ] **Step 2: 校验 docs/ 仅剩 API.md**

Run: `ls docs/`
Expected: 仅 `API.md`。

Run: `git ls-files docs/`
Expected: `docs/API.md` 一行。

- [ ] **Step 3: Commit**

```bash
git commit -m "docs: 移除内部开发过程产物（spec/plan），docs/ 仅留 API.md"
```

---

## Task 6: 全局校验

**Files:** 无修改，仅校验。

- [ ] **Step 1: 源码未被误改（sanity）**

Run: `git status --short && git diff --stat HEAD~5 -- '*.py' pages/ tests/ scripts/`
Expected: `git status` 干净（无未提交）；diff stat 对 `.py`/`pages/`/`tests/`/`scripts/` **无输出**（本次未动代码）。

- [ ] **Step 2: 测试仍全绿（确认未误碰源码）**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -5`
Expected: `62 passed`（或当前测试数）。若失败，说明误改了源码，回退检查。

- [ ] **Step 3: 文档间链接自洽**

Run: `grep -n 'CHANGELOG.md\|docs/API.md\|LICENSE' README.md`
Expected: 三条相对链接均存在。

Run: `grep -n 'superpowers' README.md docs/API.md CHANGELOG.md metadata.yaml`
Expected: 仅 CHANGELOG.md 的 `[1.1.2]` 条目出现一次（"移除……docs/superpowers/"），其余文件无匹配。

- [ ] **Step 4: 版本号一致**

Run: `grep 'version' metadata.yaml && grep -o '1\.1\.2' CHANGELOG.md | head -1`
Expected: metadata `version: 1.1.2`，CHANGELOG 含 `1.1.2`。

- [ ] **Step 5: 推送**

```bash
git push origin main
```
Expected: 推送成功，无 reject。

---

## Task 7: 打 tag v1.1.2 + 发 GitHub release

- [ ] **Step 1: 打 tag**

```bash
git tag -a v1.1.2 -m "v1.1.2: 文档梳理与修复"
git push origin v1.1.2
```
Expected: tag 推送成功。

- [ ] **Step 2: 发 release（notes 取 CHANGELOG [1.1.2] 节）**

```bash
gh release create v1.1.2 --title "v1.1.2" --notes "$(cat <<'EOF'
## Changed

- 梳理项目文档结构：README 按用户动线重排，砍与 API.md 重叠的 SSE 字段细节。
- 移除内部开发过程产物（`docs/superpowers/` 下的 spec 与 plan）。
- 统一 AstrBot 版本声明为 `>=4.25.5`（metadata + README 一致）。
- 校对 `docs/API.md`：澄清 `since` 整数行 id 语义、错误码对齐代码、补直连说明。

## Added

- 新增 `CHANGELOG.md`。

完整更新日志见 [CHANGELOG.md](https://github.com/zzttzzmyswy/astrbot_plugin_botapi/blob/main/CHANGELOG.md)。
EOF
)"
```
Expected: 输出 release URL `https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.2`。

- [ ] **Step 3: 校验 release**

Run: `gh release view v1.1.2 --json url -q .url`
Expected: 上述 URL。

---

## Self-Review（已执行）

**Spec 覆盖：** spec §3 README 重构 → Task 1；§4 API.md 4 项 → Task 2 Step 1-6（Base URL/直连、auth 401 注、since 语义、error 真实码、错误码表、§9 prose）；§5 CHANGELOG → Task 3；§6 metadata → Task 4；§7 删除清单 → Task 5；§8 验收 → Task 6；D6 发版 → Task 7。无遗漏。

**Placeholder 扫描：** 各步均含完整新文本或精确命令，无 TBD/TODO。

**类型一致：** `since` 整数语义在 Task 2 Step 3/6 与 Task 6 Step 3 校验口径一致；错误码 `SESSION_KICKED` 在 Task 2 Step 4/5 与权威事实一致；版本 `1.1.2` 在 Task 3/4/6/7 一致。
