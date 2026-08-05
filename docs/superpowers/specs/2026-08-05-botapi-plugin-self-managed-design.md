# botapi 纯插件自管 + 绑定回归插件配置（v3.0.3 设计）

> 日期：2026-08-05
> 范围：`astrbot_plugin_botapi`（AstrBot 插件，服务端）
> 前置：v3.0.2（已发布，绑定改由平台 tokens 承载，存在 bug）；v3.0.1（账户数据迁插件配置）
> 本设计：v3.0.2 绑定模型有根因 bug（tokenA 走到 tokenB 配置），改为**纯插件自管** + **绑定回归插件配置 bindings 表**，职责边界：插件做 token→平台索引，AstrBot 做平台→abconf 路由。

## 背景与根因

v3.0.2 的绑定模型是「token 出现在目标平台 `tokens` 列表即绑定」，`binding_platform_for` 扫描平台条目 tokens 得目标平台，覆写 UMO 为 `{bound}:FriendMessage:botapi_{scoped}`。

**实测 bug**：机器人A tokens 配 tokenA、机器人B tokens 配 tokenB，但 tokenA 会话走到 tokenB 配置。

**根因（三环）**：
1. AstrBot 的 LLM 配置只由 `umop_config_routing` 路由表决定（`astr_main_agent.py:1236` `get_config(umo=event.unified_msg_origin)` → `AstrBotConfigMgr._load_conf_mapping` → `UmopConfigRouter`）。平台条目 tokens 与路由表**无任何自动关联**。
2. botapi 的 `tokens` 是插件经 `config_metadata` 注入的**全局** platform item（`config_service.py:914-931` `items.update()`），会渲染在每个平台条目上，用户容易把 tokenA/tokenB 配到同一条目或不同条目实际共享。
3. 两套索引（token→平台 vs 平台→abconf）不对齐，`binding_platform_for` 只取第一个命中即 break，不检查多平台冲突。

**结论**：平台 tokens 不是可靠的绑定载体；绑定应回到插件自己维护的 bindings 表，平台→abconf 交给 AstrBot 原生路由（用户在配置文件管理页手配）。

## 目标

1. **纯插件自管**：插件 enable 即自起服务器，不再依赖 botapi 平台条目 enable。
2. **绑定回归插件配置**：`bindings: [{token, platform_id}]` 存插件配置（全局），由插件后台 WebUI 维护。
3. **职责边界**：插件做 token→平台 id 索引 + UMO 覆写；AstrBot 做平台 id→abconf 路由（用户手配 `umop_config_routing`）。
4. **删除平台 tokens 绑定字段**：botapi 不再注入 tokens 到平台配置页。

## 已验证的技术前提

- `CoreLifecycle.event_queue` 是全局共享 queue（`core_lifecycle.py:208`），Star 可通过 `context.get_event_queue()` 获取（`context.py:627`）→ adapter 可脱离 PlatformManager 自建。
- `PlatformManager.load_platform` 里 `if not platform_config["enable"]: return`（`manager.py:108-109`）→ 当前确实依赖 botapi 平台条目 enable；必须脱离。
- 插件配置 list 包裹的 bindings 可被 `check_config_integrity` 保留（v3.0.1 已实测，动态键须 list 包裹）。
- `AstrMessageEvent.unified_msg_origin` setter 重建 MessageSession（`astr_message_event.py:110-114`）→ 覆写 UMO 路由到绑定平台前缀。

## 设计决策（用户确认）

1. **旧 v3.0.2 平台 tokens 数据不迁移**：忽略平台 tokens 里的旧绑定，插件后台从空 bindings 重建。最干净，避免继承 bug 数据。
2. **绑定下拉仅列活跃平台**：`platform_manager.platform_insts` 的 id 集合（排除 botapi 自身）；绑定目标必须在活跃集合，否则视为未绑定。
3. **纯插件自管 adapter**：`BotApiAdapter` 不再继承 `Platform`、不 `@register_platform_adapter`；由 `Star.initialize()` 自建 + 自起服务器。
4. **插件只做 token→平台**：平台 id→abconf 由用户 AstrBot 配置文件管理页配置；插件不感知 abconf。

## 数据模型

```jsonc
// 插件配置 astrbot_plugin_botapi_config.json（账户数据唯一来源）
{
  "host": "0.0.0.0", "port": 9000,
  "tokens": ["tokenA", "tokenB"],            // 账户注册表（auth 严格校验，空=拒连）
  "bindings": [                               // 绑定表（一对一：token→platform_id）
    { "token": "tokenA", "platform_id": "aiocqhttp_main" },
    { "token": "tokenB", "platform_id": "aiocqhttp_backup" }
  ],
  "sessions": [ { "token": "tokenA", "list": [...] } ]
}
```

- botapi 平台条目**不再需要**（可删除）；若残留，迁移收敛 tokens 后清空为占位。
- 目标平台条目不再注入 tokens 字段（`config_metadata` 删除后 AstrBot `check_config_integrity` 会剔除落盘）。

