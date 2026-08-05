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
    # AstrMessageEvent 会再拼 {pid}:FriendMessage: 前缀，故 msg.session_id 应为裸 scoped key
    assert captured["session_id"] == "tok"
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
    # 分会话：msg.session_id 为裸 scoped key "tok:abc"，AstrMessageEvent 拼前缀成完整 umo
    assert captured["session_id"] == "tok:abc"
    assert captured["key"] == "tok:abc"


@pytest.mark.asyncio
async def test_submit_inbound_unified_origin_single_prefix(monkeypatch):
    """回归：AstrMessageEvent.unified_msg_origin 必须只有一层 {pid}:FriendMessage: 前缀。

    旧 bug：submit_inbound 把完整 umo 传给 AstrMessageEvent 的 session_id，
    导致拼出 botapi:FriendMessage:botapi:FriendMessage:{token} 双重前缀，
    会话上下文路由错乱、管理页 Session ID 显示错误。
    """
    from astrbot_plugin_botapi import routes as routes_mod
    a = _adapter(monkeypatch)

    async def fake_persist(key, mid, text):
        pass

    monkeypatch.setattr(routes_mod, "persist_inbound_text", fake_persist)
    committed = []

    def fake_commit(event):
        committed.append(event)

    a.commit_event = fake_commit
    # 先建分会话 abc（submit_inbound 的 resolve_sid 需要它存在）
    cur = S.sessions_list(a, "tok")
    cur.append({"id": "abc", "name": "x", "created_at": 1})
    S.save_sessions(a, "tok", cur)
    # 默认会话 + 分会话各验证一次
    await routes_mod.submit_inbound(a, "tok", "hi")
    await routes_mod.submit_inbound(a, "tok", "hi2", session_id="abc")
    assert len(committed) == 2
    # 完整 umo 由 AstrMessageEvent 拼出，只一层前缀
    assert committed[0].unified_msg_origin == "botapi:FriendMessage:tok"
    assert committed[1].unified_msg_origin == "botapi:FriendMessage:tok:abc"
    # 不能被双重前缀污染
    assert committed[0].unified_msg_origin.count("botapi:FriendMessage:") == 1
    assert committed[1].unified_msg_origin.count("botapi:FriendMessage:") == 1


@pytest.mark.asyncio
async def test_event_broadcast_carries_session_id(monkeypatch):
    import asyncio
    from astrbot_plugin_botapi.event import BotApiMessageEvent
    from astrbot_plugin_botapi.models import SSEEvent
    a = _adapter(monkeypatch)
    a._sse_clients = {}
    q = asyncio.Queue(maxsize=10)
    a._sse_clients["tok:abc"] = [q]
    # _broadcast 走 adapter._broadcast_to(scoped, evt) → adapter._put(q, evt)
    a._put = lambda qq, evt: qq.put_nowait(evt)

    from astrbot.api.platform import MessageType
    msg = SimpleNamespace(sender=SimpleNamespace(user_id="tok"),
                          type=MessageType.FRIEND_MESSAGE)
    meta = SimpleNamespace(id="botapi")
    # BotApiMessageEvent 收到的 session_id 是裸 scoped key（"tok:abc"），
    # AstrMessageEvent 会再拼 {pid}:FriendMessage: 前缀成完整 umo。
    ev = BotApiMessageEvent("hi", msg, meta, "tok:abc", a)
    assert ev.sid == "abc"
    # 真正调用 _broadcast：验证 scope 路由到 "tok:abc" 队列 + session_id 注入
    await ev._broadcast(SSEEvent("message", {"x": 1}))
    got = await q.get()
    assert got.data["session_id"] == "abc"
    assert got.data["x"] == 1


@pytest.mark.asyncio
async def test_event_broadcast_default_session_scopes_to_token(monkeypatch):
    import asyncio
    from astrbot_plugin_botapi.event import BotApiMessageEvent
    from astrbot_plugin_botapi.models import SSEEvent
    a = _adapter(monkeypatch)
    a._sse_clients = {}
    q = asyncio.Queue(maxsize=10)
    a._sse_clients["tok"] = [q]
    a._put = lambda qq, evt: qq.put_nowait(evt)

    from astrbot.api.platform import MessageType
    msg = SimpleNamespace(sender=SimpleNamespace(user_id="tok"),
                          type=MessageType.FRIEND_MESSAGE)
    meta = SimpleNamespace(id="botapi")
    ev = BotApiMessageEvent("hi", msg, meta, "tok", a)
    assert ev.sid == "default"
    await ev._broadcast(SSEEvent("message", {"x": 2}))
    got = await q.get()
    assert got.data["session_id"] == ""
    assert got.data["x"] == 2
    assert q.empty()  # 不会投到其它队列
