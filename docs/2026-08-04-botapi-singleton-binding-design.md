# botapi 单实例化 + 多机器人绑定设计

> 日期：2026-08-04
> 范围：`astrbot_plugin_botapi`（AstrBot 插件，服务端）

## 目标

解决：一个 AstrBot 对接多个配置文件时，不同用户自定义的会话不能及时路由到指定配置文件。

方案：botapi 单实例化（端口从机器人配置页移到插件配置页）；一个 AstrBot 只启动一个 botapi；
允许多个机器人（AstrBot platform 实例）；每个机器人绑定一个或多个 botapi 账户（token）；
用户新会话自动指向该用户绑定的机器人配置，从而正确解析到该用户的配置文件。

## 已确认的设计决策

1. **机器人 = AstrBot platform 实例**。绑定 = botapi token → platform_id 映射。
2. **绑定后消息 UMO 完全复用被绑定平台的 UMO**（如 `aiocqhttp:FriendMessage:botapi_{token}[:{sid}]`），
   让 AstrBot 原生 `umop_config_routing` 路由到绑定平台的配置文件，AstrBot 原生
   conversation_manager/history 管理会话与历史。
3. **绑定表在插件配置页维护**（token → platform_id）。
4. **插件内实现，不改 AstrBot 核心**。

## 已验证的技术前提

- `AstrMessageEvent.unified_msg_origin` **有 setter**（`astr_message_event.py:110-114`），设置后重建
  `MessageSession`。botapi 可在提交前设置事件 UMO 为绑定平台 UMO。
- `EventBus.dispatch`（`event_bus.py:41-42`）用 `event.unified_msg_origin` → `get_conf_info` 路由到
  对应配置文件的 pipeline scheduler。设置 UMO 后自动走绑定平台配置文件。
- pipeline 里 `event.send()` / `send_streaming`（`internal.py:160,438`）是**虚方法分派**到
  `BotApiMessageEvent.send` override（botapi SSE），不因 UMO 改变而转向绑定平台 adapter。
- `active_event_registry` 按 UMO 管理（仅用于 reset/terminate 会话），UMO 设为绑定平台后语义正确
  （同一用户会话）。
- `MessageSession.from_str` 用 `split(":", 2)`，第三段（session_id）可含冒号 →
  `botapi_{token}:{sid}` 作为 session_id 安全，多会话与绑定 UMO 兼容。

## 术语

- **绑定（binding）**：botapi token → platform_id 的映射。
- **绑定平台 UMO**：`{platform_id}:FriendMessage:botapi_{token}[:{sid}]`。
  session_id 前缀 `botapi_` 避免与绑定平台原生用户 id 冲突（该平台已有同名用户时）。

---

## 一、botapi 单实例化（端口移到插件配置页）

### 现状

botapi 是 `register_platform_adapter("botapi", ...)` 平台适配器，端口（host/port）在平台配置里。
AstrBot 每配一个 botapi platform 条目就 `run()` 一个 Quart 服务器（adapter.py:75-77）。

### 改动

- **端口/监听配置从平台配置移到插件配置**：在插件的全局配置声明 `botapi_host` / `botapi_port`。
  插件 `Star.__init__(self, context, config)` 的 `config` 参数是插件配置（AstrBotConfig），
  从 `self.config.get("botapi_host")` / `self.config.get("botapi_port")` 读取。插件配置页编辑
  这些键（metadata.yaml 或插件配置 schema 声明，需查证插件配置页如何暴露任意键）。
- **单实例保证**：botapi 的 Quart 服务器由插件 `Star` 在初始化时启动一次（`BotApiStar.__init__`
  里 `asyncio.create_task(self._app.run_task(host, port))`）。`BotApiAdapter.run()` 改为 **no-op**
  （返回一个永不结束的 coroutine 或空），避免 AstrBot `PlatformManager` 对每个 botapi platform
  条目重复启动 Quart。adapter 仍被实例化（用于消息事件构造/SSE 广播），但服务器只由 Star 管。
