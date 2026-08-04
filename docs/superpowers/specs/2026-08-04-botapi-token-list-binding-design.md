# botapi 绑定存储改造：token 列表即绑定（修订版设计）

> 日期：2026-08-04
> 范围：`astrbot_plugin_botapi`（AstrBot 插件，服务端）
> 前置：`docs/superpowers/specs/2026-08-04-botapi-singleton-binding-design.md`（已实现 v3.0.0 分支，未发布）
> 本修订：替换「`botapi_bindings` 独立映射表」为「token 出现在目标平台 tokens 列表即绑定」，并清理 botapi 平台条目多余字段。

## 目标

多机器人绑定关系不再存独立的 `botapi_bindings: {token: platform_id}` 映射，而是把 botapi 账户 token 直接写进其绑定目标平台的 `tokens` 列表。配置布局归位：

- **插件配置**（`astrbot_plugin_botapi_config.json`）：只剩 `host`、`port`。
- **botapi 平台条目**：只剩 `tokens`（账户注册表）；删除 `host`/`port`/`nicknames`/`botapi_bindings`。
- **目标平台条目**：`tokens` = 绑定到该平台的 botapi 账户（一对一：一个 token 只出现在一个平台的 tokens 里）。

绑定解析 = 扫描非 botapi 平台条目 `tokens`。用户新建会话沿用绑定平台 UMO，路由到该用户配置文件（目标不变）。

## 已验证的技术前提

- `astrbot_config["platform"]` 是纯列表（`astrbot/core/config/default.py:281` `"platform": []`）；`AstrBotConfig.check_config_integrity` 只递归 dict 值、**不递归列表项**（`astrbot_config.py:207-209`），注入 `tokens` 键到平台条目不会被剥离、可跨 reload 存活。
- AstrBot 原生平台适配器均不使用裸 `tokens` 配置键（weixin_oc 用 `weixin_oc_context_tokens` 等），`tokens` 作为 botapi 绑定键无冲突。
- `_active_platforms` 活跃校验、`bound_conversation_umo`、UMO 覆写链路不变（沿用 v3.0.0）。

## 设计决策（用户确认）

1. **token 留在 botapi tokens**：botapi 条目 `tokens` 是账户注册表；绑定 = 额外写进目标平台 tokens。账户列表仍读 botapi tokens；解绑后自动回 botapi 默认路由。
2. **绑定后会话上下文落绑定平台**：UMO = `{绑定平台}:FriendMessage:botapi_{token}[:{sid}]`，context/history 存绑定平台 conversation_manager（现状不变）。
3. **auth 严格化**：`_is_valid_token` 改为 `token in (adapter.cfg.tokens or [])`；空列表 = 拒连，删除「空则允许所有」分支。
4. **字段清理**：botapi 平台条目删除 host/port/nicknames/botapi_bindings；插件配置删 botapi_bindings；管理页删昵称展示/改名功能。

## 数据模型

```jsonc
// 插件配置 astrbot_plugin_botapi_config.json
{ "host": "0.0.0.0", "port": 9000 }

// botapi 平台条目
{ "id": "botapi", "type": "botapi", "enable": true, "tokens": ["t1","t2","t3"] }

// 目标平台条目（新增 tokens 字段）
{ "id": "aiocqhttp_xxx", "type": "aiocqhttp", "enable": true,
  "tokens": ["t1"], ... }

// 迁移前（v3.0.0 分支已写入的数据）：
// botapi 条目含 host/port/nicknames/botapi_bindings；目标平台无 tokens。
```

## 一、绑定解析（adapter.py `binding_platform_for` 重写）

扫描非 botapi 平台条目 `tokens`，返回含该 token 的**活跃**平台 id；未命中/不活跃返回 None。活跃校验保留在 `binding_platform_for` 内部（接口不变，`bound_conversation_umo`/`submit_inbound`/`_do_stats`/`_do_bind` 调用方式不变）。

```python
def binding_platform_for(self, token: str) -> str | None:
    pid = None
    try:
        for p in astrbot_config.get("platform", []):
            if p.get("id") == self.config.get("id") or p.get("type") == "botapi":
                continue
            if token in (p.get("tokens") or []):
                pid = p.get("id")
                break
    except Exception:
        pass
    if not pid:
        return None
    active = getattr(self, "_active_platforms", None)
    if active is not None and active:
        return pid if pid in active else None
    # _active_platforms 为空（重启后平台注入前）：回退校验该条目 enable=True
    try:
        for p in astrbot_config.get("platform", []):
            if p.get("id") == pid:
                return pid if p.get("enable") else None
    except Exception:
        pass
    return None
```

