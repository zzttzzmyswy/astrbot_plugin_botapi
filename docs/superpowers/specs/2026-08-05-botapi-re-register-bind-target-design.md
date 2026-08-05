# botapi 重新注册为绑定目标 + 双模式适配器（v3.0.4 设计）

> 日期：2026-08-05
> 范围：`astrbot_plugin_botapi`（AstrBot 插件，服务端）
> 前置：v3.0.3（已发布，纯插件自管 + 绑定回归插件配置 bindings 表）
> 本设计：v3.0.3 的绑定模型错位（绑定目标排除 botapi，但用户实际要绑定 botapi 条目），改为 **botapi 重新注册为平台适配器**，可作绑定目标；服务器仍插件自起（双模式）。

## 背景与根因

v3.0.3 把 botapi 从平台适配器改为纯插件自管（去注册），并假设「绑定目标 = 非 botapi 真实平台」。**用户实测**：后台要绑定的是 botapi 条目本身（多个 botapi 条目 + 其他平台，绑定目标主要是 botapi）。v3.0.3 的去注册 + 迁移禁用条目 + `bind_token` 跳过 botapi 类型，导致绑定下拉找不到 botapi。

**用户模型**：token 先由插件路由到指定的适配器/平台/机器人（UMO 前缀用该条目 id），再由 AstrBot 路由表路由到对应 abconf。

## 目标

1. **botapi 重新注册为平台适配器**：出现在「机器人/平台」配置页，可作为绑定目标。
2. **服务器仍插件自起**：插件 enable 即运作，不依赖条目 enable 决定服务器；条目 enable 只表示「可作为绑定目标」。
3. **双模式 adapter**：条目实例 = 极简占位壳（不设 runtime/不建 app/不跑迁移）；插件实例 = 完整功能 + 服务器。
4. **反向迁移**：v3.0.3 禁用的 botapi 条目恢复 enable=True。
5. **绑定目标允许 botapi 条目**：`bind_token` 删除 botapi 类型跳过逻辑。

## 已验证的技术前提

- AstrBot 对同 type 多条目：`load_platform` 对每个 enable 条目实例化（`manager.py:217` `cls_type = platform_cls_map[type]`），id 各自唯一（`manager.py:219` `_inst_map[platform_config["id"]]`）。
- 重新注册后 `platform_cls_map["botapi"]` 存在 → `manager.py:212` "Platform adapter not found" 检查通过，条目 enable 不再报错（这是 v3.0.3 禁用的原因，现已消除）。
- PlatformManager 对每个条目调 `inst.run()`（`manager.py:52`）并驻留——条目实例的 `run()` 需返回永不完成的协程（不跑服务器，但 PlatformManager 当它驻留）。
- `PlatformManager.load_platform` 用 3 参 `cls_type(platform_config, self.settings, self.event_queue)`（`manager.py:217-218`）。

## 设计决策（用户确认）

1. **重新注册 + 插件自起**：botapi 重新 `@register_platform_adapter`（可作绑定目标），服务器仍由 `Star.initialize` 自建实例启动（不依赖条目 enable）。
2. **条目实例 = 占位壳**：PlatformManager 实例化的条目实例只记录 id，不设 `runtime().adapter`、不建 Quart app、不跑迁移、不跑服务器。
3. **恢复 3 参平台签名**：`__init__(platform_config, platform_settings, event_queue)`（兼容 PlatformManager）；插件实例由 Star 传伪 config。
4. **显式标记键 `_star_managed`**：插件实例 `platform_config` 带 `_star_managed: true`；无标记 → 条目模式。
5. **反向迁移**：迁移把所有 `type==botapi` 条目恢复 `enable=True`（反转 v3.0.3 禁用）。
6. **绑定目标允许 botapi**：`bind_token` 删除 `type=="botapi"` 目标跳过逻辑；`binding_platform_for` 不变（查 bindings + 活跃校验）。
7. **绑定下拉排除 `"botapi"` 常量**（插件实例），保留 botapi_a/botapi_b（条目实例）。

## 数据模型

