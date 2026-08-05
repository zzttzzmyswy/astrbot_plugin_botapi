# tests/test_binding_routing.py
import os
from types import SimpleNamespace
import pytest
from astrbot_plugin_botapi import sessions as S


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """重置插件配置单例 → tmp_path，预写空配置（隔离真实磁盘 data/config）。"""
    import json
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [], "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _adapter(monkeypatch, bindings=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a.cfg = SimpleNamespace(tokens=["tok"], sessions={})
    pc.set_sessions_map({})
    a._sse_clients = {}
    a._token_to_origin = {}
    a.client_self_id = "self"
    a._uploaded_files = {}
    a._serializer = SimpleNamespace()
    a.commit_event = lambda e: None
    a._active_platforms = {"aiocqhttp_main"}
    pc.set_bindings(list(bindings if bindings is not None else
                          [{"token": "tok", "platform_id": "aiocqhttp_main"}]))
    return a


@pytest.mark.asyncio
async def test_submit_inbound_bound_uses_platform_umo(monkeypatch):
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch)
    committed = []

    async def fake_persist(key, mid, text):
        pass
    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)

    def fake_commit(event):
        committed.append(event)
    a.commit_event = fake_commit

    await routes_mod.submit_inbound(a, "tok", "hi")
    assert committed[0].unified_msg_origin == "aiocqhttp_main:FriendMessage:botapi_tok"
    assert committed[0].unified_msg_origin.count("botapi") == 1


@pytest.mark.asyncio
async def test_submit_inbound_bound_scoped_sid(monkeypatch):
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch)
    cur = S.sessions_list(a, "tok")
    cur.append({"id": "abc", "name": "x", "created_at": 1})
    S.save_sessions(a, "tok", cur)
    committed = []

    async def fake_persist(key, mid, text):
        pass
    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)

    def fake_commit(event):
        committed.append(event)
    a.commit_event = fake_commit

    await routes_mod.submit_inbound(a, "tok", "hi", session_id="abc")
    assert committed[0].unified_msg_origin == "aiocqhttp_main:FriendMessage:botapi_tok:abc"


@pytest.mark.asyncio
async def test_submit_inbound_unbound_keeps_botapi_umo(monkeypatch):
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch, bindings=[])
    committed = []

    async def fake_persist(key, mid, text):
        pass
    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)

    def fake_commit(event):
        committed.append(event)
    a.commit_event = fake_commit

    await routes_mod.submit_inbound(a, "tok", "hi")
    assert committed[0].unified_msg_origin == "botapi:FriendMessage:tok"


@pytest.mark.asyncio
async def test_submit_inbound_bound_inactive_platform_fallback_botapi(monkeypatch):
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch, bindings=[])
    a._active_platforms = set()
    committed = []

    async def fake_persist(key, mid, text):
        pass
    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)

    def fake_commit(event):
        committed.append(event)
    a.commit_event = fake_commit

    await routes_mod.submit_inbound(a, "tok", "hi")
    assert committed[0].unified_msg_origin == "botapi:FriendMessage:tok"
