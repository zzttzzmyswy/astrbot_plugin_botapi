# tests/test_chat.py
import hashlib

import pytest

from astrbot_plugin_botapi.main import BotApiStar

_hash = BotApiStar._hash_tok


# ── Task 1: submit_inbound 共享 helper ──


def _fake_adapter():
    from types import SimpleNamespace

    class _A:
        client_self_id = "selfid"
        platform_id = "botapi"
        _uploaded_files = {}

        def __init__(self):
            self.committed = []
            self._token_to_origin = {}

        def meta(self):
            return SimpleNamespace(id="botapi")

        def commit_event(self, ev):
            self.committed.append(ev)

    return _A()


@pytest.mark.asyncio
async def test_submit_inbound_builds_and_commits(monkeypatch):
    from astrbot_plugin_botapi import routes as R

    seen = {}

    async def fake_persist(token, mid, text):
        seen["persist"] = (token, mid, text)

    monkeypatch.setattr(R, "persist_inbound_text", fake_persist)

    adapter = _fake_adapter()
    mid = await R.submit_inbound(adapter, "t1", "你好")
    assert mid.startswith("botapi_")
    assert seen["persist"] == ("t1", mid, "你好")
    assert adapter.committed, "event 应被 commit"
    evt = adapter.committed[0]
    assert evt.session_id == "t1"
    assert evt.message_obj.sender.user_id == "t1"
    assert evt.message_obj.message_str == "你好"
    assert evt.get_extra("enable_streaming") is True


# ── Task 2: admin _do_chat ──


def _star_with_tokens(tokens):
    """BotApiStar 不跑 __init__（避免注册 web_api），注入带 tokens 的假 adapter。"""
    from types import SimpleNamespace

    class _A:
        pass

    adapter = _A()
    adapter.cfg = SimpleNamespace(tokens=list(tokens), nicknames={})
    adapter.platform_id = "botapi"
    from astrbot_plugin_botapi.runtime import runtime

    rt = runtime()
    rt.adapter = adapter
    s = BotApiStar.__new__(BotApiStar)
    return s


@pytest.mark.asyncio
async def test_do_chat_happy(monkeypatch):
    s = _star_with_tokens(["t1"])

    async def fake_submit(adapter, token, text):
        assert token == "t1" and text == "你好"
        return "botapi_xxx"

    async def fake_get(pid, tok, since, limit):
        return [], False

    monkeypatch.setattr("astrbot_plugin_botapi.routes.submit_inbound", fake_submit)
    monkeypatch.setattr("astrbot_plugin_botapi.history.get_history", fake_get)
    res = await s._do_chat(_hash("t1"), "你好")
    assert res["status"] == "ok"
    assert res["data"]["message_id"] == "botapi_xxx"


@pytest.mark.asyncio
async def test_do_chat_unknown_account():
    s = _star_with_tokens(["t1"])
    res = await s._do_chat("deadbeef", "x")
    assert res["status"] == "error"
    assert res["message"] == "未找到账户"


@pytest.mark.asyncio
async def test_do_chat_empty_text():
    s = _star_with_tokens(["t1"])
    res = await s._do_chat(_hash("t1"), "   ")
    assert res["status"] == "error"
    assert res["message"] == "消息不能为空"


@pytest.mark.asyncio
async def test_do_chat_adapter_not_ready():
    from astrbot_plugin_botapi.runtime import runtime

    rt = runtime()
    rt.adapter = None
    s = BotApiStar.__new__(BotApiStar)
    res = await s._do_chat(_hash("t1"), "hi")
    assert res["message"] == "适配器未就绪"


# ── Task 3: admin _do_history ──


@pytest.mark.asyncio
async def test_do_history_happy(monkeypatch):
    s = _star_with_tokens(["t1"])

    async def fake_get(pid, tok, since, limit):
        assert tok == "t1" and since == "5" and limit == 50
        return [{"message_id": "6", "role": "assistant", "type": "text",
                 "content": "hi", "timestamp": 1}], False

    monkeypatch.setattr("astrbot_plugin_botapi.history.get_history", fake_get)
    res = await s._do_history(_hash("t1"), since="5", limit=50)
    assert res["status"] == "ok"
    assert res["data"]["messages"][0]["message_id"] == "6"
    assert res["data"]["has_more"] is False


@pytest.mark.asyncio
async def test_do_history_unknown_account():
    s = _star_with_tokens(["t1"])
    res = await s._do_history("deadbeef")
    assert res["status"] == "error"
    assert res["message"] == "未找到账户"


@pytest.mark.asyncio
async def test_do_history_limit_capped(monkeypatch):
    s = _star_with_tokens(["t1"])
    seen = {}

    async def fake_get(pid, tok, since, limit):
        seen["limit"] = limit
        return [], False

    monkeypatch.setattr("astrbot_plugin_botapi.history.get_history", fake_get)
    await s._do_history(_hash("t1"), limit="9999")
    assert seen["limit"] == 200


@pytest.mark.asyncio
async def test_do_history_adapter_not_ready():
    from astrbot_plugin_botapi.runtime import runtime

    rt = runtime()
    rt.adapter = None
    s = BotApiStar.__new__(BotApiStar)
    res = await s._do_history(_hash("t1"))
    assert res["message"] == "适配器未就绪"