- **兼容迁移**：启动时若插件配置无 port，回退旧平台配置里的 port（`config["platform"]` 里
  type=botapi 条目的 port），再回退默认 9000。

### 实现要点

- `BotApiStar.__init__`：读插件配置（`self.config`）拿 host/port，启动单例 Quart 服务器。
- `BotApiAdapter.run()`：不再自行起服务器（改由 Star 管理生命周期），或保留但 Star 只初始化
  一个 adapter 实例。
- 需要确认 AstrBot `PlatformManager` 对 platform 条目重复启动的影响（每个 botapi platform 条目
  仍会实例化 adapter；需保证仅一个起 Quart）。

---

## 二、token → platform 绑定表

### 存储

插件配置新增字段（插件配置页可编辑）：

```json
{
  "botapi_bindings": {
    "<token>": "<platform_id>"
  }
}
```

- token 是 botapi 账户（现有 tokens 列表）。
- platform_id 是 AstrBot platform 条目 id（如 `aiocqhttp_xxx`、`telegram_xxx`）。
- 一个 platform 可绑定多个 token（表是多对一）。
- 未绑定的 token：保持现有 botapi 身份 UMO（`botapi:FriendMessage:{token}`），回退行为不变。

### 管理

- Web 管理页（现有 dashboard）：账户列表增加「绑定机器人」操作，下拉选择已启动的 platform 列表，
  保存到绑定表。
- Phone API 无需暴露绑定管理（管理页足够）。

---

## 三、消息提交与 UMO 路由

### `submit_inbound` 改动

```python
async def submit_inbound(adapter, token, text, file_ids=None, session_id="") -> str:
    sid = _sessions.resolve_sid(adapter, token, session_id)
    scoped_key = _sessions.scoped_key_for(adapter, token, sid)
    ...
    msg.session_id = scoped_key   # 裸第三段（AstrMessageEvent 会拼前缀）
    ...
    event = BotApiMessageEvent(..., session_id=scoped_key, adapter=adapter)
    # 绑定平台：覆写 UMO 使 AstrBot 路由到绑定平台配置文件
    bound_platform = adapter.binding_platform_for(token)
    if bound_platform:
        event.unified_msg_origin = (
            f"{bound_platform}:FriendMessage:botapi_{scoped_key}"
        )
    event.set_extra("enable_streaming", True)
    await persist_inbound_text(scoped_key, msg.message_id, text)
    adapter.commit_event(event)
    return msg.message_id
```

- 绑定后 UMO = `{platform}:FriendMessage:botapi_{token}`（默认会话）或
  `{platform}:FriendMessage:botapi_{token}:{sid}`（分会话）。
- `botapi_` 前缀避免与绑定平台原生用户 id 冲突。
- **注意**：`BotApiMessageEvent` 的 `self.sid` / `self.scoped_key` 解析基于构造时的 session_id
  （裸 scoped key），与 UMO 设置无关，仍正确。

### `BotApiAdapter.binding_platform_for(token)`

- 查绑定表（从插件配置读取）返回 platform_id 或 None。
- 同时需维护 `token → bound platform` 缓存，供 history/clear 等用。

---

## 四、历史 / 会话 / 清空 读取绑定平台的 conversation

因为绑定后事件 UMO 是绑定平台 UMO，AstrBot 的 conversation_manager 会把会话上下文存到
绑定平台 UMO 名下。botapi 的 `/history`、会话列表、清空操作必须读**绑定平台 UMO**。

### 改动

- `history.get_conversation_messages(rt, platform_id, token, limit)`：`platform_id` 参数改为
  绑定平台 id（而非固定 `adapter.platform_id`）。调用方传 `adapter.binding_platform_for(token) or adapter.platform_id`。
