import asyncio
from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.platform import PlatformMetadata, MessageType
from astrbot_plugin_botapi.event import BotApiMessageEvent
import astrbot_plugin_botapi.event as evmod


@pytest.mark.asyncio
async def test_full_streaming_chain_to_sse(monkeypatch):
    """集成测试：commit_event -> send_streaming -> SSE 队列 + 持久化"""
    persisted = []

    async def fake_persist_text(token, mid, text, kind):
        persisted.append((kind, text))

    async def fake_persist_thinking(token, mid, text):
        persisted.append(("thinking", text))

    monkeypatch.setattr(evmod, "persist_assistant_text", fake_persist_text)
    monkeypatch.setattr(evmod, "persist_assistant_thinking", fake_persist_thinking)

    class FakeAdapter:
        platform_id = "botapi"
        _sse_clients = {"tok": []}

        async def _broadcast_to(self, t, evt):
            for q in self._sse_clients.get(t, []):
                self._put(q, evt)

        def _put(self, q, evt):
            q.put_nowait(evt)

        async def _push_media(self, chain, t, mid):
            pass

        class _S:
            _media_enabled = False
            async def serialize_chain(self, mc, event):
                mid = event.message_obj.message_id if event else None
                return {"message_id": mid, "role": "assistant", "type": "text",
                        "content": mc.get_plain_text(), "timestamp": 0}

    adapter = FakeAdapter()
    adapter._serializer = FakeAdapter._S()
    q = asyncio.Queue(maxsize=100)
    adapter._sse_clients["tok"] = [q]

    msg_obj = SimpleNamespace(message_id="m1", type=MessageType.FRIEND_MESSAGE,
                              sender=SimpleNamespace(user_id="tok"))
    event = BotApiMessageEvent(message_str="问", message_obj=msg_obj,
                               platform_meta=PlatformMetadata(name="botapi", description="BotAPI", id="botapi"),
                               session_id="tok", adapter=adapter)

    async def gen():
        r = MessageChain([Plain("思考中")])
        r.type = "reasoning"
        yield r
        yield MessageChain([Plain("答案")])

    await event.send_streaming(gen())

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    types = [e.event_type for e in events]
    assert "thinking" in types
    finals = [e for e in events if e.event_type == "message" and e.data.get("final")]
    assert len(finals) == 1
    assert finals[0].data["content"] == "答案"
    assert ("thinking", "思考中") in persisted
    assert ("final", "答案") in persisted
