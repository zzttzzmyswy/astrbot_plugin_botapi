# tests/test_admin_handlers.py
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


def _make_star(monkeypatch, tokens=None):
    """真实 BotApiAdapter（免 __init__）+ 插件配置注入 tokens。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    from astrbot_plugin_botapi import runtime as rt_mod
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "type": "botapi"}
    a.cfg = SimpleNamespace(tokens=list(tokens or []))
    a._sse_clients = {}
    a._disabled_tokens = set()
    a._last_active = {}
    a._active_platforms = {"aiocqhttp_main"}
    pc.set_tokens(list(tokens or []))
    pc.save()

    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    rt_mod.runtime().adapter = a
    return star, a, registered


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


@pytest.mark.asyncio
async def test_create_account_persists(monkeypatch):
    import json, os
    from astrbot_plugin_botapi import plugin_conf as pc
    star, adapter, _ = _make_star(monkeypatch, tokens=[])
    token = await star._do_create("newtok")
    assert token["status"] == "ok"
    assert token["data"]["token"] == "newtok"
    assert "newtok" in pc.get_tokens()
    assert "newtok" in adapter.cfg.tokens
    # 配置已落盘（plugin_conf save → AstrBotConfig.save_config）
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    disk = json.load(open(
        os.path.join(astrbot_path_mod.get_astrbot_config_path(),
                     "astrbot_plugin_botapi_config.json"), encoding="utf-8-sig"))
    assert "newtok" in disk.get("tokens", [])


@pytest.mark.asyncio
async def test_delete_account(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    star, adapter, _ = _make_star(monkeypatch, tokens=["a", "b"])
    result = await star._do_delete(_hash("a"))
    assert result["status"] == "ok"
    assert "a" not in pc.get_tokens()
    assert "a" not in adapter.cfg.tokens


@pytest.mark.asyncio
async def test_toggle_disable(monkeypatch):
    star, adapter, _ = _make_star(monkeypatch, tokens=["a"])
    result = await star._do_toggle(_hash("a"), disabled=True)
    assert result["status"] == "ok"
    assert "a" in adapter._disabled_tokens


@pytest.mark.asyncio
async def test_stats_envelope(monkeypatch):
    star, adapter, _ = _make_star(monkeypatch, tokens=["a", "b"])
    result = await star._do_stats()
    assert result["status"] == "ok"
    assert result["data"]["total_accounts"] == 2