- `_do_history`：用 `binding_platform_for(target)` 决定 umo 的 platform 段。
- `_do_clear`：`umo_for` 需用绑定平台 id 构造 umo。
- `_do_stats` / 管理页消息计数：同样用绑定平台 umo 读 conversation。

### session_id 归属

- botapi 自己的 `sessions` 表（config 里）仍按 token 管理，与会话列表显示无关。
- 绑定后会话的「上下文」在绑定平台 UMO 下，botapi 会话列表仍展示，但历史读绑定平台 conversation。

---

## 五、回复链路

### `event.send()` / `send_streaming`（LLM 回复）— 不需改

pipeline 调 `event.send()` → `BotApiMessageEvent.send` override → SSE 广播到 botapi 的
scoped key（`adapter._broadcast_to`）。这是虚方法分派，绑定 UMO 不影响回复回到 botapi。

### `send_by_session`（主动/反向消息）— 需处理

绑定平台 UMO 下，若任何代码路径调 `send_by_session(session, ...)`，其中 `session.session_id` =
`botapi_{token}` 或 `botapi_{token}:{sid}`（第三段）。当前 `BotApiAdapter.send_by_session`
解析 `parts[0]/parts[1]` 取 token/sid——但绑定后第三段带 `botapi_` 前缀且不含绑定平台段，
解析出的「token」会是 `botapi_{token}`，导致 scoped_key 错误投不到 botapi 队列。

**修复**：`send_by_session` 检测 session_id 是否 `botapi_` 前缀；若是，剥掉前缀得裸 token（+ 可选
`:sid`），投到 botapi 的 scoped SSE 队列。这是绑定模式下 botapi 主动消息的唯一入口。

---

## 六、App 端影响

- App 无改动。App 仍通过 botapi HTTP API（`/api/v1/botapi/...`）通信，token 是 botapi 账户。
  绑定是服务端路由行为，对 App 透明。
- App 会话列表、消息收发接口不变。

---

## 七、Web 管理页

- 账户列表新增「绑定机器人」列/操作：选 platform_id，保存到绑定表。
- 展示当前绑定。
- 需要能列出已启动的 platform（AstrBot platform 列表）。查 `platform_manager._inst_map` 或
  `config["platform"]` 已启用的条目。

---

## 八、边界与降级

- **未绑定 token**：走 botapi 身份 UMO（现状），回退行为不变。
- **绑定平台未启用/不存在**：回退 botapi 身份 UMO。
- **绑定的 platform 停止**：`binding_platform_for` 需校验 platform 是否活跃，否则回退。
- **多会话 + 绑定**：sid 作为 session_id 后缀，`botapi_{token}:{sid}`，安全。
- **绑定表变更**：即时生效（读插件配置，无需重启）。
- **单实例冲突**：若用户仍配了多个 botapi platform 条目，只启动一个 Quart（Star 管理），
  其余条目 adapter 不重复起端口（run() no-op）。

---

## 九、测试

### 服务端（pytest）

- `test_binding_storage.py`：绑定表读写、持久化、platform 校验。
- `test_binding_routing.py`：`submit_inbound` 设置绑定 UMO（默认+分会话）；未绑定回退 botapi UMO；
  `binding_platform_for` 边界（平台不存在/停用回退）。
- `test_binding_history.py`：`/history` 读绑定平台 conversation；清空/统计用绑定 umo。
- `test_binding_proactive.py`：`send_by_session` 从绑定 UMO 反向解析 token 并投 botapi SSE。
- `test_singleton.py`：单实例 Quart（多 platform 条目只起一个服务器）。

### App

- 无（App 接口不变）。

---

## 十、兼容性 / 版本

- AstrBot ≥ 4.25.5（不变）。
- 插件版本 → v3.0.0（架构级改动）。
- 存量配置：未绑定 token 行为不变；端口从旧平台配置迁移到插件配置。
- App 无需升级（接口不变），但建议配套发布说明。
