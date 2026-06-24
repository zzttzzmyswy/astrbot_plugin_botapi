# tests/test_adapter_lifecycle.py
import asyncio
from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.platform.platform import PlatformStatus
from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi.models import SSEEvent


def _make_adapter():
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.config = {"id": "botapi"}
    adapter._sse_clients = {"tok": [asyncio.Queue(maxsize=10)]}
    adapter._shutdown = asyncio.Event()
    adapter._media_enabled = True
    adapter._serializer = SimpleNamespace()
    adapter.platform_id = "botapi"
    adapter.client_self_id = "selfid"
    adapter._status = PlatformStatus.RUNNING
    return adapter


@pytest.mark.asyncio
async def test_terminate_sends_none_sentinel():
    adapter = _make_adapter()
    await adapter.terminate()
    q = adapter._sse_clients["tok"][0]
    item = await q.get()
    assert item is None   # 哨兵


@pytest.mark.asyncio
async def test_send_by_session_pushes_to_queue(monkeypatch):
    adapter = _make_adapter()
    pushed = []

    async def bcast(token, evt):
        adapter._put(adapter._sse_clients[token][0], evt)

    async def push_media(chain, token, mid):
        pushed.append(mid)

    adapter._broadcast_to = bcast
    adapter._push_media = push_media

    class FakeSer:
        async def serialize_chain(self, mc, event):
            return {"message_id": None, "role": "assistant", "type": "text",
                    "content": mc.get_plain_text(), "timestamp": 0}
    adapter._serializer = FakeSer()

    session = SimpleNamespace(session_id="tok")
    mc = MessageChain([Plain("主动通知")])
    await adapter.send_by_session(session, mc)
    q = adapter._sse_clients["tok"][0]
    evt = await q.get()
    assert evt.data["content"] == "主动通知"
    assert evt.data["final"] is True
    assert pushed  # _push_media 被调
