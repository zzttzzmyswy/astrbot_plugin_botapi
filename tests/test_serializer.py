from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image
from astrbot_plugin_botapi.serializer import MessageSerializer


def _event(mid="m1", token="tok"):
    return SimpleNamespace(message_obj=SimpleNamespace(message_id=mid), token=token,
                           adapter=SimpleNamespace(_media_enabled=True, _sse_clients={}))


@pytest.mark.asyncio
async def test_serialize_chain_text():
    ser = MessageSerializer(_media_enabled=True)
    chain = MessageChain([Plain("hello "), Plain("world")])
    payload = await ser.serialize_chain(chain, _event())
    assert payload["type"] == "text"
    assert payload["content"] == "hello world"
    assert payload["message_id"] == "m1"
    assert payload["role"] == "assistant"


@pytest.mark.asyncio
async def test_media_disabled_returns_none():
    ser = MessageSerializer(_media_enabled=False)
    comp = Image.fromFileSystem("/nonexistent.png")
    url = await ser._media_url(comp)
    assert url is None


@pytest.mark.asyncio
async def test_media_url_calls_register(monkeypatch):
    ser = MessageSerializer(_media_enabled=True)
    comp = Image.fromFileSystem("/nonexistent.png")
    called = {}

    async def fake_register(self):
        called["yes"] = True
        return "https://dash/api/file/tok123"

    monkeypatch.setattr(Image, "register_to_file_service", fake_register, raising=True)
    url = await ser._media_url(comp)
    assert url == "https://dash/api/file/tok123"
    assert called.get("yes")
