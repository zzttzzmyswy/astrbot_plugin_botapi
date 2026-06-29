import asyncio
from collections import defaultdict
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi import routes as routes_mod
from astrbot_plugin_botapi.models import SSEEvent


def _make_adapter(monkeypatch):
    """Construct a fully wired BotApiAdapter with Quart app and /stream registered."""
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.cfg = SimpleNamespace(host="127.0.0.1", port=9000, tokens=["tok"])
    adapter.config = {"id": "botapi", "tokens": ["tok"]}
    adapter.platform_id = "botapi"
    adapter.client_self_id = "selfid"
    adapter._disabled_tokens = set()
    adapter._last_active = {}
    adapter._uploaded_files = {}
    adapter._sse_clients = defaultdict(list)
    adapter._media_enabled = True
    adapter._serializer = SimpleNamespace()
    adapter._token_to_origin = {}
    adapter.commit_event = lambda e: None
    from quart import Quart
    adapter.app = Quart("t")
    routes_mod._setup_routes(adapter)
    return adapter


def _make_adapter_without_app():
    """Minimal adapter without Quart app, for unit-testing _stream_gen."""
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.platform_id = "botapi"
    adapter._sse_clients = {}
    adapter._last_active = {}
    return adapter


# ── Integration test ──

@pytest.mark.asyncio
async def test_stream_registers_queue_and_cleans_up(monkeypatch):
    """Integration: connecting to /stream registers a queue; None sentinel triggers cleanup."""
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()

    gen_task = asyncio.create_task(_drain(client))
    await asyncio.sleep(0.5)   # allow /stream to establish and register queue
    assert len(adapter._sse_clients.get("tok", [])) == 1
    q = adapter._sse_clients["tok"][0]
    # Send None to trigger clean shutdown (generator breaks, finally removes queue)
    await q.put(None)
    await asyncio.wait_for(gen_task, timeout=5)
    await asyncio.sleep(0.2)
    assert adapter._sse_clients.get("tok", []) == []


async def _drain(client):
    async with client.request("/api/v1/botapi/stream", method="GET",
                              headers={"Authorization": "Bearer tok"}):
        pass  # held until SSE stream ends (None sentinel breaks _stream_gen)


# ── Unit tests for _stream_gen (refactored generator, option b) ──

@pytest.mark.asyncio
async def test_stream_gen_yields_event_from_queue():
    """Put an SSEEvent into the queue; _stream_gen must yield its SSE text."""
    adapter = _make_adapter_without_app()
    q = asyncio.Queue(maxsize=256)
    adapter._sse_clients["tok"] = [q]

    async def consumer():
        gen = routes_mod._stream_gen(adapter, "tok", q, since=None)
        result = await gen.__anext__()
        await gen.aclose()
        return result

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)
    evt = SSEEvent("message", {"content": "hello"})
    await q.put(evt)
    result = await asyncio.wait_for(task, timeout=2)
    assert "event: message" in result
    assert "hello" in result


@pytest.mark.asyncio
async def test_stream_gen_ping_on_timeout(monkeypatch):
    """When q.get() times out inside the loop, yield a ping SSE."""
    adapter = _make_adapter_without_app()
    q = asyncio.Queue(maxsize=256)
    adapter._sse_clients["tok"] = [q]

    async def fake_wait_for(coro, timeout):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    gen = routes_mod._stream_gen(adapter, "tok", q, since=None)
    result = await gen.__anext__()
    await gen.aclose()
    assert "event: ping" in result


@pytest.mark.asyncio
async def test_stream_gen_breaks_on_none_sentinel():
    """Put None into the queue; generator must stop (no more items yielded)."""
    adapter = _make_adapter_without_app()
    q = asyncio.Queue(maxsize=256)
    adapter._sse_clients["tok"] = [q]

    await q.put(None)
    gen = routes_mod._stream_gen(adapter, "tok", q, since=None)
    items = [item async for item in gen]
    assert items == []


@pytest.mark.asyncio
async def test_stream_gen_cleans_up_queue_in_finally():
    """finally must remove the queue from _sse_clients[token] on generator exit."""
    adapter = _make_adapter_without_app()
    q = asyncio.Queue(maxsize=256)
    adapter._sse_clients["tok"] = [q]

    await q.put(None)
    gen = routes_mod._stream_gen(adapter, "tok", q, since=None)
    async for _ in gen:
        pass
    # After generator exits normally, finally must have removed the queue
    assert q not in adapter._sse_clients.get("tok", [])


@pytest.mark.asyncio
async def test_stream_gen_no_catchup_replay_when_since_given(monkeypatch):
    """since 给定时不再经 SSE 回放历史。

    旧实现把 catchup_events 的历史行经 SSE 重发给 client，client 用本地 now()
    存 created_at、丢弃事件自带的 timestamp，导致较早的历史被盖上最新时间、
    排到真正最新记录下方。历史补漏改由 client 调 /history 端点（row_to_sse 带
    真实 timestamp+role，mergeHistory 正确落库）。since 参数保留兼容 client
    的 /stream?since=<cursor> URL，但不再回放。
    """
    adapter = _make_adapter_without_app()
    q = asyncio.Queue(maxsize=256)
    adapter._sse_clients["tok"] = [q]

    called = {"n": 0}

    async def fake_catchup(platform_id, token, since):
        called["n"] += 1
        return [SSEEvent("message", {"content": "should_not_replay"})]

    monkeypatch.setattr(routes_mod, "catchup_events", fake_catchup)

    gen = routes_mod._stream_gen(adapter, "tok", q, since="100")
    # 放入实时事件：第一个 yield 必须来自 queue，而非 catchup 回放
    await q.put(SSEEvent("message", {"content": "live"}))
    item1 = await gen.__anext__()
    assert "live" in item1
    assert "should_not_replay" not in item1
    assert called["n"] == 0   # catchup_events 不应被调用

    await q.put(None)
    async for _ in gen:
        pass
    assert q not in adapter._sse_clients.get("tok", [])
