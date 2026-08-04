# tests/test_platforms_web.py — Task 7: GET platforms 端点 + 账户数据 bound_platform 字段
import hashlib
import json
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


def _make_star(monkeypatch, tokens=None, bindings=None, active=None,
               config_platforms=None, cm=None):
    """仿 test_binding_handlers.py::_make_star，扩展 _active_platforms / 平台配置。

    config_platforms: astrbot_config["platform"] 列表（缺省只有 botapi 自身条目）。
    cm: conversation_manager fake（缺省空 FakeCM，stats 的 get_curr_conversation_id 返回 None）。
    """
    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    binds = dict(bindings or {})
    adapter = SimpleNamespace(
        cfg=SimpleNamespace(tokens=list(tokens or []), nicknames={}),
        config={"id": "botapi", "tokens": list(tokens or []), "nicknames": {},
                "botapi_bindings": binds},
        platform_id="botapi",
        _sse_clients={},
        _disabled_tokens=set(),
        _last_active={},
        _active_platforms=set(active or ()),
        _put=lambda q, evt: None,
    )

    # 复刻真实 adapter 的 binding_platform_for 语义（绑定 + 平台活跃才返回）
    def binding_platform_for(t):
        pid = binds.get(t)
        if not pid:
            return None
        return pid if pid in adapter._active_platforms else None
    adapter.binding_platform_for = binding_platform_for
    from astrbot_plugin_botapi import runtime as rt_mod

    rt = rt_mod.runtime()
    rt.adapter = adapter
    rt.conversation_manager = cm or SimpleNamespace(
        get_curr_conversation_id=lambda umo: None
    )
    fake_cfg = {
        "platform": list(
            config_platforms
            if config_platforms is not None
            else [{"id": "botapi", "type": "botapi", "tokens": list(tokens or []), "enable": True}]
        )
    }

    class FakeAstrbotConfig:
        def __getitem__(self, k):
            return fake_cfg[k]

        def get(self, k, d=None):
            return fake_cfg.get(k, d)

        def save_config(self):
            fake_cfg["_saved"] = True

    import astrbot_plugin_botapi.main as main_mod

    monkeypatch.setattr(main_mod, "_cfg_singleton", FakeAstrbotConfig())
    return star, adapter, fake_cfg, registered


# ── GET platforms ──


@pytest.mark.asyncio
async def test_platforms_from_active_set(monkeypatch):
    """adapter._active_platforms 非空 → 直接返回活跃平台（不含 botapi 自身）。"""
    star, adapter, _, _ = _make_star(
        monkeypatch, tokens=["a"], active={"aiocqhttp_main", "telegram_x", "botapi"}
    )
    res = await star._do_platforms()
    assert res["status"] == "ok"
    assert res["data"]["platforms"] == ["aiocqhttp_main", "telegram_x"]


@pytest.mark.asyncio
async def test_platforms_fallback_to_enabled_config(monkeypatch):
    """_active_platforms 为空 → 回退到 astrbot_config 里 enable=True 的平台条目（排除自身）。"""
    star, adapter, _, _ = _make_star(
        monkeypatch,
        tokens=["a"],
        active=set(),
        config_platforms=[
            {"id": "botapi", "enable": True},
            {"id": "aiocqhttp_main", "enable": True},
            {"id": "telegram_disabled", "enable": False},
        ],
    )
    res = await star._do_platforms()
    assert res["status"] == "ok"
    assert res["data"]["platforms"] == ["aiocqhttp_main"]


@pytest.mark.asyncio
async def test_platforms_active_set_empty_falls_back(monkeypatch):
    """active 集非空但被过滤后为空（只剩自身）→ 回退 config，保证列表非空。"""
    star, adapter, _, _ = _make_star(
        monkeypatch,
        tokens=["a"],
        active={"botapi"},
        config_platforms=[
            {"id": "botapi", "enable": True},
            {"id": "aiocqhttp_main", "enable": True},
        ],
    )
    res = await star._do_platforms()
    assert res["status"] == "ok"
    assert res["data"]["platforms"] == ["aiocqhttp_main"]


@pytest.mark.asyncio
async def test_platforms_adapter_not_ready(monkeypatch):
    """adapter 未就绪 → 仍回退到 enable 平台配置，不抛异常。"""
    star, adapter, _, _ = _make_star(
        monkeypatch,
        tokens=["a"],
        active=set(),
        config_platforms=[
            {"id": "botapi", "type": "botapi", "enable": True},
            {"id": "aiocqhttp_main", "enable": True},
        ],
    )
    from astrbot_plugin_botapi import runtime as rt_mod

    rt_mod.runtime().adapter = None
    res = await star._do_platforms()
    assert res["status"] == "ok"
    assert res["data"]["platforms"] == ["aiocqhttp_main"]


@pytest.mark.asyncio
async def test_platforms_route_registered(monkeypatch):
    """/astrbot_plugin_botapi/platforms 以 GET 注册。"""
    star, adapter, _, registered = _make_star(monkeypatch, tokens=["a"])
    assert "/astrbot_plugin_botapi/platforms" in {r[0] for r in registered}
    routes = dict((r[0], r[2]) for r in registered)
    assert routes["/astrbot_plugin_botapi/platforms"] == ["GET"]


# ── bound_platform 字段 ──


@pytest.mark.asyncio
async def test_stats_includes_bound_platform(monkeypatch):
    """_do_stats per_account 含 bound_platform（绑定 token 显示其平台 id）。"""
    star, adapter, _, _ = _make_star(
        monkeypatch, tokens=["a", "b"], bindings={"a": "aiocqhttp_main"}
    )
    res = await star._do_stats()
    per = {a["token_hash"]: a for a in res["data"]["per_account"]}
    assert per[_hash("a")]["bound_platform"] == "aiocqhttp_main"
    assert per[_hash("b")]["bound_platform"] is None


@pytest.mark.asyncio
async def test_accounts_includes_bound_platform(monkeypatch):
    """_accounts 每条含 bound_platform。"""
    star, adapter, _, _ = _make_star(
        monkeypatch, tokens=["a", "b"], bindings={"a": "aiocqhttp_main"}
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
        bindings={"a": "aiocqhttp_main"},
        active={"aiocqhttp_main"},   # 绑定平台需在活跃集合内才生效
        cm=FakeCM(),
    )
    res = await star._do_stats()
    assert res["status"] == "ok"
    assert seen == ["aiocqhttp_main:FriendMessage:botapi_a"]
    assert res["data"]["per_account"][0]["message_count"] == 1
    assert res["data"]["per_account"][0]["bound_platform"] == "aiocqhttp_main"
