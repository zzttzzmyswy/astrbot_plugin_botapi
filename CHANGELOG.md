# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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

## [3.0.2] - 2026-08-05

### Changed

- **绑定交给机器人/平台配置**：删除插件 Web 后台的「绑定/解绑」功能与 `GET platforms`
  端点；绑定关系由目标机器人（平台）配置的 `tokens` 列表承载（token 出现在哪个平台
  条目的 tokens，会话即路由到该平台 LLM 配置）。账户注册表（`tokens`）仍由插件后台
  维护；启动自动把存量插件配置 `bindings` 迁移进目标平台 tokens 后移除。
- 删除账户时仍会从所有平台 tokens 移除该 token（联动清绑定）。



### 修复

- **升级后账户丢失（v3.0.0 回归）**：账户数据此前存在 botapi 平台条目，WebUI 保存一次
  平台配置即被 AstrBot `update_bot` 整体覆盖 → tokens 清空。账户数据（tokens/bindings/
  sessions）迁入插件配置 `astrbot_plugin_botapi_config.json`（全局），botapi 平台条目
  清空为路由占位；启动自动迁移存量平台条目账户。

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
- **账户绑定状态展示**：`stats` 与 `accounts` 响应每条账户新增 `bound_platform` 字段，
  管理页以徽标显示当前绑定平台。

### Changed

- **auth 严格化**：token 必须显式存在于 botapi 平台条目的 `tokens` 列表（空=拒连）。
- 移除账户昵称/备注（`nicknames`）字段与改名功能；绑定关系不再存独立的
  `botapi_bindings` 映射（启动时自动迁移到目标平台 tokens 列表）。

### 兼容性

- 老客户端/App 无需改动（HTTP API 不变）；绑定表为空时行为不变（默认路由）。
- 存量 `botapi_bindings` 配置启动时自动迁移；未在 botapi tokens 里的旧 token 需
  重新加入（auth 严格化）。

## [2.0.2] - 2026-08-04

### 修复

- **会话 id 双重前缀**：修复服务端报告的会话 id 出现 `botapi:FriendMessage:botapi:FriendMessage:{token}`
  双重前缀的问题。`submit_inbound` 传给 AstrMessageEvent 的 session_id 由完整 umo 改为裸 scoped key
  （`AstrMessageEvent` 会自行拼接 `{pid}:FriendMessage:` 前缀）。此问题会导致会话上下文路由错乱、
  管理页 Session ID 显示错误。

## [2.0.1] - 2026-08-04

### 修复

- **管理页对话历史**：改为全量重绘消息列表（以服务端为准重建 DOM），修复
  增量去重 Set 无限增长与 50 条窗口滑动导致的「旧消息残留 + 新头被误判重复跳过」漂移。
- **跳过空气泡**：`get_conversation_messages` 与前端都跳过空内容条目（工具调用帧等
  assistant 空 content 不再渲染成空 bot 气泡）。
- **轮询失败自停**：历史拉取连续 3 次失败后停止轮询并提示，避免会话被删/网络中断时无限空转。
- 历史拉取 limit 提升至 200（服务端封顶 200）。

## [2.0.0] - 2026-08-04

### 新增

- **多会话支持**：账户级多会话管理（单账户多会话），完整会话 CRUD 操作（创建/删除/修改/重命名）。
- **会话管理 API**：Web 管理页新增会话下钻功能，可管理和切换账户的多个会话。
- **Phone API 会话参数**：手机端 `/sessions` 端点查询会话列表，`/message` 等端点支持 `session_id` 参数指定会话。
- **跨设备同步**：服务端权威的会话状态同步，支持多设备协同操作同一账户的会话。

### 兼容性

- **默认会话向后兼容**：老客户端在无会话概念下仍可正常工作，自动映射至默认会话。
- **老客户端兼容**：未升级的客户端继续使用单会话模式，新旧客户端共存。

## [1.3.2] - 2026-07-04

### 新增

- **分块上传**:大文件(>10MB)切 5MB/块逐个 POST `/upload/chunk`,最后
  `/upload/complete` 合并。绕过服务端 ASGI/反代 ~90s 的请求超时(50MB 单次
  上传会 408)。配合 `MAX_CONTENT_LENGTH=200MB` 与 nginx `client_max_body_size`。

## [1.3.1] - 2026-06-29

### Fixed

- 手机端拉取历史时较早的消息被盖上最新时间、排到真正最新记录下方：根因是 SSE 重连补漏（`_stream_gen` 的 `catchup_events`）把历史行（含较早消息）经 SSE 重发给 client，client 用本地 `now()` 存 `created_at`、丢弃事件自带的 `timestamp`，导致重放的较早历史显示成最新时间。`_stream_gen` 不再经 SSE 回放历史，`since` 参数保留兼容 client URL 但不再回放。历史补漏统一走 `/history` 端点（`row_to_sse` 带真实 `timestamp`+`role`，client `mergeHistory` 正确落库），不丢消息、时间正确。

### Note

- 重连后错过的消息改由 client 的 60s 周期对齐 / 回复后补漏 / 回前台全量同步（均走 `/history`）补齐，最迟 60s 显示，时间正确。
- `history.catchup_events` 函数保留（本身正确），未来 client 改用事件自带 `timestamp` 后可重新启用以降低重连补漏延迟。

## [1.3.0] - 2026-06-28

### Fixed

