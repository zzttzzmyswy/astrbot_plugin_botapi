# tests/test_binding_handlers.py — Web 绑定/解绑 handler 持久化测试（仿 test_admin_handlers.py）
import hashlib
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


def _fake_context():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()

        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, handler, methods, desc))

    return FakeContext(), registered


def _make_star(monkeypatch, tokens=None, bindings=None, platforms=None):
    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    binds = dict(bindings or {})
    adapter = SimpleNamespace(
        cfg=SimpleNamespace(tokens=list(tokens or [])),
        config={
            "id": "botapi",
            "tokens": list(tokens or []),
            "nicknames": {},
            "botapi_bindings": binds,
        },
        platform_id="botapi",
        _sse_clients={},
        _disabled_tokens=set(),
        _last_active={},
        _put=lambda q, evt: None,
        # 真实 adapter 的方法（供 _do_bind/_do_unbind 调用）
        bind_token=lambda t, pid: adapter.config["botapi_bindings"].update({t: pid}),
        unbind_token=lambda t: adapter.config["botapi_bindings"].pop(t, None),
    )
    from astrbot_plugin_botapi import runtime as rt_mod

    rt = rt_mod.runtime()
    rt.adapter = adapter
    fake_cfg = {
        "platform": list(
            platforms
            or [
                {
                    "id": "botapi",
                    "tokens": list(tokens or []),
                    "botapi_bindings": dict(binds),
                }
            ]
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


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


@pytest.mark.asyncio
async def test_bind_persists_to_platform_subtree(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])
    res = await star._do_bind(_hash("a"), "aiocqhttp_main")
    assert res["status"] == "ok"
    assert adapter.config["botapi_bindings"]["a"] == "aiocqhttp_main"
    assert fake_cfg["platform"][0]["botapi_bindings"]["a"] == "aiocqhttp_main"
    assert fake_cfg.get("_saved") is True


@pytest.mark.asyncio
async def test_bind_empty_platform_id_rejected(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])
    res = await star._do_bind(_hash("a"), "")
    assert res["status"] == "error"
    assert "platform_id" in res["message"]
    assert "a" not in adapter.config["botapi_bindings"]


@pytest.mark.asyncio
async def test_bind_unknown_account_rejected(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])
    res = await star._do_bind(_hash("nope"), "aiocqhttp_main")
    assert res["status"] == "error"
    assert "未找到账户" in res["message"]


@pytest.mark.asyncio
async def test_unbind_removes_binding(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(
        monkeypatch, tokens=["a"], bindings={"a": "aiocqhttp_main"}
    )
    res = await star._do_unbind(_hash("a"))
    assert res["status"] == "ok"
    assert "a" not in adapter.config["botapi_bindings"]
    assert fake_cfg["platform"][0]["botapi_bindings"] == {}
    assert fake_cfg.get("_saved") is True


@pytest.mark.asyncio
async def test_routes_registered(monkeypatch):
    star, adapter, fake_cfg, registered = _make_star(monkeypatch, tokens=["a"])
    routes = {r[0] for r in registered}
    assert "/astrbot_plugin_botapi/accounts/<token_hash>/bind" in routes
    assert "/astrbot_plugin_botapi/accounts/<token_hash>/unbind" in routes


@pytest.mark.asyncio
async def test_bind_adapter_not_ready(monkeypatch):
    """Test that _do_bind returns error when adapter is None."""
    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    from astrbot_plugin_botapi import runtime as rt_mod

    rt = rt_mod.runtime()
    rt.adapter = None  # adapter not ready
    res = await star._do_bind(_hash("a"), "aiocqhttp_main")
    assert res["status"] == "error"
    assert "适配器未就绪" in res["message"]


@pytest.mark.asyncio
async def test_bind_with_stubbed_persist_fails(monkeypatch):
    """Verify that persistence write is tested: stub _persist_bindings and confirm test fails."""
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])

    # Stub _persist_bindings to no-op BEFORE calling _do_bind
    star._persist_bindings = lambda adapter: None

    res = await star._do_bind(_hash("a"), "aiocqhttp_main")
    assert res["status"] == "ok"
    assert adapter.config["botapi_bindings"]["a"] == "aiocqhttp_main"

    # Now the platform subtree should NOT have been updated (since we stubbed persist)
    # This proves the original test was passing because of the separate copy (dict(binds))
    with pytest.raises(KeyError):
        _ = fake_cfg["platform"][0]["botapi_bindings"]["a"]