## 一、adapter 重构（adapter.py）

### 去掉

- `@register_platform_adapter` 装饰器 + `config_metadata`/`default_config_tmpl`
- `Platform` 继承、`super().__init__(platform_config, event_queue)`
- 模块级单实例锁 `_server_lock`/`_SERVER_STARTED`/`_server_owner`（多平台条目防重复绑定产物，纯插件自管后仅一个实例）
- `_legacy_port()`（平台条目已弃用）、`binding_platform_for` 的平台 tokens 扫描、`unbind_token` 的平台 tokens 移除、`_read_raw_bindings`

### 新签名

```python
def __init__(self, host: str, port: int, event_queue: asyncio.Queue) -> None:
```

### 自实现（原从 Platform 继承）

```python
self.client_self_id = uuid.uuid4().hex          # 原 platform.py:45，routes.py:26 用作 msg.self_id
self.platform_id = "botapi"                      # 未绑定默认路由前缀（原 self.config.get("id","botapi")）

def meta(self) -> PlatformMetadata:
    return PlatformMetadata(name="botapi", description="BotAPI 移动端适配器",
                            id="botapi", adapter_display_name="BotAPI 移动端",
                            support_streaming_message=True, support_proactive_message=True)

def commit_event(self, event) -> None:
    self._event_queue.put_nowait(event)
```

### 保留

`_sse_clients`、`_last_active`、`_upload_dir`、`_serializer`、`_disabled_tokens`、`_token_to_origin`、`send_by_session`、`_broadcast_to`、`_push_media`、`_put`、`run()`（直接 `app.run_task`，去锁）、`_migrate_accounts`（精简版）。

### 服务器生命周期

- `run()` 返回 `self.app.run_task(host, port, shutdown_trigger=self._shutdown.wait)`，不再有锁
- `shutdown()`：`_shutdown.set()` + 复位 `runtime().adapter = None`（供 `Star.terminate` 调用）

## 二、bindings 存储（plugin_conf.py / _conf_schema.json）

- `_conf_schema.json` 恢复 `bindings` 键声明（`type: list`）
- `plugin_conf.py` 恢复 `get_bindings()` / `set_bindings()`：

```python
def get_bindings():
    return list(load_plugin_conf().get("bindings") or [])

def set_bindings(bindings):
    load_plugin_conf()["bindings"] = list(bindings)
```

## 三、绑定/解绑逻辑（adapter.py）

```python
# plugin_conf.get_bindings()/set_bindings()/save()
def binding_platform_for(self, token: str) -> str | None:
    """查插件配置 bindings 表得 platform_id；校验在 _active_platforms 内才返回。
    未绑定/目标不活跃 → None（回退默认路由）。"""
    from . import plugin_conf as _bindings
    pid = None
    for b in _bindings.get_bindings():
        if b.get("token") == token:
            pid = b.get("platform_id")
            break
    if not pid:
        return None
    active = getattr(self, "_active_platforms", None)
    if active is not None and active:
        return pid if pid in active else None
    # 空集（重启后平台注入前）：回退校验该条目 enable=True
    try:
        for p in astrbot_config.get("platform", []):
            if p.get("id") == pid:
                return pid if p.get("enable") else None
    except Exception:
        pass
    return None

def bind_token(self, token: str, platform_id: str) -> None:
    """一对一：先移除 token 旧绑定，再写入新目标。"""
    from . import plugin_conf as _bindings
    binds = _bindings.get_bindings()
    binds = [b for b in binds if b.get("token") != token]
    binds.append({"token": token, "platform_id": platform_id})
    _bindings.set_bindings(binds)
    _bindings.save()

def unbind_token(self, token: str) -> None:
    """移除 token 的绑定条目；有变更才存。"""
    from . import plugin_conf as _bindings
    binds = _bindings.get_bindings()
    new = [b for b in binds if b.get("token") != token]
    if new != binds:
        _bindings.set_bindings(new)
        _bindings.save()
```

## 四、Web API + 前端（main.py / dashboard）

### 恢复的 Web API

| 端点 | 方法 | 用途 |
|---|---|---|
| `/{P}/platforms` | GET | 活跃平台 id 列表（绑定下拉） |
| `/{P}/accounts/<hash>/bind` | POST | 绑定：body `{platform_id}` |
| `/{P}/accounts/<hash>/unbind` | POST | 解绑 |

- `_do_platforms`：`_refresh_active_platforms()` 后返回 `{"platforms": sorted(active_ids)}`
- `_do_bind`：查 token → 校验 platform_id 在活跃集合 → `adapter.bind_token(target, platform_id)`
- `_do_unbind`：查 token → `adapter.unbind_token(target)`
- `_do_delete`：保留 `adapter.unbind_token(target)` 联动
- `_do_stats`/`_accounts`：`bound_platform` 改回 `adapter.binding_platform_for(token)`（查 bindings 表），返回目标平台 id

