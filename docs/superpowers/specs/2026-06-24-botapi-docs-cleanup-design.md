# BotAPI 文档梳理与修复 — 设计

> 日期：2026-06-24
> 范围：仅文档（README、docs/API.md、新增 CHANGELOG.md、metadata.yaml 版本字段）。不改任何 `.py`/`pages/`/`tests/`/`scripts/`。
> 目标：重新梳理文档结构，修复既有错误，删除面向内部过程的无关内容，按语义化版本发 `1.1.2`。

## 1. 背景与问题

当前文档存在以下问题：

1. **版本不一致**：`README.md` 写"适用 AstrBot ≥ 4.25.5"，`metadata.yaml` 写 `astrbot_version: ">=4.25.0"`。实际仅对照 AstrBot 4.25.5 源码核实（`register_platform_adapter` 装饰器签名、`RespondStage` `send`/`send_streaming` 调用点、`set_extra("enable_streaming", True)`、`Context.register_web_api`），未测更低版本。
2. **release 链接坏**：`README.md` L42 `../../releases` 相对路径在 GitHub 仓库根渲染时失效。
3. **内部过程产物混入仓库**：`docs/superpowers/specs/2026-06-24-botapi-astrbot-plugin-design.md`（设计 spec）与 `docs/superpowers/plans/2026-06-24-botapi-astrbot-plugin.md`（19 任务 TDD 计划）是 brainstorming/planning 阶段产物，对插件终端用户与二次开发者均为噪声。`README.md` L141 还引用了该 spec。
4. **无更新日志**：仓库内无 `CHANGELOG.md`，但 GitHub 上已有 `v1.1.0` / `v1.1.1` 两个 release，本地 28 条 commit。
5. **README 与 API.md 职责重叠**：README"手机端接口"节列了 SSE 事件字段细节，与 `docs/API.md` §6/§7 重复。

## 2. 决策（已与用户确认）

| # | 决策 | 选择 |
|:-:|:--|:--|
| D1 | `docs/superpowers/` 内部 spec/plan | **删除**（连同空目录） |
| D2 | AstrBot 版本声明 | 统一为 `>=4.25.5`（metadata + README 一致） |
| D3 | 更新日志组织 | 独立 `CHANGELOG.md`，Keep a Changelog 1.1.0 格式 |
| D4 | 文档梳理深度 | 方案 B：轻度重构 + 修复（按用户动线重排，砍重叠） |
| D5 | 管理 API 是否写进手机端 API.md | 不写（管理页走 AstrBot bridge 信封，非手机端 HTTP） |
| D6 | 本次发版 | 发 `1.1.2`：metadata bump + CHANGELOG 加 `[1.1.2]` + git tag + GitHub release |

## 3. README.md 重构

### 3.1 章节顺序（重排后）

```
# BotAPI 移动端适配器（AstrBot 插件）
> 一句话定位；适用 AstrBot ≥ 4.25.5
## 它解决什么          （原"为什么不是 webchat/Matrix"，精简为 3–4 行对比表）
## 架构                （保留现有 ASCII 图，文字精简，去过度实现细节）
## 安装                （zip / git clone；release 链接改绝对 URL）
## 配置                （字段表 + 多账户说明 + callback_api_base 提醒）
## 手机端接口          （仅端点速览表 + 一句事件枚举 → 链 docs/API.md）
## 管理页              （功能 bullet）
## Nginx 反代          （SSE 必须的配置块，保留）
## 自检                （selfcheck.sh 用法，保留）
## 已知限制            （4 条设计取舍，精简措辞）
## 更新日志            （链 CHANGELOG.md）  ← 新增
## 开发                （venv + pytest；删除 superpowers spec 引用）
## License
```

### 3.2 具体修改

- **新增** "## 更新日志" 节：`见 [CHANGELOG.md](CHANGELOG.md)。`
- **手机端接口** 节：删除 SSE 事件字段细节（`streaming`/`final`/`segment_end`/`subtype` 等说明），只保留 5 端点速览表 + 一句"事件类型：`message`/`thinking`/`error`/`ping`，详见 API.md"。细节全交 `docs/API.md`。
- **安装** 节 L42：`../../releases` → `https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases`。
- **开发** 节：删除 L141 `设计 spec：docs/superpowers/specs/…（已对照 AstrBot 4.25.5 源码三轮核实）。` 整行。"62 个测试"保留。
- **版本** 头部与配置节统一 `≥ 4.25.5`（头部已是，配置节无版本字样，无需改）。
- **措辞精简**：架构节、已知限制节去掉冗余修饰，不删信息点。

