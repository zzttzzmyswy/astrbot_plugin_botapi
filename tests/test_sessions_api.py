# tests/test_sessions_api.py
# Phone API 会话 CRUD 端点测试（routes.py）+ 纯逻辑测试（sessions.py）
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi import sessions as S
from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi import routes as routes_mod


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


# ── 纯逻辑测试（来自 task brief，verbatim）──

def _adapter(monkeypatch):
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
    a._put = lambda q, e: None
    return a


def test_create_session_appends(monkeypatch):
    a = _adapter(monkeypatch)
    s = S.sessions_list(a, "tok")
    s.append({"id": "abc", "name": "工作", "created_at": 2})
    S.save_sessions(a, "tok", s)
    names = [x["name"] for x in S.sessions_list(a, "tok")]
    assert names == ["默认会话", "工作"]


# ── Quart test_client 端点测试（参照 test_routes_stream.py::_make_adapter）──

def _make_adapter(monkeypatch):
    """Fully wired BotApiAdapter with Quart app + all routes registered."""
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.cfg = SimpleNamespace(host="127.0.0.1", port=9000,
                                  tokens=["tok"], sessions={})
    adapter.config = {"id": "botapi", "tokens": ["tok"], "sessions": {}}
    adapter.platform_id = "botapi"
    adapter.client_self_id = "selfid"
    adapter._disabled_tokens = set()
    adapter._last_active = {}
    adapter._uploaded_files = {}
    adapter._sse_clients = {}
    adapter._token_to_origin = {}
    adapter._put = lambda q, e: None
    adapter.commit_event = lambda e: None
    from quart import Quart
    adapter.app = Quart("t")
    routes_mod._setup_routes(adapter)
    return adapter