### 前端 dashboard

- 账户表格「绑定」列：显示绑定目标平台名 + `解绑` 按钮；未绑定显示 `绑定` 按钮
- 恢复 `modal-bind` 模态框：下拉 `select-bind-platform`，选项来自 `GET platforms`
- 恢复 JS：`openBind`/`bindTarget`/`bindAccount`/`unbindAccount`、`wireDelegation` 的 bind/unbind 分支、`setupToolbar` 的 bind 模态监听
- 恢复 `.bind-select` 样式；`bound_platform` 展示改绑定目标平台名

## 五、迁移（adapter `__init__`，精简）

```python
def _migrate_accounts(self):
    """账户收敛（v3.0.1 保留）：botapi 平台条目 tokens → 插件配置 tokens。
    清理旧键；幂等。不再做 bindings→平台展开。"""
    # 1. 插件 tokens 为空且 botapi 条目 tokens 非空 → 迁到插件（剥离条目 tokens）
    # 2. 清理 botapi 条目旧键 (botapi_bindings/nicknames/host/port/sessions)
    # 3. 落盘（save + astrbot_config.save_config）
```

- **删除**：v3.0.2 的 bindings→平台 tokens 展开、`_read_raw_bindings` 时序 hack
- **bindings 默认空表**：不迁移旧平台 tokens 数据（用户确认后台重建）

## 六、Star 生命周期（main.py）

```python
class BotApiStar(Star):
    async def initialize(self):
        if runtime().adapter is not None:
            return                       # 已起过（重载等）
        rt = runtime()
        rt.context = self.context
        rt.conversation_manager = self.context.conversation_manager
        rt.message_history_manager = self.context.message_history_manager
        from .plugin_conf import get_host, get_port
        adapter = BotApiAdapter(get_host() or "0.0.0.0", get_port() or 9000,
                                self.context.get_event_queue())
        rt.adapter = adapter
        self._server_task = asyncio.create_task(adapter.run())

    async def terminate(self):
        adapter = runtime().adapter
        if adapter:
            adapter.shutdown()           # 设 _shutdown、复位 runtime().adapter=None
        if getattr(self, "_server_task", None):
            self._server_task.cancel()
            await asyncio.gather(self._server_task, return_exceptions=True)
```

- `__init__` 注册 Web API 保持不变（register_web_api 不依赖 adapter）
- `_refresh_active_platforms` 保留：从 `context.platform_manager.platform_insts` 拉活跃 id（排除 botapi 自身），供绑定下拉/校验

## 七、边界与降级

- **未绑定 token**：bindings 无条目 → botapi 默认路由（`botapi:FriendMessage:{scoped}`），行为不变
- **绑定平台禁用/停止**：`_active_platforms` 非空且 pid 不在 → 回退 botapi；空集时 `enable=False` 回退
- **一对一切换**：`bind_token` 先移除旧条目再写新目标，保证 token 只在一个平台
- **删除账户**：`_do_delete` 联动 `unbind_token` 清 bindings
- **auth 严格化**：`_is_valid_token` 保持 `token in (adapter.cfg.tokens or [])`（空=拒连）
- **插件禁用**：`terminate()` 停服务器 + 复位 adapter；平台配置页不再有 botapi 条目依赖

## 八、版本与兼容

- 版本：v3.0.3；README/CHANGELOG 更新为「纯插件自管 + 绑定回归插件配置」
- 老客户端/App 无改动（HTTP API 不变）；绑定前行为不变
- botapi 平台条目可删除（不再需要）；残留条目迁移收敛后清空
- 平台 tokens 字段随 `config_metadata` 删除而消失（AstrBot 剔除未声明键）

## 九、测试

- `test_binding_storage.py`：bindings 表语义——`binding_platform_for` 查表 + 活跃校验回退；`bind_token` 一对一去旧；`unbind_token` 清条目
- `test_binding_routing.py`：`pc.set_bindings` 写插件 bindings；假 adapter `binding_platform_for` 查 bindings；UMO 覆写/回退
- `test_binding_handlers.py`：恢复（bind/unbind 端点回归）
- `test_binding_history.py`：假 adapter 绑定基于 bindings；`bound_conversation_umo` 一致
- `test_platforms_web.py`：恢复 `_do_platforms` 测试；绑定展示改目标平台名
- `test_migration.py`：账户收敛 + 清理旧键；删 bindings→平台 迁移断言
- `test_plugin_conf.py`：恢复 bindings roundtrip + schema 断言
- `test_sessions_storage.py`：绑定 fixture 改 bindings 表
- **新增 `test_lifecycle.py`**：Star `initialize` 建 adapter 起服务器、`terminate` 停服务器复位 runtime；`commit_event` 入队；adapter 不继承 Platform
- 测试纪律：`_conf` fixture（`reset_plugin_conf` + monkeypatch `get_astrbot_config_path` → tmp_path + 预写空配置）
