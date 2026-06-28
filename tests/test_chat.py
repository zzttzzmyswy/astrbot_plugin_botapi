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