数据源从 `self.config["botapi_bindings"]` 改为模块级 `astrbot_config` 平台列表（与 `_legacy_port`/`sessions.save_sessions` 同一引用，便于测试 monkeypatch）。`self.config["botapi_bindings"]` 键读写全部移除。

## 二、绑定/解绑写入（adapter.py `bind_token`/`unbind_token` 重写）

直接在 `astrbot_config["platform"]` 平台列表上操作并 `save_config()`（与 `sessions.save_sessions` 同模式）。

- `unbind_token(token)`：遍历非 botapi 平台条目，从各自 `tokens` 移除 token（有变更才写），落盘。
- `bind_token(token, platform_id)`：先 `unbind_token(token)`（保证一对一，从旧平台移除），再把 token 追加到目标平台条目 `tokens`（去重），落盘。
- `unbind` 是 bind 的原子前提：正常路径不会出现 token 在多个平台。

删除 `botapi_bindings` 键读写；`main.py._persist_bindings` 移除（bind/unbind 自带落盘）。

## 三、管理页（main.py / dashboard）

- 移除 `/accounts/<hash>/nickname` 路由与 `_do_set_nickname`/`_set_nickname`。
- `_persist_account_state` 改 `_persist_tokens`（只写 tokens，无 nicknames）。
- `_do_stats`/`_accounts` 响应删除 `nickname` 字段；`bound_platform` 改 `adapter.binding_platform_for(token)`。
- `_do_create` 删除 nickname 参数；`_do_delete` 在删除后调 `adapter.unbind_token(target)`（从所有平台移除）。
- `_do_export` meta 的 `nickname` 删除，标题用 `token_preview`。
- `_do_bind`/`_do_unbind`：删除 `_persist_bindings` 调用（adapter 自带落盘），其余不变。
- dashboard HTML/JS：删昵称列、改名按钮、昵称输入框、`setNickname`、`modal-nickname`、export/chat 传参的 nickname。

## 四、auth 严格化（routes.py `_is_valid_token`）

```python
def _is_valid_token(adapter, token):
    return token in (adapter.cfg.tokens or [])
```

删除「空则允许所有非空 token」分支。token 管理页新增账户流程不变（`_do_create` 加进 tokens）。

## 五、迁移（启动时一次性，adapter `__init__`）

v3.0.0 分支已把绑定写入 botapi 条目 `botapi_bindings`。`BotApiAdapter.__init__` 末尾执行一次迁移（幂等，多 botapi 条目各自跑一遍无副作用）：

1. 读 botapi 条目 `botapi_bindings`；对每个 `(token, pid)` 把 token 加入 `pid` 条目 `tokens`（去重）。
2. 删除 botapi 条目的 `botapi_bindings`、`nicknames`、`host`、`port` 键。
3. `save_config()`。

迁移放 adapter `__init__`（Star 先于 adapter 加载，故不能放 Star；adapter `__init__` 时 `self.config` 已有条目 id，`astrbot_config` 平台列表可读写）。幂等判定：botapi 条目无 `botapi_bindings` 键即已迁过，直接跳过。

## 六、边界与降级

- **未绑定 token**：不在任何非 botapi 平台 tokens → botapi 默认路由（现状不变）。
- **绑定平台禁用/停止**：`_active_platforms` 非空且 pid 不在 → 回退 botapi；空集时 `enable=False` 回退。
- **一对一同名冲突**：`bind_token` 先 unbind，保证 token 只在一个平台。
- **删除账户**：从 botapi tokens 与所有平台 tokens 同时移除。
- **auth 严格化**：升级后未在 botapi tokens 里的旧 token 会 401（需管理页重新创建/加入）——用户已确认接受。

## 七、版本与兼容

- 版本保持 v3.0.0（未发布，无 tag）；README/CHANGELOG v3.0.0 描述更新为「token 列表即绑定」模型。
- 老客户端/App 无改动（HTTP API 不变）。

## 八、测试

- `test_binding_storage`：binding_platform_for 扫描语义；bind 写入目标平台 tokens + 先解绑保证一对一；unbind 从所有平台移除；空列表。
- `test_binding_routing`：submit_inbound 绑定 UMO 由扫描得出（默认+分会话）；未绑定回退；平台禁用回退。
- `test_binding_history`：bound_conversation_umo 用新解析读绑定平台 conversation。
- `test_binding_handlers`：_do_bind/_do_unbind/_do_delete 落盘平台 tokens；_persist_bindings 移除后无悬挂引用。
- `test_auth`：空 tokens 拒连；列表内 token 放行。
- `test_migration`：旧 botapi_bindings + nicknames + host/port 迁移到新布局且幂等。
- 前端 JS 改动无单测，靠现有浏览器/管理页手动验证。
