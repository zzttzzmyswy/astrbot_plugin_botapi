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
- 导出对话记录（Markdown / JSON，完整历史无条数上限，Blob 下载）
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
.venv/bin/python -m pytest -q                  # 75 个测试
```

## License

[MIT](LICENSE)。

> 注：AstrBot 本体为 AGPL-3.0。本插件按 MIT 发布；如需与 AstrBot 的 copyleft 完全一致，可改用 AGPL-3.0（替换 LICENSE 即可）。
