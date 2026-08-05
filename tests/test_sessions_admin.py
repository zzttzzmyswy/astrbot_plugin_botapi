# tests/test_sessions_admin.py
# Task 5: Web 会话路由 + 清空/删除联动 + stats/disconnect scoped 聚合
import hashlib
import asyncio
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar
from astrbot_plugin_botapi.runtime import runtime as _get_runtime
from astrbot_plugin_botapi import sessions as S


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
    """重置插件配置单例 → tmp_path（sessions 数据源）。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    import json, os
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [], "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def _make_star(monkeypatch, tokens=None, sessions=None):
    """参照 test_admin_handlers.py::_make_star：带 sessions 存储 + _sse_clients。"""
    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    all_s = dict(sessions or {})
    adapter = SimpleNamespace(
        cfg=SimpleNamespace(tokens=list(tokens or []),
                            sessions=dict(all_s)),
        config={"id": "botapi", "tokens": list(tokens or []),
                "sessions": dict(all_s)},
        platform_id="botapi",
        _sse_clients={},
        _disabled_tokens=set(),
        _last_active={},
        _token_to_origin={},
        _put=lambda q, evt: None,
        unbind_token=lambda t: None,   # _do_delete 调用（真实 adapter 自带落盘）
        binding_platform_for=lambda t: None,   # _do_stats bound_platform（本文件不断言绑定）
    )
    # 数据源写插件配置单例（tokens/sessions 全局读取）
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_tokens(list(tokens or []))
    pc.set_sessions_map(all_s)
    from astrbot_plugin_botapi import runtime as rt_mod

    rt = rt_mod.runtime()
    rt.adapter = adapter
    return star, adapter, registered


def _fake_context():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()

        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, handler, methods, desc))

    return FakeContext(), registered


# ── 路由注册 ──


def test_session_admin_routes_registered():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()

        def register_web_api(self, route, handler, methods, desc):
            registered.append(route)

    BotApiStar(FakeContext(), None)
    assert any(r.endswith("/sessions/<token_hash>") for r in registered)
    assert any("rename" in r for r in registered)
    assert any("delete" in r for r in registered)
    assert any(r.endswith("/sessions/<token_hash>/disconnect") for r in registered)
    assert any(r.endswith("/sessions/<token_hash>/clear") for r in registered)


# ── _do_clear 按会话作用域清空 ──


@pytest.mark.asyncio
async def test_do_clear_uses_scoped_umo(monkeypatch):
    """按会话作用域清空：session_id 命中 → umo 为 token:会话。"""
    star, adapter, _ = _make_star(
        monkeypatch, tokens=["tok"], sessions={"tok": [{"id": "abc", "name": "工作", "created_at": 1}]}
    )

    class FakeCM:
        async def new_conversation(self, umo):
            self.umo = umo

    cm = FakeCM()
    from astrbot_plugin_botapi import runtime as rt_mod
    rt_mod.runtime().conversation_manager = cm
    res = await star._do_clear(_hash("tok"), session_id="abc")
    assert res["status"] == "ok"
    assert cm.umo == "botapi:FriendMessage:tok:abc"

@pytest.mark.asyncio
async def test_do_clear_default_session_uses_token_umo(monkeypatch):
    """默认会话 clear → umo 退化为 token 基座（兼容）。"""
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"])

    class FakeCM:
        async def new_conversation(self, umo):
            self.umo = umo

    cm = FakeCM()
    from astrbot_plugin_botapi import runtime as rt_mod
    rt_mod.runtime().conversation_manager = cm
    res = await star._do_clear(_hash("tok"), session_id="")
    assert res["status"] == "ok"
    assert cm.umo == "botapi:FriendMessage:tok"


@pytest.mark.asyncio
async def test_do_clear_unknown_session_error(monkeypatch):
    """未知 session_id clear → 报错不调用 new_conversation。"""
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"])
    calls = []

    class FakeCM:
        async def new_conversation(self, umo):
            calls.append(umo)

    from astrbot_plugin_botapi import runtime as rt_mod
    rt_mod.runtime().conversation_manager = FakeCM()
    res = await star._do_clear(_hash("tok"), session_id="nope")
    assert res["status"] == "error"
    assert calls == []


# ── _do_sessions / _do_create_session / _do_rename_session / _do_delete_session ──


@pytest.mark.asyncio
async def test_do_sessions_lists_with_default(monkeypatch):
    star, adapter, _ = _make_star(
        monkeypatch, tokens=["tok"], sessions={"tok": [{"id": "abc", "name": "工作", "created_at": 1}]}
    )
    res = await star._do_sessions(_hash("tok"))
    assert res["status"] == "ok"
    ids = [x["id"] for x in res["data"]["sessions"]]
    assert ids == ["default", "abc"]


@pytest.mark.asyncio
async def test_do_sessions_unknown_account(monkeypatch):
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"])
    res = await star._do_sessions("deadbeef")
    assert res["status"] == "error"
    assert res["message"] == "未找到账户"


@pytest.mark.asyncio
async def test_do_create_session(monkeypatch):
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"])
    from astrbot_plugin_botapi import plugin_conf as pc
    res = await star._do_create_session(_hash("tok"), "工作")
    assert res["status"] == "ok"
    sid = res["data"]["session"]["id"]
    assert sid != S.DEFAULT_SESSION_ID
    ids = [x["id"] for x in S.sessions_list(adapter, "tok")]
    assert ids == ["default", sid]
    # save_sessions 落盘到插件配置 sessions map
    assert sid in [x["id"] for x in pc.get_sessions_map()["tok"]]


@pytest.mark.asyncio
async def test_do_create_session_empty_name(monkeypatch):
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"])
    res = await star._do_create_session(_hash("tok"), "   ")
    assert res["status"] == "error"
    assert res["message"] == "会话名称不能为空"


@pytest.mark.asyncio
async def test_do_rename_session(monkeypatch):
    star, adapter, _ = _make_star(
        monkeypatch, tokens=["tok"], sessions={"tok": [{"id": "abc", "name": "旧名", "created_at": 1}]}
    )
    from astrbot_plugin_botapi import plugin_conf as pc
    res = await star._do_rename_session(_hash("tok"), "abc", "新名")
    assert res["status"] == "ok"
    names = {x["name"] for x in S.sessions_list(adapter, "tok")}
    assert "新名" in names and "旧名" not in names
    # save_sessions 落盘到插件配置 sessions map（含派生 default 在最前）
    stored = {x["id"]: x["name"] for x in pc.get_sessions_map()["tok"]}
    assert stored["abc"] == "新名"


@pytest.mark.asyncio
async def test_do_rename_session_unknown(monkeypatch):
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"])
    res = await star._do_rename_session(_hash("tok"), "nope", "x")
    assert res["status"] == "error"
    assert res["message"] == "未找到会话"


@pytest.mark.asyncio
async def test_do_delete_session_calls_delete_session(monkeypatch):
    """_do_delete_session 拒删默认 + 调用 sessions.delete_session（清 conversation/SSE/存储）。"""
    star, adapter, _ = _make_star(
        monkeypatch, tokens=["tok"], sessions={"tok": [{"id": "abc", "name": "工作", "created_at": 1}]}
    )
    calls = []
    q = asyncio.Queue(maxsize=1)
    adapter._sse_clients = {"tok:abc": [q]}
    adapter._put = lambda qq, evt: qq.put_nowait(evt)

    class FakeCM:
        async def delete_conversations_by_user_id(self, umo):
            calls.append(("del", umo))

    from astrbot_plugin_botapi import runtime as rt_mod
    rt_mod.runtime().conversation_manager = FakeCM()

    # 删除默认 → 拒绝，且不触达 delete_session
    res = await star._do_delete_session(_hash("tok"), S.DEFAULT_SESSION_ID)
    assert res["status"] == "error"
    assert "默认" in res["message"]
    assert calls == []

    # 删除普通会话 → 联动清理
    res = await star._do_delete_session(_hash("tok"), "abc")
    assert res["status"] == "ok"
    ids = [x["id"] for x in S.sessions_list(adapter, "tok")]
    assert ids == ["default"]
    assert calls == [("del", "botapi:FriendMessage:tok:abc")]
    assert await q.get() is None   # SSE 队列收到关闭哨兵


# ── _do_chat / _do_history 带 session_id 路由 ──


@pytest.mark.asyncio
async def test_do_chat_scoped_session_id(monkeypatch):
    star, adapter, _ = _make_star(
        monkeypatch, tokens=["tok"], sessions={"tok": [{"id": "abc", "name": "x", "created_at": 1}]}
    )
    seen = {}

    async def fake_submit(a, token, text, session_id=""):
        seen["sid"] = session_id
        seen["text"] = text
        return "botapi_1"

    monkeypatch.setattr("astrbot_plugin_botapi.routes.submit_inbound", fake_submit)
    res = await star._do_chat(_hash("tok"), "你好", session_id="abc")
    assert res["status"] == "ok"
    assert seen["sid"] == "abc" and seen["text"] == "你好"


@pytest.mark.asyncio
async def test_do_chat_unknown_session_error(monkeypatch):
    """未知 session_id chat → 报错而非 500。"""
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"])
    res = await star._do_chat(_hash("tok"), "你好", session_id="nope")
    assert res["status"] == "error"
    assert res["message"] == "未找到会话"


@pytest.mark.asyncio
async def test_do_history_scoped_session_id(monkeypatch):
    star, adapter, _ = _make_star(
        monkeypatch, tokens=["tok"], sessions={"tok": [{"id": "abc", "name": "x", "created_at": 1}]}
    )
    seen = {}

    async def fake_get(rt, pid, tok, limit):
        seen["key"] = tok
        seen["limit"] = limit
        return []

    monkeypatch.setattr("astrbot_plugin_botapi.history.get_conversation_messages", fake_get)
    await star._do_history(_hash("tok"), since=None, limit=50, session_id="abc")
    assert seen["key"] == "tok:abc"   # scoped_key 作为 user_id 基座


# ── _do_stats / _do_disconnect / _do_delete scoped 聚合 ──


@pytest.mark.asyncio
async def test_stats_aggregates_scoped_sse(monkeypatch):
    """同一 token 的默认 + 分会话队列聚合为 online / sse_connections。"""
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"])
    q_d = asyncio.Queue(maxsize=1)
    q_a = asyncio.Queue(maxsize=1)
    adapter._sse_clients = {"tok": [q_d], "tok:abc": [q_a]}
    res = await star._do_stats()
    per = res["data"]["per_account"]
    assert per[0]["online"] is True
    assert per[0]["sse_connections"] == 2


@pytest.mark.asyncio
async def test_disconnect_aggregates_scoped_sse(monkeypatch):
    """断开会话：所有分区队列收到 SESSION_KICKED，且从 _sse_clients 移除。"""
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"])
    q_d = asyncio.Queue(maxsize=1)
    q_a = asyncio.Queue(maxsize=1)
    adapter._sse_clients = {"tok": [q_d], "tok:abc": [q_a]}
    adapter._put = lambda qq, evt: qq.put_nowait(evt)
    res = await star._do_disconnect(_hash("tok"))
    assert res["status"] == "ok"
    got = []
    for q in (q_d, q_a):
        evt = q.get_nowait()
        got.append(evt.event_type)
    assert got == ["error", "error"]
    assert adapter._sse_clients == {}   # scoped 队列全部清理


@pytest.mark.asyncio
async def test_do_delete_aggregates_scoped_sse(monkeypatch):
    """删除账户：scoped 队列也收到关闭哨兵（不只默认 token 分区）。"""
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"])
    q_d = asyncio.Queue(maxsize=1)
    q_a = asyncio.Queue(maxsize=1)
    adapter._sse_clients = {"tok": [q_d], "tok:abc": [q_a]}
    adapter._put = lambda qq, evt: qq.put_nowait(evt)
    res = await star._do_delete(_hash("tok"))
    assert res["status"] == "ok"
    assert q_d.get_nowait() is None   # RED 期旧实现只 pop 默认 key → 此处 QueueEmpty 快速失败而非挂死
    assert q_a.get_nowait() is None   # scoped 队列也必须收到关闭哨兵
    assert adapter._sse_clients == {}
