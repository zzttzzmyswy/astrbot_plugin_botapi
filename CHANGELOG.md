# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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

[Unreleased]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/compare/v1.2.3...HEAD
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
