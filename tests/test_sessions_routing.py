# tests/test_sessions_routing.py
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
    a.config = {"id": "botapi", "tokens": ["tok"], "nicknames": {}, "sessions": {}}
    a.cfg = SimpleNamespace(tokens=["tok"], nicknames={}, sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    a.client_self_id = "self"
    a._uploaded_files = {}
    a._serializer = SimpleNamespace()
    a.commit_event = lambda e: None
    monkeypatch.setattr(S, "astrbot_config", {"platform": []})
    return a


@pytest.mark.asyncio
async def test_submit_inbound_routes_to_session(monkeypatch):
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch)
    captured = {}

    async def fake_persist(key, mid, text):
        captured["key"] = key

    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)

    def fake_commit(event):
        captured["session_id"] = event.message_obj.session_id
        captured["sender"] = event.message_obj.sender.user_id

    a.commit_event = fake_commit
    await routes_mod.submit_inbound(a, "tok", "hi")
    assert captured["session_id"] == "botapi:FriendMessage:tok"
    assert captured["sender"] == "tok"
    assert captured["key"] == "tok"


@pytest.mark.asyncio
async def test_submit_inbound_scoped_sid(monkeypatch):
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch)
    cur = S.sessions_list(a, "tok")
    cur.append({"id": "abc", "name": "x", "created_at": 1})
    S.save_sessions(a, "tok", cur)
    captured = {}

    async def fake_persist(key, mid, text):
        captured["key"] = key

    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)

    def fake_commit(event):
        captured["session_id"] = event.message_obj.session_id

    a.commit_event = fake_commit
    await routes_mod.submit_inbound(a, "tok", "hi", session_id="abc")
    assert captured["session_id"] == "botapi:FriendMessage:tok:abc"
    assert captured["key"] == "tok:abc"


@pytest.mark.asyncio
async def test_event_broadcast_carries_session_id(monkeypatch):
    from astrbot_plugin_botapi.event import BotApiMessageEvent
    from astrbot_plugin_botapi.models import SSEEvent
    a = _adapter(monkeypatch)
    a._sse_clients = {}
    q = __import__("asyncio").Queue(maxsize=10)
    a._sse_clients["tok:abc"] = [q]

    async def fake_broadcast(scoped, evt):
        a._sse_clients[scoped][0].put_nowait(evt)

    # 用最小 fake message_obj
    from astrbot.api.platform import MessageType
    msg = SimpleNamespace(sender=SimpleNamespace(user_id="tok"),
                          type=MessageType.FRIEND_MESSAGE)
    meta = SimpleNamespace(id="botapi")
    ev = BotApiMessageEvent("hi", msg, meta, "botapi:FriendMessage:tok:abc", a)
    assert ev.sid == "abc"