```jsonc
// 插件配置 astrbot_plugin_botapi_config.json（账户数据唯一来源）
{
  "host": "0.0.0.0", "port": 9000,
  "tokens": ["tokenA", "tokenB"],
  "bindings": [
    { "token": "tokenA", "platform_id": "botapi_a" },
    { "token": "tokenB", "platform_id": "aiocqhttp_main" }
  ],
  "sessions": [ ... ]
}

// AstrBot 平台配置（botapi 条目重新启用）
[
  { "id": "botapi_a", "type": "botapi", "enable": true },
  { "id": "botapi_b", "type": "botapi", "enable": true },
  { "id": "aiocqhttp_main", "type": "aiocqhttp", "enable": true }
]
```

## 一、adapter 双模式（adapter.py）

### 重新注册

```python
@register_platform_adapter(
    "botapi",
    "BotAPI 自定义移动端适配器 — 一人一 Bot 极简移动端接入，支持弱网断连恢复",
    default_config_tmpl={},   # 无 tokens 字段（账户注册表在插件配置）
    adapter_display_name="BotAPI 移动端",
    support_streaming_message=True,
)
class BotApiAdapter(Platform):
```

### `__init__` 双模式

```python
    def __init__(self, platform_config, platform_settings, event_queue):
        super().__init__(platform_config, event_queue)
        self.platform_id = platform_config.get("id", "botapi")
        self._is_entry = not platform_config.get("_star_managed")
        if self._is_entry:
            # 条目模式：极简占位壳，仅记录 id 作为绑定目标。
            # 不设 runtime().adapter、不建 Quart app、不跑迁移、不跑服务器。
            return
        # 插件模式：完整初始化
        self._event_queue = event_queue
        self.client_self_id = uuid.uuid4().hex
        self.cfg = BotApiConfig(tokens=[], sessions={})
        self._token_to_origin = {}
        self._sse_clients = defaultdict(list)
        self._active_platforms = set()
        self._disabled_tokens = set()
        self._last_active = {}
        self._uploaded_files = {}
        self._upload_dir = Path(astrbot_config.get("data_path", "./data")) / "botapi_uploads"
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._shutdown = asyncio.Event()
        self._media_enabled = bool(astrbot_config.get("callback_api_base"))
        self._serializer = MessageSerializer(_media_enabled=self._media_enabled)
        runtime().adapter = self
        from quart import Quart
        self.app = Quart(__name__)
        from .routes import _setup_routes
        self._setup_routes = lambda: _setup_routes(self)
        self._setup_routes()
        from .plugin_conf import load_plugin_conf, get_tokens, get_host, get_port
        self._conf = load_plugin_conf()
        self._host = get_host() or "0.0.0.0"
        self._port = get_port() or 9000
        self._migrate_accounts()
        self.cfg.tokens = get_tokens()
        self._server_started = False
```

### `meta()` 返回条目 id

```python
    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="botapi",
            description="BotAPI 自定义移动端适配器",
            id=self.platform_id,          # 插件实例 "botapi"，条目实例 botapi_a 等
            adapter_display_name="BotAPI 移动端",
            support_streaming_message=True,
            support_proactive_message=True,
        )
```

### `run()` 条目实例占位

```python
    def run(self):
        if self._is_entry:
            # 条目实例：不跑服务器，返回永不完成的协程（PlatformManager 驻留用）。
            return self._shutdown.wait()
        return self.app.run_task(host=self._host, port=self._port,
                                 shutdown_trigger=self._shutdown.wait)
```

### `commit_event` 自实现（插件模式用）

```python
    def commit_event(self, event) -> None:
        self._event_queue.put_nowait(event)
```

### `bind_token` 允许 botapi 目标

删除 `type=="botapi"` 目标跳过逻辑（v3.0.3 禁止，现允许）：

```python
    def bind_token(self, token: str, platform_id: str) -> None:
        """绑定 token → 目标平台（含 botapi 条目）：先移除旧条目（一对一），再追加。"""
        from .plugin_conf import get_bindings, set_bindings, save
        binds = [b for b in get_bindings() if b.get("token") != token]
        binds.append({"token": token, "platform_id": platform_id})
        set_bindings(binds)
        save()
```

## 二、Star 生命周期（main.py）

- `Star.initialize` 构造插件实例用伪 config：`BotApiAdapter({"id": "botapi", "_star_managed": True}, {}, event_queue)`
- 其余不变（`rt.context` 注入、`_server_task` 类级、`terminate` 停服务器）

