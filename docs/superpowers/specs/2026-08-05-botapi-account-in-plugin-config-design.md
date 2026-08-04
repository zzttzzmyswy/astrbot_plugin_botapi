# 账户数据迁移到插件配置（全局）设计

> 日期：2026-08-05
> 范围：`astrbot_plugin_botapi`（AstrBot 插件，服务端）
> 背景：v3.0.0 已发布，但账户数据放在 botapi **平台条目**（`astrbot_config["platform"]`），
> 用户升级后 WebUI 保存一次平台配置即被 `update_bot` 整体覆盖 → 账户全部丢失。本设计把
> 账户数据（tokens/sessions/bindings）迁入插件配置（全局），botapi 平台条目清空为占位。

## 事故根因（已定位）

- v3.0.0 账户读 `astrbot_config["platform"]` 里 botapi 条目的 `tokens`。
- AstrBot `BotConfigService.update_bot`（`config_service.py:1245`）用前端表单 config **整体替换**
  平台条目（`self.config["platform"][idx] = config`）。
- v3.0.0 把 `default_config_tmpl` 从 `{"host","port","tokens","nicknames"}` 改小成 `{"tokens":[]}`，
  表单按新模板渲染 → 保存时 tokens 被重置为 `[]` → 账户列表清空 + 旧 token 401。

## 正确模型（用户确认）

**账户是全局概念，全部放插件配置 `astrbot_plugin_botapi_config.json`**，独立于任何平台条目的
生命周期。botapi 平台条目只剩 `{type,id,enable}` 路由占位；目标平台条目不再承载 botapi 账户。

## 关键技术约束（已实测验证）

- AstrBot `AstrBotConfig.check_config_integrity`（`astrbot_config.py:173-230`）：
  - **list 类型键：元素完全保留**（含 list 内 object 的动态键）。
  - **dict/object 类型键：递归检查，动态键被剔除**（`bindings.t1`/`sessions.t1` → `Config key removed`），
    schema 声明 `items` 也不例外。
  - 推论：动态键映射（bindings/sessions）**必须用 list 包裹**，schema 声明 `{"type":"list"}`。
- `DEFAULT_VALUE_MAP` 支持 int/float/bool/string/text/list/file/object/template_list/dict。
- 插件配置 `AstrBotConfig` 实例目前只在 adapter `__init__` 局部创建 → 需提升为**全局单例**
  （模块级持有），供 adapter/main/sessions 读写同一实例并 `save_config`，避免多实例互相覆盖。
- botapi 平台条目与目标平台条目当前可能残留账户/绑定数据 → 启动迁移收敛回插件配置并清空。

## 数据模型（`astrbot_plugin_botapi_config.json`）

```jsonc
{
  "host": "0.0.0.0",
  "port": 9000,
  "tokens": ["t1", "t2", "t3"],          // 账户注册表（list，元素保留）
  "bindings": [                           // 绑定映射（list 包裹，一对一）
    {"token": "t1", "platform_id": "aiocqhttp_xxx"}
  ],
  "sessions": [                           // 会话（list 包裹）
    {"token": "t1", "list": [{"id": "default", "name": "默认会话", "created_at": 0}]}
  ]
}
```

- **tokens**：`list[str]`，账户注册表（auth 严格列表，空=拒连）。
- **bindings**：`list[{"token","platform_id"}]`，token→平台一对一。一个 token 最多一条。
- **sessions**：`list[{"token","list":[会话对象]}]`，每个 token 一份会话列表。
- `_conf_schema.json` 声明全部 5 键（host/port/tokens/bindings/sessions 均为 list 承载动态结构）。

## 一、全局插件配置单例（新模块 `plugin_conf.py`）

```python
# plugin_conf.py — 全局插件配置单例（账户数据源 + host/port）
_conf = None   # 模块级单例

def load_plugin_conf():
    global _conf
    if _conf is None:
        from astrbot.core.utils.astrbot_path import get_astrbot_config_path
        from astrbot.core.config.astrbot_config import AstrBotConfig
        import os, json, inspect
        # schema 从插件目录 _conf_schema.json 读（复用 adapter._load_plugin_schema 的路径逻辑）
        _conf = AstrBotConfig(
            config_path=os.path.join(get_astrbot_config_path(), "astrbot_plugin_botapi_config.json"),
            schema=_load_schema(),
        )
    return _conf

def _load_schema():
    from pathlib import Path
    import json
    p = Path(__file__).parent / "_conf_schema.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8-sig"))
    return {}
```

- adapter `__init__` 用 `load_plugin_conf()` 读 host/port/tokens/bindings/sessions（不再各自 new）。
- main.py/sessions.py 读写账户走同一单例，`save_config()` 落盘。
- 测试用 `load_plugin_conf.reset()`（或 `_conf=None`）注入临时路径。
- **接口**：
  - `load_plugin_conf() -> AstrBotConfig`
  - `reset_plugin_conf()`（测试/重载用）
  - `get_tokens()` / `set_tokens(list)` / `get_bindings() -> list[dict]` /
    `set_bindings(list)` / `get_sessions_map() -> dict[token, list]` / `set_sessions_map(dict)` /
    `save()`（helper，转发 `save_config`）。

