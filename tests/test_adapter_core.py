# tests/test_adapter_core.py
import asyncio
from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image
from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi.models import SSEEvent


def _make_adapter():
    # 绕过 PlatformManager 实例化与 ABC 抽象方法检查，直接构造测试用 adapter
    # Python 3.14: object.__new__ 自身也会检查 abstractmethods → 临时清空
    _abstract = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        adapter = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abstract
    adapter.run = lambda: None          # Task 13 才实现
    adapter._sse_clients = {}
    adapter._media_enabled = True
    adapter._serializer = type("S", (), {"_media_url": classmethod(
        lambda cls, comp: _async_return("https://dash/api/file/" + str(id(comp) % 1000)))})()
    return adapter


async def _async_return(v):
    return v


@pytest.mark.asyncio
async def test_put_nonblocking_when_full():
    """Python 3.14: asyncio.get_event_loop 可能无运行中的 loop → 改为 async."""
    adapter = _make_adapter()
    q = asyncio.Queue(maxsize=2)
    adapter._put(q, SSEEvent("message", {"i": 1}))
    adapter._put(q, SSEEvent("message", {"i": 2}))
    # 第 3 个：队列满，丢最旧后放入，不抛异常
    adapter._put(q, SSEEvent("message", {"i": 3}))
    i1 = await q.get()
    i2 = await q.get()
    assert i1.data["i"] == 2   # 最旧(1)被丢
    assert i2.data["i"] == 3


@pytest.mark.asyncio
async def test_broadcast_to_all_queues():
    adapter = _make_adapter()
    adapter._sse_clients["tok"] = [asyncio.Queue(maxsize=10), asyncio.Queue(maxsize=10)]
    await adapter._broadcast_to("tok", SSEEvent("message", {"x": 1}))
    for q in adapter._sse_clients["tok"]:
        assert (await q.get()).data["x"] == 1


@pytest.mark.asyncio
async def test_push_media_per_queue_independent_token(monkeypatch):
    """每队列铸独立 token：N 队列 → 每队列恰好 1 条媒体事件。"""
    adapter = _make_adapter()
    q1, q2 = asyncio.Queue(maxsize=10), asyncio.Queue(maxsize=10)
    adapter._sse_clients["tok"] = [q1, q2]
    mint_count = {"n": 0}

    async def fake_media_url(comp):
        mint_count["n"] += 1
        return f"https://dash/api/file/t{mint_count['n']}"

    adapter._serializer._media_url = fake_media_url

    chain = MessageChain([Image.fromFileSystem("/x.png")])
    await adapter._push_media(chain, "tok", "mid1")

    e1 = await q1.get()
    e2 = await q2.get()
    assert e1.data["type"] == "image"
    assert e2.data["type"] == "image"
    # 两队列拿到不同 token URL
    assert e1.data["content"] != e2.data["content"]
    assert mint_count["n"] == 2   # 每队列铸一次


@pytest.mark.asyncio
async def test_push_media_skips_when_no_url(monkeypatch):
    adapter = _make_adapter()
    q = asyncio.Queue(maxsize=10)
    adapter._sse_clients["tok"] = [q]

    async def no_url(comp):
        return None

    adapter._serializer._media_url = no_url
    chain = MessageChain([Image.fromFileSystem("/x.png")])
    await adapter._push_media(chain, "tok", "mid1")
    assert q.empty()   # 无 URL 则不投递


@pytest.mark.asyncio
async def test_push_media_ignores_plain():
    adapter = _make_adapter()
    q = asyncio.Queue(maxsize=10)
    adapter._sse_clients["tok"] = [q]
    chain = MessageChain([Plain("text")])
    await adapter._push_media(chain, "tok", "mid1")
    assert q.empty()
