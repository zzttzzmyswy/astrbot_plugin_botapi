# tests/test_sessions_sse.py
import asyncio
from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image
from astrbot.api.platform import MessageType
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform import platform as _platmod
from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi.models import SSEEvent
from astrbot_plugin_botapi import sessions as S


def _adapter(monkeypatch):
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "sessions": {}}
    a.cfg = SimpleNamespace(sessions={})
    a._sse_clients = {}
    a._serializer = SimpleNamespace()
    a._media_enabled = True
    return a


def _patch_metrics(monkeypatch):
    """super().send_by_session 会 create_task(Metric.upload)，避免后台任务泄漏。"""
    async def _noop(**kwargs):
        return None
    monkeypatch.setattr(_platmod.Metric, "upload", staticmethod(_noop))


def _session(umo):
    """模拟 AstrBot MessageSession：from_str 把 umo 切成三段，.session_id 是第三段。"""
    platform_id, mtype, session_id = umo.split(":", 2)
    return MessageSession(platform_id, MessageType(mtype), session_id)


def _drain(q: asyncio.Queue) -> list:
    """非阻塞取空队列（RED 期旧实现会投错分区 → 空队列断言失败而非挂死）。"""
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return out


async def _fake_serialize_chain(mc, event):
    return {"role": "assistant", "type": "text", "content": mc.get_plain_text()}


@pytest.mark.asyncio
async def test_broadcast_to_scoped_partitions(monkeypatch):
    a = _adapter(monkeypatch)
    q_d = asyncio.Queue(maxsize=10)
    q_a = asyncio.Queue(maxsize=10)
    a._sse_clients["tok"] = [q_d]
    a._sse_clients["tok:abc"] = [q_a]
    await a._broadcast_to("tok", SSEEvent("message", {"session_id": ""}))
    await a._broadcast_to("tok:abc", SSEEvent("message", {"session_id": "abc"}))
    assert (await q_d.get()).data["session_id"] == ""
    assert (await q_a.get()).data["session_id"] == "abc"


@pytest.mark.asyncio
async def test_push_media_scoped(monkeypatch):
    a = _adapter(monkeypatch)
    q = asyncio.Queue(maxsize=10)
    a._sse_clients["tok:abc"] = [q]

    async def fake_media_url(comp):
        return "https://dash/api/file/x"

    a._serializer._media_url = fake_media_url
    chain = MessageChain([Image.fromFileSystem("/x.png")])
    await a._push_media(chain, "tok", "mid1", sid="abc")
    e = await q.get()
    assert e.data["type"] == "image"
    assert e.data["session_id"] == "abc"


@pytest.mark.asyncio
async def test_push_media_scoped_not_default_queue(monkeypatch):
    """scoped 媒体只进分区队列，不进默认 token 队列。"""
    a = _adapter(monkeypatch)
    q_d = asyncio.Queue(maxsize=10)
    q_a = asyncio.Queue(maxsize=10)
    a._sse_clients["tok"] = [q_d]
    a._sse_clients["tok:abc"] = [q_a]

    async def fake_media_url(comp):
        return "https://dash/api/file/x"

    a._serializer._media_url = fake_media_url
    chain = MessageChain([Image.fromFileSystem("/x.png")])
    await a._push_media(chain, "tok", "mid1", sid="abc")
    e = await q_a.get()
    assert e.data["type"] == "image"
    assert e.data["session_id"] == "abc"
    assert q_d.empty()   # 默认分区不应收到


@pytest.mark.asyncio
async def test_push_media_default_sid_backward_compat(monkeypatch):
    """缺省 sid='default' → 退化为按 token 投递（旧行为），session_id 为空。"""
    a = _adapter(monkeypatch)
    q = asyncio.Queue(maxsize=10)
    a._sse_clients["tok"] = [q]

    async def fake_media_url(comp):
        return "https://dash/api/file/x"

    a._serializer._media_url = fake_media_url
    chain = MessageChain([Image.fromFileSystem("/x.png")])
    await a._push_media(chain, "tok", "mid1")
    e = await q.get()
    assert e.data["type"] == "image"
    assert e.data["session_id"] == ""


@pytest.mark.asyncio
async def test_send_by_session_default_uses_token(monkeypatch):
    """3 段 umo（默认会话）→ 投到 token 分区，session_id 为空。"""
    a = _adapter(monkeypatch)
    _patch_metrics(monkeypatch)
    q_d = asyncio.Queue(maxsize=10)
    a._sse_clients["tok"] = [q_d]
    a._serializer.serialize_chain = _fake_serialize_chain

    chain = MessageChain([Plain("你好")])
    await a.send_by_session(_session("botapi:FriendMessage:tok"), chain)
    evs = _drain(q_d)
    assert len(evs) == 1
    e = evs[0]
    assert e.data["type"] == "text"
    assert e.data["final"] is True
    assert e.data["session_id"] == ""


@pytest.mark.asyncio
async def test_send_by_session_scoped_goes_to_partition(monkeypatch):
    """4 段 umo（非默认会话）→ 投到 token:sid 分区，session_id=sid。"""
    a = _adapter(monkeypatch)
    _patch_metrics(monkeypatch)
    q_d = asyncio.Queue(maxsize=10)
    q_a = asyncio.Queue(maxsize=10)
    a._sse_clients["tok"] = [q_d]
    a._sse_clients["tok:abc"] = [q_a]
    a._serializer.serialize_chain = _fake_serialize_chain

    chain = MessageChain([Plain("你好")])
    await a.send_by_session(_session("botapi:FriendMessage:tok:abc"), chain)
    evs = _drain(q_a)
    assert len(evs) == 1
    assert evs[0].data["final"] is True
    assert evs[0].data["session_id"] == "abc"
    assert q_d.empty()   # 默认分区不应收到
