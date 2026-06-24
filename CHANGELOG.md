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
