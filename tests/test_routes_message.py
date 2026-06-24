import asyncio
from types import SimpleNamespace

import pytest


def _make_adapter_with_app(monkeypatch):
    """Construct a fully wired BotApiAdapter with Quart app and routes registered."""
    from astrbot_plugin_botapi.adapter import BotApiAdapter

    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.cfg = SimpleNamespace(host="127.0.0.1", port=9000, tokens=["secret-tok"])
    adapter.config = {"id": "botapi", "tokens": ["secret-tok"]}
    adapter.platform_id = "botapi"
    adapter._disabled_tokens = set()
    adapter._last_active = {}
    adapter._uploaded_files = {}
    adapter._sse_clients = {}
    adapter._media_enabled = True
    adapter._serializer = SimpleNamespace()
    adapter.client_self_id = "selfid"
    adapter._token_to_origin = {}
    committed = []
    adapter.commit_event = lambda e: committed.append(e)

    from quart import Quart
    adapter.app = Quart("t")

    from astrbot_plugin_botapi import routes as routes_mod
    routes_mod._setup_routes(adapter)

    adapter._committed = committed
    return adapter


@pytest.mark.asyncio
async def test_message_requires_auth(monkeypatch):
    """POST /api/v1/botapi/message without token must return 401."""
    adapter = _make_adapter_with_app(monkeypatch)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/message", json={"text": "hi"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_message_returns_message_id_only(monkeypatch):
    """POST /api/v1/botapi/message must return only message_id (no reply)
    and must set enable_streaming=True on the committed event."""
    adapter = _make_adapter_with_app(monkeypatch)

    import astrbot_plugin_botapi.history as hist

    async def _stub(*a, **k):
        return None

    monkeypatch.setattr(hist, "persist_inbound_text", _stub)

    client = adapter.app.test_client()
    r = await client.post(
        "/api/v1/botapi/message",
        json={"text": "hi"},
        headers={"Authorization": "Bearer secret-tok"},
    )
    assert r.status_code == 200
    body = await r.get_json()
    assert "message_id" in body
    assert "reply" not in body
    assert len(adapter._committed) == 1
    evt = adapter._committed[0]
    assert evt.get_extra("enable_streaming") is True
