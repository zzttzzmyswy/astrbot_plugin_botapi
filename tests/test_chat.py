# tests/test_chat.py
import hashlib

import pytest

from astrbot_plugin_botapi.main import BotApiStar

_hash = BotApiStar._hash_tok


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """隔离插件配置单例（submit_inbound 的 resolve_sid 读 sessions map）。"""
    import os
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        import json
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [],
                   "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


# ── Task 1: submit_inbound 共享 helper ──


def _fake_adapter():
    from types import SimpleNamespace

    class _A:
        client_self_id = "selfid"
        platform_id = "botapi"
        _uploaded_files = {}
        config = {"id": "botapi", "tokens": ["t1"], "nicknames": {}, "sessions": {}}
        cfg = SimpleNamespace(tokens=["t1"], nicknames={}, sessions={})

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
    # session_id 是裸 scoped key（默认=t1）；AstrMessageEvent 会拼成完整 umo
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
    adapter.config = {"id": "botapi", "tokens": list(tokens), "nicknames": {}, "sessions": {}}
    adapter.platform_id = "botapi"
    from astrbot_plugin_botapi.runtime import runtime

    rt = runtime()
    rt.adapter = adapter
    s = BotApiStar.__new__(BotApiStar)
    return s


@pytest.mark.asyncio
async def test_do_chat_happy(monkeypatch):
    s = _star_with_tokens(["t1"])

    async def fake_submit(adapter, token, text, session_id=""):
        assert token == "t1" and text == "你好" and session_id == ""
        return "botapi_xxx"

    monkeypatch.setattr("astrbot_plugin_botapi.routes.submit_inbound", fake_submit)
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


# ── Task 3: admin _do_history（读 conversation_manager）──


@pytest.mark.asyncio
async def test_do_history_happy(monkeypatch):
    s = _star_with_tokens(["t1"])

    async def fake_get(rt, pid, tok, limit):
        assert tok == "t1" and limit == 50
        return [{"message_id": "0", "role": "assistant", "type": "text",
                 "content": "hi", "timestamp": 0}]

    monkeypatch.setattr("astrbot_plugin_botapi.history.get_conversation_messages", fake_get)
    res = await s._do_history(_hash("t1"), since="5", limit=50, session_id="")
    assert res["status"] == "ok"
    assert res["data"]["messages"][0]["content"] == "hi"
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

    async def fake_get(rt, pid, tok, limit):
        seen["limit"] = limit
        return []

    monkeypatch.setattr("astrbot_plugin_botapi.history.get_conversation_messages", fake_get)
    await s._do_history(_hash("t1"), limit="9999", session_id="")
    assert seen["limit"] == 200


@pytest.mark.asyncio
async def test_do_history_adapter_not_ready():
    from astrbot_plugin_botapi.runtime import runtime

    rt = runtime()
    rt.adapter = None
    s = BotApiStar.__new__(BotApiStar)
    res = await s._do_history(_hash("t1"))
    assert res["message"] == "适配器未就绪"
