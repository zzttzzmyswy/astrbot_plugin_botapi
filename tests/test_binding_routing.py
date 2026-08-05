# tests/test_binding_routing.py
from types import SimpleNamespace
import pytest
from astrbot_plugin_botapi import sessions as S


def _adapter(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": ["tok"], "sessions": {}}
    a.cfg = SimpleNamespace(tokens=["tok"], sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    a.client_self_id = "self"
    a._uploaded_files = {}
    a._serializer = SimpleNamespace()
    a.commit_event = lambda e: None
    a._active_platforms = {"aiocqhttp_main"}
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "botapi", "type": "botapi", "enable": True},
        {"id": "aiocqhttp_main", "tokens": ["tok"], "enable": True},
    ]})
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
    a = _adapter(monkeypatch)
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "botapi", "type": "botapi", "enable": True},
        {"id": "aiocqhttp_main", "tokens": [], "enable": True},
    ]})
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
    a = _adapter(monkeypatch)
    a._active_platforms = set()
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "botapi", "type": "botapi", "enable": True},
        {"id": "aiocqhttp_main", "tokens": ["tok"], "enable": False},
    ]})
    committed = []

    async def fake_persist(key, mid, text):
        pass
    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)

    def fake_commit(event):
        committed.append(event)
    a.commit_event = fake_commit

    await routes_mod.submit_inbound(a, "tok", "hi")
    assert committed[0].unified_msg_origin == "botapi:FriendMessage:tok"
