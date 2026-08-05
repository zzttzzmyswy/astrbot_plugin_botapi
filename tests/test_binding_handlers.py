# tests/test_binding_handlers.py — bind/unbind/platforms Web API（纯插件自管后绑定回归插件配置）
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar
from astrbot_plugin_botapi.runtime import runtime as _get_runtime


@pytest.fixture(autouse=True)
def _cleanup_runtime():
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
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [],
                   "bindings": [], "sessions": []}, f)
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


def _make_star(monkeypatch, tokens=None, active=None):
    """真实 BotApiAdapter（免 __init__）+ 插件配置注入 tokens/bindings。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    from astrbot_plugin_botapi import runtime as rt_mod
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a.cfg = SimpleNamespace(tokens=list(tokens or []))
    a._sse_clients = {}
    a._disabled_tokens = set()
    a._last_active = {}
    a._active_platforms = set(active if active is not None else {"aiocqhttp_main"})
    pc.set_tokens(list(tokens or []))
    pc.save()
    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    rt_mod.runtime().adapter = a
    return star, a, registered


@pytest.mark.asyncio
async def test_bind_writes_bindings(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"], active={"aiocqhttp_main"})
    res = await star._do_bind(_hash("tok"), "aiocqhttp_main")
    assert res["status"] == "ok"
    assert pc.get_bindings() == [{"token": "tok", "platform_id": "aiocqhttp_main"}]


@pytest.mark.asyncio
async def test_bind_rejects_inactive_platform(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"], active={"telegram_x"})
    res = await star._do_bind(_hash("tok"), "aiocqhttp_main")
    assert res["status"] == "error"          # 目标不在活跃集合
    assert pc.get_bindings() == []


@pytest.mark.asyncio
async def test_unbind_clears_bindings(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_bindings([{"token": "tok", "platform_id": "aiocqhttp_main"}])
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"], active={"aiocqhttp_main"})
    res = await star._do_unbind(_hash("tok"))
    assert res["status"] == "ok"
    assert pc.get_bindings() == []


@pytest.mark.asyncio
async def test_do_platforms_lists_active(monkeypatch):
    from astrbot_plugin_botapi import runtime as rt_mod
    star, adapter, _ = _make_star(monkeypatch, tokens=[], active={"aiocqhttp_main", "telegram_x"})
    res = await star._do_platforms()
    assert res["status"] == "ok"
    assert res["data"]["platforms"] == ["aiocqhttp_main", "telegram_x"]


@pytest.mark.asyncio
async def test_delete_unbinds(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_bindings([{"token": "tok", "platform_id": "aiocqhttp_main"}])
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"], active={"aiocqhttp_main"})
    await star._do_delete(_hash("tok"))
    assert pc.get_bindings() == []
