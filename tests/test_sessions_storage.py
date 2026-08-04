# tests/test_sessions_storage.py
from types import SimpleNamespace
import pytest

from astrbot_plugin_botapi.models import BotApiConfig
from astrbot_plugin_botapi import sessions as S


def _adapter(monkeypatch=None, sessions=None, bindings=None, active=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": ["tok"], "nicknames": {}, "sessions": sessions or {}}
    if bindings is not None:
        a.config["botapi_bindings"] = bindings
    a.cfg = SimpleNamespace(tokens=["tok"], nicknames={}, sessions=sessions or {})
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = set(active or ())
    if monkeypatch is not None:
        fake_cfg = {"platform": [{"id": "botapi", "sessions": sessions or {}}]}
        monkeypatch.setattr(S, "astrbot_config", fake_cfg)
    return a


def test_config_has_sessions_field():
    c = BotApiConfig()
    assert hasattr(c, "sessions")
    assert c.sessions == {}


def test_sessions_list_derives_default_first():
    a = _adapter()
    s = S.sessions_list(a, "tok")
    assert s[0]["id"] == S.DEFAULT_SESSION_ID
    assert s[0]["name"] == "默认会话"
    assert len(s) == 1
    # 不改存储
    assert a.config["sessions"] == {}


def test_sessions_list_keeps_existing_after_default():
    a = _adapter(sessions={"tok": [{"id": "abc", "name": "工作", "created_at": 1}]})
    ids = [x["id"] for x in S.sessions_list(a, "tok")]
    assert ids == ["default", "abc"]


def test_umo_and_scoped_key():
    a = _adapter()
    assert S.umo_for(a, "tok", "default") == "botapi:FriendMessage:tok"
    assert S.umo_for(a, "tok", "abc123") == "botapi:FriendMessage:tok:abc123"
    assert S.scoped_key_for(a, "tok", "default") == "tok"
    assert S.scoped_key_for(a, "tok", "abc123") == "tok:abc123"


def test_resolve_sid():
    a = _adapter(sessions={"tok": [{"id": "abc", "name": "x", "created_at": 1}]})
    assert S.resolve_sid(a, "tok", "") == "default"
    assert S.resolve_sid(a, "tok", None) == "default"
    assert S.resolve_sid(a, "tok", "default") == "default"
    assert S.resolve_sid(a, "tok", "abc") == "abc"
    with pytest.raises(LookupError):
        S.resolve_sid(a, "tok", "nope")


@pytest.mark.asyncio
async def test_delete_default_session_guard():
    a = _adapter(sessions={"tok": [{"id": "abc", "name": "x", "created_at": 1}]})
    with pytest.raises(LookupError):
        await S.delete_session(a, "tok", S.DEFAULT_SESSION_ID)
    # 未改动存储、未断 SSE
    assert S.sessions_list(a, "tok")[0]["id"] == S.DEFAULT_SESSION_ID
    assert a._sse_clients == {}
    assert "abc" in [x["id"] for x in a.config["sessions"]["tok"]]


def test_save_sessions_persists_config_cfg_global(monkeypatch):
    a = _adapter(monkeypatch)
    S.save_sessions(a, "tok", [{"id": "abc", "name": "工作", "created_at": 1}])
    assert a.config["sessions"]["tok"][0]["id"] == "abc"
    assert a.cfg.sessions["tok"][0]["id"] == "abc"
    # astrobot_config 平台子树被更新
    assert S.astrbot_config["platform"][0]["sessions"]["tok"][0]["name"] == "工作"


def test_sse_queues_for_aggregates_scoped():
    import asyncio
    a = _adapter()
    a._sse_clients = {
        "tok": [asyncio.Queue(maxsize=1)],
        "tok:abc": [asyncio.Queue(maxsize=1)],
        "other": [asyncio.Queue(maxsize=1)],
    }
    assert len(S.sse_queues_for(a, "tok")) == 2


@pytest.mark.asyncio
async def test_delete_session_removes_and_saves(monkeypatch):
    a = _adapter(monkeypatch, sessions={"tok": [{"id": "abc", "name": "x", "created_at": 1}]})
    calls = []
    import asyncio
    q = asyncio.Queue(maxsize=1)
    a._sse_clients = {"tok:abc": [q]}
    a._put = lambda qq, evt: qq.put_nowait(evt)

    class FakeCM:
        async def delete_conversations_by_user_id(self, umo):
            calls.append(umo)

    from astrbot_plugin_botapi.runtime import runtime
    rt = runtime()
    rt.conversation_manager = FakeCM()
    await S.delete_session(a, "tok", "abc")
    # 会话被移除，默认会话仍派生在列
    assert all(x["id"] != "abc" for x in S.sessions_list(a, "tok"))
    # 持久化结果：存储里不含 abc
    assert all(x["id"] != "abc" for x in a.config["sessions"]["tok"])
    assert all(x["id"] != "abc" for x in a.cfg.sessions["tok"])
    # 存储里也不含 default（只读派生，不写存储）
    assert all(x["id"] != S.DEFAULT_SESSION_ID for x in a.config["sessions"]["tok"])
    # SSE 队列收到关闭哨兵 None
    assert await q.get() is None
    # 未绑定 → 删裸 botapi UMO
    assert calls == ["botapi:FriendMessage:tok:abc"]


@pytest.mark.asyncio
async def test_delete_session_bound_deletes_bound_umo(monkeypatch):
    """绑定 token 的会话删除必须用绑定平台 UMO（{bound}:FriendMessage:botapi_tok:abc），
    否则 delete_conversations_by_user_id 精确匹配不到 → 静默 no-op、会话泄漏。"""
    a = _adapter(monkeypatch,
                 sessions={"tok": [{"id": "abc", "name": "x", "created_at": 1}]},
                 bindings={"tok": "aiocqhttp_main"}, active={"aiocqhttp_main"})
    calls = []
    import asyncio
    q = asyncio.Queue(maxsize=1)
    a._sse_clients = {"tok:abc": [q]}
    a._put = lambda qq, evt: qq.put_nowait(evt)

    class FakeCM:
        async def delete_conversations_by_user_id(self, umo):
            calls.append(umo)

    from astrbot_plugin_botapi.runtime import runtime
    rt = runtime()
    rt.conversation_manager = FakeCM()
    await S.delete_session(a, "tok", "abc")
    # 会话仍被移除（存储/SSE 逻辑不受影响）
    assert all(x["id"] != "abc" for x in S.sessions_list(a, "tok"))
    assert await q.get() is None
    # 删除的 conversation 是绑定平台 UMO（带 botapi_ 前缀），而非裸 botapi UMO
    assert calls == ["aiocqhttp_main:FriendMessage:botapi_tok:abc"]


@pytest.mark.asyncio
async def test_delete_session_bound_inactive_uses_botapi_umo(monkeypatch):
    """绑定目标平台不在活跃集合（绑定静默回退）→ 会话实际在 botapi 自身 UMO → 删该 UMO。"""
    a = _adapter(monkeypatch,
                 sessions={"tok": [{"id": "abc", "name": "x", "created_at": 1}]},
                 bindings={"tok": "dead_platform"})   # active 空集 → 绑定不生效
    calls = []
    import asyncio
    q = asyncio.Queue(maxsize=1)
    a._sse_clients = {"tok:abc": [q]}
    a._put = lambda qq, evt: qq.put_nowait(evt)

    class FakeCM:
        async def delete_conversations_by_user_id(self, umo):
            calls.append(umo)

    from astrbot_plugin_botapi.runtime import runtime
    rt = runtime()
    rt.conversation_manager = FakeCM()
    await S.delete_session(a, "tok", "abc")
    assert calls == ["botapi:FriendMessage:tok:abc"]