## 4. docs/API.md 修复

不重写，校对 + 小修：

1. **校对 drift**：逐端点核对 `/auth` `/message` `/upload` `/stream` `/history` 的请求/响应字段与 `routes.py` `adapter.py` `models.py` 一致。重点核：`message_id` 前缀 `botapi_`、`session_id` 形态 `botapi:FriendMessage:<token>`、错误码集合 `{INVALID_TOKEN, no_file, RATE_LIMITED, SESSION_KICKED, PUSH_FAILED}` 是否真实存在于代码。
2. **Base URL**：补一句"也可直连 `http://host:port`（不经 nginx，端点路径相同）"——`selfcheck.sh` 即直连 9000。
3. **历史 ID 类型**：明确 `since`/`before` 为 `platform_message_history` 表的 stable int id（字符串化），与 §9 示例 `?since=2` 对齐，避免 App 误用 `botapi_*` ID 查历史。
4. **管理 API 不入此文档**（D5）。

不改：§6 SSE 事件类型、§7 聚合约定、§9 完整流程示例。

## 5. CHANGELOG.md（新增）

Keep a Changelog 1.1.0 格式，倒序，中文措辞与 commit 一致。三版本同日（2026-06-24），如实标注。

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
### Added
- 新增 `CHANGELOG.md`。

## [1.1.1] - 2026-06-24
### Fixed
- 管理页"改名/删除"按钮无响应：iframe sandbox 无 `allow-modals`，
  原生 `confirm`/`prompt`/`alert` 全被拦截。改用页内模态
  （`confirmDialog`/`promptDialog`/`toast`）。

## [1.1.0] - 2026-06-24
### Fixed
- 管理页按钮改事件委托，刷新按钮加"刷新中…"可见反馈。
### Changed
- 账户昵称/备注上线（仅管理页展示，不注入对话上下文）。
- 加入 `scripts/selfcheck.sh` 自检脚本。

## [1.0.0] - 2026-06-24
### Added
- BotAPI 适配器插件首个可用版本：`/auth` `/message` `/upload`
  `/stream` `/history` 五端点，纯 SSE 回复，逐 token 流式，
  断连重连自动补消息，多账户隔离，Dashboard 管理页。
- 完整手机端 API 文档 `docs/API.md`。
```

底部版本链接引用（Keep a Changelog 惯例的 `[1.1.2]: https://github.com/zzttzzmyswy/astrbot_plugin_botapi/releases/tag/v1.1.2` 等）在发版后补全。

## 6. metadata.yaml

```yaml
name: astrbot_plugin_botapi
desc: BotAPI 自定义移动端适配器 — 一人一 Bot 极简移动端接入，支持弱网断连恢复。
version: 1.1.2          # 1.1.1 → 1.1.2
author: zzttzzmyswy
repo: https://github.com/zzttzzmyswy/astrbot_plugin_botapi
astrbot_version: ">=4.25.5"   # 4.25.0 → 4.25.5
```

## 7. 删除清单

- `git rm docs/superpowers/specs/2026-06-24-botapi-astrbot-plugin-design.md`
- `git rm docs/superpowers/plans/2026-06-24-botapi-astrbot-plugin.md`
- 移除空目录 `docs/superpowers/`（git rm 后自然消失）
- `README.md` L141 spec 引用行删除
- 本设计 spec 与实施 plan 本身也是过程产物，实施完成后随 `docs/superpowers/` 一并移除（不随仓库发布）

**不动**：`scripts/selfcheck.sh`、`LICENSE`、`pyproject.toml`、`conftest.py`、`tests/`、所有 `.py`、`pages/` 三件套。

## 8. 验收

- `README.md` 渲染无坏链（release 链接绝对 URL、CHANGELOG 链接相对路径有效）。
- `metadata.yaml` `version=1.1.2`、`astrbot_version=">=4.25.5"`。
- `CHANGELOG.md` 存在，含 `[Unreleased]`/`[1.1.2]`/`[1.1.1]`/`[1.1.0]`/`[1.0.0]` 五节。
- `docs/` 下仅 `API.md`，无 `superpowers/`。
- API.md 校对：与代码字段一致，错误码集合吻合。
- git tag `v1.1.2` + GitHub release `v1.1.2` 创建。
- 既有 62 个测试不受影响（本次不动代码，无需重跑；实施时仅作 sanity `pytest -q` 确认未误改源码）。

## 9. 非目标

- 不重构任何代码。
- 不改 API 行为/字段。
- 不动管理页前端（v1.1.1 已修）。
- 不补文档之外的 README 章节（如 FAQ、截图）——YAGNI。