- 管理页直接对话看不到任何消息（v1.2.0 起一直空白）：根因是 `platform_message_history` 表在部分 AstrBot 4.26 环境的仪表盘插件页请求上下文下，`message_history_manager.insert` 调了不抛错但不落表（直接 insert 同样 0 行，DB 层问题，插件无法修复）。管理页历史改读 `conversation_manager`（LLM 真实对话上下文，与 stats 同源、稳定可用），不再依赖 platform_message_history。

### Changed

- 管理页 `sessions/<hash>/history` 数据源由 `platform_message_history` 改为 `conversation_manager`。
- 前端按 `(role, content)` 去重追加，避免对话被 LLM 截断/重排时索引漂移导致重复或丢失。
- 移除 v1.2.3–v1.2.6 的诊断日志与 `_diag` 字段。

### Note

- 手机端 `/history`（端口 9000）仍读 `platform_message_history`；若该环境同样不落表，手机端历史/断连补消息也会空。后续可同样改读 conversation_manager，但会改变手机端 API 语义（历史变为 LLM 对话视图，不再含 thinking/tool_status 行），需评估。

## [1.2.6] - 2026-06-28

### Changed

- 直接对话诊断：`_do_chat` 里绕过 submit_inbound 直接调 `message_history_manager.insert` 插一行测试数据，隔离 persist 链路 vs DB 层。diag 新增 `rows_after_direct` / `direct_err`。

## [1.2.5] - 2026-06-28

### Changed

- 直接对话诊断再增强：前端把 `_diag` 打成字符串（不用展开），后端 `_insert` 加服务端日志（no-op 时 WARNING、插入时 INFO 带 platform_id/user_id）。定位 persist 为何没把消息写进 platform_message_history。

## [1.2.4] - 2026-06-28

### Changed

- 直接对话诊断增强：`_do_history` / `_do_chat` 响应加 `_diag`（`platform_id` / `target` / `mgr_set` / `rows_after_send`），前端 console 直接可见。定位为何历史返回 0 行（stats 的 417 来自 conversation_manager，与 platform_message_history 是两张表）。

## [1.2.3] - 2026-06-28

### Changed

- 管理页直接对话加诊断日志：`openChat` / `loadHistory` / `pollOnce` / `sendChat` 的响应与错误全部打印到 console。用于定位 v1.2.2 后仍看不到消息的根因（是调用没触发、响应回不来、还是返回空）。

## [1.2.2] - 2026-06-28

### Fixed

- 管理页直接对话仍看不到历史 / 收发消息（v1.2.1 未根治）：根因是 bridge `apiGet(endpoint, params)` 带 query 参数的回复路径在 sandbox iframe（null-origin）下触发父外壳 `postMessage` target origin `'null'` 失败，请求发出去但响应回不来。改用 `apiPost(endpoint, body)`（与 export / chat 同构，已验证可用）：`sessions/<hash>/history` 由 GET 改 POST，`since` / `limit` 走 body。

## [1.2.1] - 2026-06-28

### Fixed

- 管理页直接对话加载历史/轮询失败（"Plugin bridge endpoint is invalid"）：`bridge.apiGet(endpoint, params)` 的 query 须走 `params` 参数，误把 `?limit=`/`?since=` 拼进 endpoint 字符串导致端点不匹配。改为 `{ limit }` / `{ since }` 传参。

## [1.2.0] - 2026-06-28

### Added

- 管理页直接对话：账户行「对话」按钮进入整页聊天视图，admin 以该账户身份在同一会话发话（与手机端共享上下文/历史），轮询历史收回复（final / thinking / tool_status），不碰 SSE。手机端会实时收到 admin 发起的回复（同一会话固有行为）。
- 后端 `submit_inbound` 共享 helper：手机 `/message` 与管理页 `/chat` 注入逻辑统一，避免双份漂移。

## [1.1.5] - 2026-06-25

### Fixed

- 兼容 AstrBot 4.26.0：`astrbot.dashboard.routes.route.Response` 已移除，4.26 改用 `astrbot.dashboard.responses` 的 `ok()`/`error()` 函数。`main.py` 加兼容 shim（4.26+ 走新函数、4.25.x 回退旧类），调用形式不变，故 `astrbot_version` 维持 `>=4.25.5`。

## [1.1.4] - 2026-06-25

### Fixed

- 历史消息时间戳早一个时区：`row_to_sse` 的 `int(row.created_at.timestamp())` 对 SQLite 读回的 naive datetime（`PlatformMessageHistory.created_at` 按 UTC 存但落库丢 `+00:00`）按服务器本地时区解释，导致非 UTC 服务器上 `/history` 与 `/stream` catchup 的 `timestamp` 偏一个时区（北京服务器早 8h）。改为 naive 时显式补 UTC。

## [1.1.3] - 2026-06-25

### Added

- 管理页历史记录导出：Markdown / JSON 两种格式，完整历史无条数上限（分页累加），Blob 下载。

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

[Unreleased]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/compare/v3.0.3...HEAD
[3.0.3]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v3.0.3
[3.0.2]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v3.0.2
[3.0.1]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v3.0.1
[3.0.0]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v3.0.0
[2.0.2]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v2.0.2
[2.0.1]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v2.0.1
[2.0.0]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v2.0.0
[1.3.1]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.3.1
[1.3.0]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.3.0
[1.2.6]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.2.6
[1.2.5]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.2.5
[1.2.4]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.2.4
[1.2.3]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.2.3
[1.2.2]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.2.2
[1.2.1]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.2.1
[1.2.0]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.2.0
[1.1.5]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.5
[1.1.4]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.4
[1.1.3]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.3
[1.1.2]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.2
[1.1.1]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.1
[1.1.0]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.0
[1.0.0]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.0.0