```python
    @classmethod
    async def initialize(cls):
        if runtime().adapter is not None:
            return
        rt = runtime()
        from .plugin_conf import get_host, get_port
        from .adapter import BotApiAdapter
        adapter = BotApiAdapter(
            {"id": "botapi", "_star_managed": True, "host": get_host() or "0.0.0.0",
             "port": get_port() or 9000},
            {}, rt.context.get_event_queue())
        rt.adapter = adapter
        cls._server_task = asyncio.create_task(adapter.run())
```

## 三、绑定逻辑（adapter.py / main.py）

### `_refresh_active_platforms`（main.py）

- 现状 `if pid and pid != "botapi"` **保持不变**——排除插件实例常量 id "botapi"，保留 botapi_a/botapi_b（条目实例）
- `platform_insts` 现在含 botapi 条目实例（其 `meta().id` = botapi_a 等）→ 自动成为绑定目标

### `binding_platform_for`（adapter.py）

- 不变：查 bindings 表得 pid → 活跃校验（`_active_platforms` 非空须含，空回退 enable）

### `_do_platforms`（main.py）

- `sorted(active) 排除 self_id`——self_id = 插件实例 platform_id = "botapi" → 排除插件实例
- 结果：下拉列 botapi_a/botapi_b + 真实平台

### UMO 覆写

- 绑定到 botapi_a → `{botapi_a}:FriendMessage:botapi_{scoped}` → AstrBot 路由表匹配 `botapi_a::` → abconf
- 未绑定回退 `botapi:FriendMessage:{scoped}`（插件实例默认）

## 四、迁移反转（adapter.py `_migrate_accounts`）

```python
            for p in platforms:
                if p.get("type") == "botapi":
                    for key in ("botapi_bindings", "nicknames", "host", "port", "sessions"):
                        if key in p:
                            p.pop(key, None)
                            changed = True
                    # 反转 v3.0.3：重新启用 botapi 条目（作为绑定目标）。
                    # 重新注册后 enable 不再触发 adapter not found。
                    if p.get("enable") is not True:
                        p["enable"] = True
                        changed = True
```

- 幂等：无 botapi 条目 no-op；已 enable 不动
- 账户收敛（step 1）不变

## 五、边界与降级

- **无 botapi 条目**：插件自起即可，绑定下拉只列其他真实平台（若无则空）
- **未绑定 token**：回退 `botapi:FriendMessage:{scoped}` 默认路由
- **绑定 botapi 条目**：UMO 前缀该条目 id → AstrBot 路由表须有 `{条目id}::` 模式才到目标 abconf；无则 default 配置
- **多 botapi 条目**：各自占位壳，互不冲突；插件实例唯一跑服务器
- **条目实例**：不设 runtime().adapter（避免覆盖插件实例）、不建 app、不跑迁移
- **条目禁用**：enable=False 的 botapi 条目不出现在 `platform_insts` → 绑定下拉没有 → `binding_platform_for` 校验不活跃 → 回退

## 六、版本与兼容

- 版本 v3.0.4；metadata.yaml + CHANGELOG + README 更新
- 老客户端/App 无改动（HTTP API 不变）；未绑定行为不变
- 升级自 v3.0.3：botapi 条目自动恢复 enable（迁移反转）
- 升级自 v3.0.2 及更早：botapi 条目可能已删（v3.0.3 迁移禁用），若用户建新条目则生效

## 七、测试

- `test_adapter_init.py`：双模式构造（`_star_managed=True` → 插件模式；无标记 → 条目模式极简）
- `test_singleton.py`：迁移反转断言（enable True）；`_legacy_port` 相关恢复（3 参签名回来）
- `test_binding_storage.py`：`bind_token` 允许 botapi 类型目标；`binding_platform_for` 对 botapi 条目 id 返回
- `test_platforms_web.py`：绑定下拉含 botapi 条目 id（排除 "botapi" 常量但保留 botapi_a）
- `test_lifecycle.py`：插件实例 `_star_managed` 构造；条目实例不设 runtime
- `test_migration.py`：迁移反转（enable True）；v3.0.3 已禁用条目恢复
- `test_binding_history/routing`：绑定 botapi 条目 id 的 UMO 覆写
- 测试纪律：`_conf` fixture；`BotApiAdapter.__new__(BotApiAdapter)` 构造（重新继承 Platform 后须确保 `run`/`meta` 均已实现——spec 第 3 节已含，故类不抽象，`__new__` 可用；若某测试需要半成品实例再考虑 abstractmethods hack）
