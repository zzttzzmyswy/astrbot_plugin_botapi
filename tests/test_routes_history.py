# tests/test_routes_history.py
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi import routes as routes_mod
from astrbot_plugin_botapi import history as hist


@pytest.mark.asyncio
async def test_history_endpoint(monkeypatch):
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.cfg = SimpleNamespace(host="127.0.0.1", port=9000, tokens=["tok"])
    adapter.config = {"id": "botapi", "tokens": ["tok"]}
    adapter.platform_id = "botapi"
    adapter.client_self_id = "selfid"
    adapter._disabled_tokens = set()
    adapter._last_active = {}
    adapter._uploaded_files = {}
    adapter._sse_clients = {}
    adapter._media_enabled = True
    adapter._serializer = SimpleNamespace()
    adapter._token_to_origin = {}
    adapter.commit_event = lambda e: None
    from quart import Quart

    adapter.app = Quart("t")
    routes_mod._setup_routes(adapter)

    async def fake_get_history(pid, token, since=None, before=None, limit=50):
        return ([{"message_id": "1", "type": "text", "content": "a"}], False)

    monkeypatch.setattr(hist, "get_history", fake_get_history)

    client = adapter.app.test_client()
    r = await client.get(
        "/api/v1/botapi/history?since=0&limit=50",
        headers={"Authorization": "Bearer tok"},
    )
    assert r.status_code == 200
    body = await r.get_json()
    assert body["messages"][0]["content"] == "a"
    assert body["has_more"] is False
