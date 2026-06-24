import asyncio
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from astrbot_plugin_botapi import history
from astrbot_plugin_botapi.models import SSEEvent


def _row(rid, kind, text, role="assistant"):
    return SimpleNamespace(
        id=rid,
        content={"role": role, "kind": kind, "text": text, "message_id": f"m{rid}"},
        created_at=datetime(2026, 6, 24, 12, 0, rid, tzinfo=timezone.utc),
    )


class FakePMH:
    def __init__(self, rows):
        self._rows = rows  # 已是升序

    async def get(self, platform_id, user_id, page=1, page_size=200):
        return list(self._rows)


def test_row_to_sse_final():
    r = _row(3, "final", "hello")
    m = history.row_to_sse(r)
    assert m["message_id"] == "3"
    assert m["role"] == "assistant"
    assert m["type"] == "text"
    assert m["content"] == "hello"
    assert isinstance(m["timestamp"], int)


def test_row_to_sse_thinking():
    r = _row(5, "thinking", "reasoning...")
    assert history.row_to_sse(r)["type"] == "thinking"


def test_row_to_sse_tool_status():
    r = _row(7, "tool_status", "🔧 tool")
    assert history.row_to_sse(r)["type"] == "tool_status"


def test_row_to_sse_user():
    r = _row(1, "user", "hi", role="user")
    assert history.row_to_sse(r)["role"] == "user"


@pytest.mark.asyncio
async def test_get_history_since_int_filter(monkeypatch):
    rows = [_row(1, "user", "u1", "user"), _row(2, "final", "a1"),
            _row(3, "final", "a2"), _row(4, "final", "a3")]
    fake_rt = SimpleNamespace(message_history_manager=FakePMH(rows), adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    msgs, has_more = await history.get_history("botapi", "tok", since="2", limit=50)
    assert [m["message_id"] for m in msgs] == ["3", "4"]


@pytest.mark.asyncio
async def test_get_history_before_int_filter(monkeypatch):
    rows = [_row(1, "final", "a1"), _row(2, "final", "a2"), _row(3, "final", "a3")]
    fake_rt = SimpleNamespace(message_history_manager=FakePMH(rows), adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    msgs, _ = await history.get_history("botapi", "tok", before="3", limit=50)
    assert [m["message_id"] for m in msgs] == ["1", "2"]


@pytest.mark.asyncio
async def test_get_history_limit(monkeypatch):
    rows = [_row(i, "final", f"a{i}") for i in range(1, 6)]
    fake_rt = SimpleNamespace(message_history_manager=FakePMH(rows), adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    msgs, has_more = await history.get_history("botapi", "tok", limit=2)
    assert [m["message_id"] for m in msgs] == ["4", "5"]
    assert has_more is True


@pytest.mark.asyncio
async def test_catchup_events_int_filter(monkeypatch):
    rows = [_row(1, "user", "u1", "user"), _row(2, "final", "a1"),
            _row(3, "thinking", "th"), _row(4, "final", "a2")]
    fake_rt = SimpleNamespace(message_history_manager=FakePMH(rows), adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    evts = await history.catchup_events("botapi", "tok", since="2")
    assert len(evts) == 2
    assert isinstance(evts[0], SSEEvent)
    # thinking 记录 → thinking 事件；final → message 事件
    assert evts[0].event_type == "thinking"
    assert evts[1].event_type == "message"


@pytest.mark.asyncio
async def test_catchup_int_not_lexicographic(monkeypatch):
    # 防字典序 bug：id 9 vs 10（字典序 "10" < "9"，但 int 10 > 9）
    rows = [_row(9, "final", "a9"), _row(10, "final", "a10")]
    fake_rt = SimpleNamespace(message_history_manager=FakePMH(rows), adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    evts = await history.catchup_events("botapi", "tok", since="9")
    assert len(evts) == 1
    assert evts[0].data["message_id"] == "10"
