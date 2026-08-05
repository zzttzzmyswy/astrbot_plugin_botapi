# tests/test_platforms_web.py — 账户数据 bound_platform 展示字段 + _refresh_active_platforms 活跃注入
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar
from astrbot_plugin_botapi.runtime import runtime as _get_runtime


@pytest.fixture(autouse=True)
def _cleanup_runtime():
    """Reset global runtime state before each test to avoid cross-test leaks."""
    rt = _get_runtime()
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None
    yield
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """重置插件配置单例 → tmp_path，预写空配置（账户数据源在插件配置，杜绝真实磁盘污染）。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [],
                   "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def _fake_context():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()

        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, handler, methods, desc))

    return FakeContext(), registered


def _make_real_adapter(monkeypatch):
    """构造真实 BotApiAdapter（绕过 __init__/ABC），带真实 binding_platform_for。

    用于验证生产路径：_refresh_active_platforms 注入 adapter._active_platforms 后，
    binding_platform_for 才返回绑定平台（修复前为空集导致恒返回 None 的静默失效）。
    """
    from astrbot_plugin_botapi.adapter import BotApiAdapter

    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": [], "sessions": {}}
    a.cfg = SimpleNamespace(tokens=[], sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    a._disabled_tokens = set()
    a._last_active = {}
    a._uploaded_files = {}
    a._active_platforms = set()
    a._serializer = SimpleNamespace()
    a.commit_event = lambda e: None
    return a


def _make_star(monkeypatch, tokens=None, bound_to=None, active=None,
               config_platforms=None, cm=None, platform_manager=None):
    """仿 test_binding_history.py::_make_star，扩展 _active_platforms / 平台配置。

    bound_to: 非空则把 token 放进该平台条目的 tokens（模拟绑定）。
    config_platforms: astrbot_config["platform"] 列表（缺省只有 botapi 自身条目）。
    cm: conversation_manager fake（缺省空 FakeCM，stats 的 get_curr_conversation_id 返回 None）。
    platform_manager: context.platform_manager fake（缺省 None → _refresh_active_platforms 静默跳过）。
    """
    ctx, registered = _fake_context()
    if platform_manager is not None:
        ctx.platform_manager = platform_manager
    star = BotApiStar(ctx, None)
    adapter = SimpleNamespace(
        cfg=SimpleNamespace(tokens=list(tokens or [])),
        config={"id": "botapi", "tokens": list(tokens or [])},
        platform_id="botapi",
        _sse_clients={},
        _disabled_tokens=set(),
        _last_active={},
        _active_platforms=set(active or ()),
        _put=lambda q, evt: None,
    )

    # 复刻真实 adapter 的 binding_platform_for 语义：token 在目标平台 tokens 且平台活跃。
    # bound_to 同时绑定"目标平台"与"该平台的 tokens 列表"（这里模拟 tokens=[第一项]）。
    def binding_platform_for(t):
        if bound_to and t == (tokens or [None])[0]:
            return bound_to if bound_to in adapter._active_platforms else None
        return None
    adapter.binding_platform_for = binding_platform_for
    from astrbot_plugin_botapi import runtime as rt_mod

    rt = rt_mod.runtime()
    rt.adapter = adapter
    rt.conversation_manager = cm or SimpleNamespace(
        get_curr_conversation_id=lambda umo: None
    )
    platform_entries = list(
        config_platforms
        if config_platforms is not None
        else [{"id": "botapi", "type": "botapi", "tokens": list(tokens or []), "enable": True}]
    )
    if bound_to and not any(p.get("id") == bound_to for p in platform_entries):
        platform_entries.append({"id": bound_to, "type": "aiocqhttp",
                                 "tokens": list(tokens or []), "enable": True})
    fake_cfg = {"platform": platform_entries}

    class FakeAstrbotConfig:
        def __getitem__(self, k):
            return fake_cfg[k]

        def get(self, k, d=None):
            return fake_cfg.get(k, d)

        def save_config(self):
            fake_cfg["_saved"] = True

    import astrbot_plugin_botapi.adapter as adapter_mod

    fake = FakeAstrbotConfig()
    monkeypatch.setattr(adapter_mod, "astrbot_config", fake)
    return star, adapter, fake_cfg, registered


# ── bound_platform 字段（展示绑定平台，不提供绑定写入）──


@pytest.mark.asyncio
async def test_stats_includes_bound_platform(monkeypatch):
    """_do_stats per_account 含 bound_platform（绑定 token 显示其平台 id）。"""
    star, adapter, _, _ = _make_star(
        monkeypatch, tokens=["a", "b"], bound_to="aiocqhttp_main",
        active={"aiocqhttp_main"},
    )
    res = await star._do_stats()
    per = {a["token_hash"]: a for a in res["data"]["per_account"]}
    assert per[_hash("a")]["bound_platform"] == "aiocqhttp_main"
    assert per[_hash("b")]["bound_platform"] is None


@pytest.mark.asyncio
async def test_accounts_includes_bound_platform(monkeypatch):
    """_accounts 每条含 bound_platform。"""
    star, adapter, _, _ = _make_star(
        monkeypatch, tokens=["a", "b"], bound_to="aiocqhttp_main",
        active={"aiocqhttp_main"},
    )
    res = await star._accounts()
    accs = {a["token_hash"]: a for a in res["data"]["accounts"]}
    assert accs[_hash("a")]["bound_platform"] == "aiocqhttp_main"
    assert accs[_hash("b")]["bound_platform"] is None


@pytest.mark.asyncio
async def test_stats_uml_reading_still_uses_bound_conversation(monkeypatch):
    """bound_platform 字段不影响 stats 原有按绑定 UMO 计数逻辑。"""
    seen = []

    class FakeCM:
        async def get_curr_conversation_id(self, umo):
            seen.append(umo)
            return "cid1"

        async def get_conversation(self, umo, cid):
            return SimpleNamespace(history=json.dumps([{"role": "user", "content": "hi"}]))

    star, adapter, _, _ = _make_star(
        monkeypatch,
        tokens=["a"],
        bound_to="aiocqhttp_main",
        active={"aiocqhttp_main"},   # 绑定平台需在活跃集合内才生效
        cm=FakeCM(),
    )
    res = await star._do_stats()
    assert res["status"] == "ok"
    assert seen == ["aiocqhttp_main:FriendMessage:botapi_a"]
    assert res["data"]["per_account"][0]["message_count"] == 1
    assert res["data"]["per_account"][0]["bound_platform"] == "aiocqhttp_main"


# ── _refresh_active_platforms：从 PlatformManager 惰性注入（绑定路由依赖活跃集）──


def _fake_pm(*insts):
    """构造 context.platform_manager fake：platform_insts 为给定列表。"""
    return SimpleNamespace(platform_insts=list(insts))


def _meta_inst(pid):
    """带 meta() 的活跃平台实例。"""
    return SimpleNamespace(config={"id": pid}, meta=lambda: SimpleNamespace(id=pid))


def _config_only_inst(pid):
    """无 meta() 的活跃平台实例（回退 inst.config.id）。"""
    return SimpleNamespace(config={"id": pid})


def test_refresh_active_platforms_pulls_meta_ids_excludes_self(monkeypatch):
    """_refresh_active_platforms 从 platform_manager.platform_insts 拉 id（走 meta()），排除 botapi 自身。"""
    star, adapter, _, _ = _make_star(monkeypatch, tokens=["a"], active=set())
    star.context.platform_manager = _fake_pm(
        _meta_inst("botapi"),
        _meta_inst("aiocqhttp_main"),
        _meta_inst("telegram_x"),
    )
    star._refresh_active_platforms()
    assert adapter._active_platforms == {"aiocqhttp_main", "telegram_x"}


def test_refresh_active_platforms_falls_back_to_config(monkeypatch):
    """inst 无 meta() 时回退 inst.config.id，同样排除 botapi 自身。"""
    star, adapter, _, _ = _make_star(monkeypatch, tokens=["a"], active=set())
    star.context.platform_manager = _fake_pm(
        _config_only_inst("botapi"),
        _config_only_inst("aiocqhttp_main"),
    )
    star._refresh_active_platforms()
    assert adapter._active_platforms == {"aiocqhttp_main"}


def test_refresh_active_platforms_noop_without_platform_manager(monkeypatch):
    """platform_manager 未就绪（context 无该属性）→ 静默跳过，不动已注入集合。"""
    star, adapter, _, _ = _make_star(monkeypatch, tokens=["a"], active={"old_platform"})
    star._refresh_active_platforms()
    assert adapter._active_platforms == {"old_platform"}


def test_refresh_active_platforms_noop_when_adapter_none(monkeypatch):
    """adapter 未就绪（启动时序）→ 静默跳过，不抛异常。"""
    star, _, _, _ = _make_star(monkeypatch, tokens=["a"], active=set())
    star.context.platform_manager = _fake_pm(_meta_inst("aiocqhttp_main"))
    from astrbot_plugin_botapi import runtime as rt_mod

    rt_mod.runtime().adapter = None
    star._refresh_active_platforms()   # 不应抛异常


@pytest.mark.asyncio
async def test_stats_refreshes_active_platforms_for_binding(monkeypatch):
    """_do_stats 惰性刷新：绑定平台在 platform_manager 活跃 → 计数读绑定平台 UMO。"""
    import json
    from astrbot_plugin_botapi import runtime as rt_mod

    star, _, fake_cfg, _ = _make_star(
        monkeypatch, tokens=["a"],
        config_platforms=[
            {"id": "botapi", "type": "botapi", "enable": True},
            {"id": "aiocqhttp_main", "tokens": ["a"], "enable": True},
        ],
    )
    real = _make_real_adapter(monkeypatch)
    real.config["tokens"] = ["a"]
    real.cfg.tokens = ["a"]
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_tokens(["a"])                       # 账户在插件配置
    rt_mod.runtime().adapter = real

    seen = []

    class FakeCM:
        async def get_curr_conversation_id(self, umo):
            seen.append(umo)
            return "cid1"

        async def get_conversation(self, umo, cid):
            return SimpleNamespace(history=json.dumps([{"role": "user", "content": "hi"}]))

    rt_mod.runtime().conversation_manager = FakeCM()
    star.context.platform_manager = _fake_pm(
        _config_only_inst("botapi"),
        _config_only_inst("aiocqhttp_main"),
    )
    res = await star._do_stats()
    assert res["status"] == "ok"
    assert seen == ["aiocqhttp_main:FriendMessage:botapi_a"]
    assert res["data"]["per_account"][0]["message_count"] == 1


@pytest.mark.asyncio
async def test_binding_requires_active_platform(monkeypatch):
    """绑定到未启动平台（不在活跃集）→ 即便 token 在平台 tokens，binding_platform_for 仍返回 None。"""
    from astrbot_plugin_botapi import runtime as rt_mod
    from astrbot_plugin_botapi import plugin_conf as pc

    star, _, fake_cfg, _ = _make_star(
        monkeypatch, tokens=["a"],
        config_platforms=[
            {"id": "botapi", "type": "botapi", "enable": True},
            {"id": "aiocqhttp_main", "tokens": ["a"], "enable": False},
        ],
    )
    real = _make_real_adapter(monkeypatch)
    real.config["tokens"] = ["a"]
    real.cfg.tokens = ["a"]
    pc.set_tokens(["a"])
    rt_mod.runtime().adapter = real
    star.context.platform_manager = _fake_pm(_config_only_inst("botapi"))
    assert real.binding_platform_for("a") is None   # 平台禁用/未在跑 → 回退默认路由