@pytest.mark.asyncio
async def test_get_sessions_requires_auth(monkeypatch):
    """未带 token 访问 /sessions 必须 401。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.get("/api/v1/botapi/sessions")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_sessions_lists_default(monkeypatch):
    """GET /sessions 返回默认会话在最前 + default_id。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.get("/api/v1/botapi/sessions",
                         headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    body = await r.get_json()
    assert body["default_id"] == S.DEFAULT_SESSION_ID
    assert body["sessions"][0]["id"] == S.DEFAULT_SESSION_ID
    assert body["sessions"][0]["name"] == "默认会话"


@pytest.mark.asyncio
async def test_post_sessions_creates_and_persists(monkeypatch):
    """POST /sessions 建会话，id 非 default，持久化后仍在列表。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/sessions", json={"name": "工作"},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    new = (await r.get_json())["session"]
    assert new["name"] == "工作"
    assert new["id"] != S.DEFAULT_SESSION_ID
    assert "created_at" in new
    ids = [x["id"] for x in S.sessions_list(adapter, "tok")]
    assert ids == ["default", new["id"]]


@pytest.mark.asyncio
async def test_post_sessions_empty_name_400(monkeypatch):
    """空白 name 建会话必须 400 name_required。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/sessions", json={"name": "   "},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400
    assert (await r.get_json())["error"] == "name_required"


@pytest.mark.asyncio
async def test_post_sessions_limit_400(monkeypatch):
    """达到 MAX_SESSIONS 上限后建会话必须 400 session_limit。"""
    adapter = _make_adapter(monkeypatch)
    cur = [{"id": f"s{i}", "name": f"s{i}", "created_at": i}
           for i in range(S.MAX_SESSIONS)]
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_sessions_map({"tok": cur})
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/sessions", json={"name": "超了"},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400
    assert (await r.get_json())["error"] == "session_limit"


@pytest.mark.asyncio
async def test_rename_session(monkeypatch):
    """POST <sid>/rename 改名并持久化。"""
    adapter = _make_adapter(monkeypatch)
    s = S.sessions_list(adapter, "tok")
    s.append({"id": "abc", "name": "旧名", "created_at": 1})
    S.save_sessions(adapter, "tok", s)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/sessions/abc/rename",
                          json={"name": "新名"},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert (await r.get_json())["message"] == "会话已重命名"
    names = {x["name"] for x in S.sessions_list(adapter, "tok")}
    assert "新名" in names and "旧名" not in names


@pytest.mark.asyncio
async def test_rename_unknown_404(monkeypatch):
    """未知 sid 改名必须 404 not_found。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/sessions/nope/rename",
                          json={"name": "x"},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 404
    assert (await r.get_json())["error"] == "not_found"


@pytest.mark.asyncio
async def test_rename_empty_name_400(monkeypatch):
    """空白 name 改名必须 400 name_required。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/sessions/default/rename",
                          json={"name": "  "},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400
    assert (await r.get_json())["error"] == "name_required"


@pytest.mark.asyncio
async def test_delete_session_removes(monkeypatch):
    """POST <sid>/delete 删除会话，默认会话仍派生在列。"""
    adapter = _make_adapter(monkeypatch)
    s = S.sessions_list(adapter, "tok")
    s.append({"id": "abc", "name": "工作", "created_at": 1})
    S.save_sessions(adapter, "tok", s)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/sessions/abc/delete",
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert (await r.get_json())["message"] == "会话已删除"
    ids = [x["id"] for x in S.sessions_list(adapter, "tok")]
    assert ids == ["default"]


@pytest.mark.asyncio
async def test_delete_default_400_not_reaching_delete_session(monkeypatch):
    """删除默认会话必须 400 default_not_deletable，且不触达 delete_session。"""
    adapter = _make_adapter(monkeypatch)
    calls = {"n": 0}

    async def fake_delete(a, tok, sid):
        calls["n"] += 1

    monkeypatch.setattr(S, "delete_session", fake_delete)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/sessions/default/delete",
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400
    assert (await r.get_json())["error"] == "default_not_deletable"
    assert calls["n"] == 0   # delete_session 不应被调用


@pytest.mark.asyncio
async def test_post_sessions_non_string_name_400(monkeypatch):
    """数字 name 建会话必须 400 name_required（不是 500）。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/sessions", json={"name": 123},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400
    assert (await r.get_json())["error"] == "name_required"


@pytest.mark.asyncio
async def test_rename_non_string_name_400(monkeypatch):
    """数字 name 改名必须 400 name_required（不是 500）。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/sessions/default/rename",
                          json={"name": 123},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400
    assert (await r.get_json())["error"] == "name_required"


# ── 未知 session_id 必须 404（IMPORTANT 2 回归）──

@pytest.mark.asyncio
async def test_stream_unknown_session_404(monkeypatch):
    """GET /stream 未知 session_id 必须 404 session_not_found（不是 500）。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.get("/api/v1/botapi/stream?session_id=nope",
                         headers={"Authorization": "Bearer tok"})
    assert r.status_code == 404
    assert (await r.get_json())["error"] == "session_not_found"


@pytest.mark.asyncio
async def test_history_unknown_session_404(monkeypatch):
    """GET /history 未知 session_id 必须 404 session_not_found（不是 500）。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.get("/api/v1/botapi/history?session_id=nope",
                         headers={"Authorization": "Bearer tok"})
    assert r.status_code == 404
    assert (await r.get_json())["error"] == "session_not_found"


@pytest.mark.asyncio
async def test_message_unknown_session_404(monkeypatch):
    """POST /message 未知 session_id 必须 404 session_not_found（不是 500）。"""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/message",
                          json={"text": "hi", "session_id": "nope"},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 404
    assert (await r.get_json())["error"] == "session_not_found"


@pytest.mark.asyncio
async def test_message_known_session_ok(monkeypatch):
    """POST /message 已知 session_id 仍正常返回 message_id（确认 404 未误伤有效会话）。"""
    adapter = _make_adapter(monkeypatch)
    cur = S.sessions_list(adapter, "tok")
    cur.append({"id": "abc", "name": "工作", "created_at": 1})
    S.save_sessions(adapter, "tok", cur)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/message",
                          json={"text": "hi", "session_id": "abc"},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    body = await r.get_json()
    assert "message_id" in body



