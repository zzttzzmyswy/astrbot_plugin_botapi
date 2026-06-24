import asyncio
from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.platform import MessageType, PlatformMetadata
from astrbot_plugin_botapi.event import BotApiMessageEvent
from astrbot_plugin_botapi.models import SSEEvent


def _setup(monkeypatch, mid="m1", token="tok"):
    received = []

    class FakeAdapter:
        platform_id = "botapi"
        _sse_clients = {token: []}

        async def _broadcast_to(self, t, evt):
            received.append(evt)

        async def _push_media(self, chain, t, m):
            received.append(SSEEvent("message", {"_push_media": m}))

    class FakeSerializer:
        async def serialize_chain(self, message, event):
            return {"message_id": mid, "role": "assistant", "type": "text",
                    "content": message.get_plain_text(), "timestamp": 0}

    persisted = []

    async def fake_persist_text(token, mid, text, kind):
        persisted.append((kind, text))

    async def fake_persist_thinking(token, mid, text):
        persisted.append(("thinking", text))

    import astrbot_plugin_botapi.event as evmod
    monkeypatch.setattr(evmod, "persist_assistant_text", fake_persist_text)
    monkeypatch.setattr(evmod, "persist_assistant_thinking", fake_persist_thinking)

    adapter = FakeAdapter()
    adapter._serializer = FakeSerializer()

    msg_obj = SimpleNamespace(message_id=mid, sender=SimpleNamespace(user_id=token),
                              type=MessageType.FRIEND_MESSAGE)
    event = BotApiMessageEvent(
        message_str="hi", message_obj=msg_obj,
        platform_meta=PlatformMetadata(name="botapi", description="BotAPI", id="botapi"),
        session_id=token, adapter=adapter)
    return event, received, persisted


@pytest.mark.asyncio
async def test_send_tool_call_status(monkeypatch):
    event, received, persisted = _setup(monkeypatch)
    chain = MessageChain()
    chain.type = "tool_call"
    chain.message("🔨 调用工具: web_search")
    await event.send(chain)
    assert received[0].event_type == "message"
    assert received[0].data["subtype"] == "tool_status"
    assert "web_search" in received[0].data["content"]
    assert received[0].data["final"] is False
    assert ("tool_status", received[0].data["content"]) in persisted


@pytest.mark.asyncio
async def test_send_normal_reply(monkeypatch):
    event, received, persisted = _setup(monkeypatch)
    chain = MessageChain([Plain("hello answer")])
    await event.send(chain)
    # 文本 final + push_media + persist final
    finals = [e for e in received if e.event_type == "message" and e.data.get("final")]
    assert len(finals) == 1
    assert finals[0].data["content"] == "hello answer"
    assert ("final", "hello answer") in persisted


@pytest.mark.asyncio
async def test_send_none_guard(monkeypatch):
    event, received, persisted = _setup(monkeypatch)
    await event.send(None)   # 不应崩
    assert received == []
    assert persisted == []


@pytest.mark.asyncio
async def test_send_streaming_sequence(monkeypatch):
    event, received, persisted = _setup(monkeypatch)

    async def gen():
        r = MessageChain([Plain("思考中")])
        r.type = "reasoning"
        yield r
        yield MessageChain([Plain("答案")])   # type 默认 None → plain 增量

    await event.send_streaming(gen())
    types = [e.event_type for e in received]
    assert "thinking" in types
    finals = [e for e in received if e.event_type == "message" and e.data.get("final")]
    assert len(finals) == 1
    assert finals[0].data["content"] == "答案"
    assert ("final", "答案") in persisted
    assert ("thinking", "思考中") in persisted


@pytest.mark.asyncio
async def test_send_streaming_none_and_break(monkeypatch):
    event, received, persisted = _setup(monkeypatch)

    async def gen():
        yield None
        b = MessageChain([]); b.type = "break"; yield b
        t = MessageChain([Plain("x")]); yield t

    await event.send_streaming(gen())   # 不崩；break 切段；最终 final "x"
    finals = [e for e in received if e.event_type == "message" and e.data.get("final")]
    assert finals and finals[0].data["content"] == "x"