## 二、adapter 改造（`adapter.py`）

- `BotApiAdapter.__init__`：
  - `self._conf = load_plugin_conf()`；`_host`/`_port` 读 `self._conf`（保留 `_legacy_port` 回退）。
  - `self.cfg` 不再承载账户（tokens/sessions 从插件配置读）。
- `binding_platform_for(token)`：改为查插件配置 `bindings` 列表（token→platform_id），命中后活跃校验不变。
- `bind_token(token, platform_id)`：写入插件配置 bindings（先移除该 token 旧条目，再追加），`save()`。
- `unbind_token(token)`：从插件配置 bindings 移除该 token 条目，`save()`。
- `_migrate_legacy_bindings()` → 改为迁移函数（见第五节）。
- 删除对 `astrbot_config["platform"]` 平台条目 tokens 的一切读写。

## 三、sessions 改造（`sessions.py`）

- `sessions_list(adapter, token)`：读插件配置 sessions 里该 token 的 list。
- `save_sessions(adapter, token, sessions)`：写插件配置 sessions（token→list），`save()`。
- 删除对 `adapter.config`/`astrbot_config` 平台条目的读写。
- `bound_conversation_umo` 不变（仍调 `binding_platform_for`）。

## 四、main.py 改造

- `_persist_tokens` → 写插件配置 tokens + `save()`（不再写平台条目）。
- `_do_stats`/`_accounts`：tokens 从插件配置读；`bound_platform` 仍 `binding_platform_for(token)`。
- `_do_create`/`_do_delete`：读写插件配置 tokens；删除时同时从 bindings/sessions 移除该 token 条目。
- `_do_bind`/`_do_unbind`：调 `adapter.bind_token`/`unbind_token`（写插件配置），不再有 `_persist_bindings`。
- `_do_export`：tokens 从插件配置读。

## 五、迁移（启动时，adapter `__init__` 调）

一次性把存量账户收敛到插件配置：

1. **平台条目 tokens → 插件 tokens**：`插件.tokens` 为空且 botapi 平台条目 `tokens` 非空 →
   复制到插件 tokens；清空平台条目 tokens。
2. **目标平台 tokens → 插件 bindings**：遍历非 botapi 平台条目 tokens，每个 token 生成一条
   `{"token": t, "platform_id": pid}`；清空这些条目 tokens。
3. **清理 botapi 平台条目**：移除 `tokens`/`botapi_bindings`/`nicknames`/`host`/`port`/`sessions` 键
   （若有），只留 `{type,id,enable}`。
4. `save()`。

幂等：`插件.tokens` 非空即视为已迁移，跳过 1-3（但仍可做 3 的清理，无键即 no-op）。

## 六、auth / UMO / 管理页

- `_is_valid_token` 仍 `token in (adapter.cfg.tokens or [])`，空=拒连不变。`adapter.cfg.tokens` 保留为
  **运行时缓存**：adapter `__init__` 从插件配置 `get_tokens()` 注入；所有写操作（`_do_create`/
  `_do_delete`/`_persist_tokens`）写插件配置后同步更新 `self.cfg.tokens`（与现有 `_persist_tokens`
  更新 cfg 的模式一致），`_is_valid_token` 与现有测试零改动。
- UMO 路由机制（`{bound}:FriendMessage:botapi_{scoped_key}`）完全不变。
- dashboard：无 UI 改动（账户列表/绑定/解绑按钮逻辑不变，仅数据源换插件配置）。

## 七、边界与降级

- 未绑定 token：插件 bindings 无条目 → botapi 默认路由（现状不变）。
- 绑定平台禁用/停止：活跃校验不变（`_active_platforms` 非空须含 pid，为空回退 enable=True）。
- 一对一：bind_token 先移除旧条目再追加。
- 删除账户：从插件 tokens/bindings/sessions 同时移除。
- 并发：`save_config` 走 AstrBotConfig 的锁（astrbot_config.py 有 `_save_state_lock`）。

## 八、版本与兼容

- 版本 → **v3.0.1**（修复 v3.0.0 的账户丢失事故，接口不变）。
- 老客户端/App 无改动（HTTP API 不变）。
- 存量平台条目账户数据启动时自动迁移到插件配置；迁移后平台条目清空，不再被 update_bot 覆盖影响。

## 九、测试

- `test_plugin_conf.py`：全局单例读写、tokens/bindings/sessions 增删、save 落盘、reset。
- `test_binding_storage.py`（重写）：binding_platform_for 查插件 bindings；bind/unbind 写插件 bindings；
  一对一；活跃/禁用回退；空 bindings 未绑定。
- `test_migration.py`（重写）：平台 tokens→插件、目标平台 tokens→bindings、清空平台条目、幂等。
- `test_sessions_storage.py`：sessions_list/save_sessions 读插件配置。
- `test_admin_handlers.py`：_do_create/_do_delete/_do_stats/_accounts 读写插件 tokens；删除联动清理
  bindings/sessions。
- `test_binding_routing.py`/`test_binding_history.py`/`test_platforms_web.py`：数据源改为插件配置后
  的 UMO/history/stats/bound_platform 断言。
- 前端无改动，靠现有管理页验证。
