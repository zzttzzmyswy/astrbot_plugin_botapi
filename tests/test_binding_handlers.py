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


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """重置插件配置单例 → tmp_path，预写空配置（账户数据源）。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    import json, os
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [],
                   "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _fake_context():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()

        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, handler, methods, desc))

    return FakeContext(), registered


def _make_star(monkeypatch, tokens=None, platforms=None):
    """真实 BotApiAdapter（免 __init__）+ adapter 与 main 共享同一 FakeAstrbotConfig。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import adapter as adapter_mod
    from astrbot_plugin_botapi import runtime as rt_mod
    import astrbot_plugin_botapi.main as main_mod

    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "type": "botapi", "tokens": list(tokens or [])}
    a.cfg = SimpleNamespace(tokens=list(tokens or []), sessions={})
    a._sse_clients = {}
    a._disabled_tokens = set()
    a._last_active = {}
    a._active_platforms = {"aiocqhttp_main"}

    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_tokens(list(tokens or []))

    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    rt_mod.runtime().adapter = a

    fake_cfg = {"platform": list(platforms if platforms is not None else [
        {"id": "botapi", "type": "botapi", "tokens": list(tokens or []), "enable": True},
        {"id": "aiocqhttp_main", "type": "aiocqhttp", "tokens": [], "enable": True},
    ])}

    class FakeAstrbotConfig:
        def __getitem__(self, k):
            return fake_cfg[k]

        def get(self, k, d=None):
            return fake_cfg.get(k, d)

        def save_config(self):
            fake_cfg["_saved"] = True

    fake = FakeAstrbotConfig()
    monkeypatch.setattr(adapter_mod, "astrbot_config", fake)
    monkeypatch.setattr(main_mod, "_cfg_singleton", fake)
    return star, a, fake_cfg, registered


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


@pytest.mark.asyncio
async def test_bind_persists_to_plugin_conf_bindings(monkeypatch):
    """绑定写入插件配置 bindings（一对一），不再写平台条目 tokens。"""
    from astrbot_plugin_botapi import plugin_conf as pc
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])
    res = await star._do_bind(_hash("a"), "aiocqhttp_main")
    assert res["status"] == "ok"
    assert pc.get_bindings() == [{"token": "a", "platform_id": "aiocqhttp_main"}]
    assert fake_cfg["platform"][1].get("tokens") == []


@pytest.mark.asyncio
async def test_bind_empty_platform_id_rejected(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])
    res = await star._do_bind(_hash("a"), "")
    assert res["status"] == "error"
    assert "platform_id" in res["message"]
    assert pc.get_bindings() == []


@pytest.mark.asyncio
async def test_bind_unknown_account_rejected(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])
    res = await star._do_bind(_hash("nope"), "aiocqhttp_main")
    assert res["status"] == "error"
    assert "未找到账户" in res["message"]


@pytest.mark.asyncio
async def test_unbind_removes_plugin_conf_binding(monkeypatch):
    """解绑从插件配置 bindings 移除该 token 条目。"""
    from astrbot_plugin_botapi import plugin_conf as pc
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])
    pc.set_bindings([{"token": "a", "platform_id": "aiocqhttp_main"}])
    res = await star._do_unbind(_hash("a"))
    assert res["status"] == "ok"
    assert pc.get_bindings() == []


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
