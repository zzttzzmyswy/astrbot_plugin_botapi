import asyncio
import json
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


def test_row_to_sse_naive_utc_timestamp():
    """SQLite 读回的 created_at 是 naive(丢了 +00:00)。row_to_sse 须按 UTC 解释,
    否则在非 UTC 服务器上 .timestamp() 会偏一个时区(北京服务器早 8h)。"""
    aware = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)
    naive = aware.replace(tzinfo=None)  # 模拟 SQLite 读回
    r_aware = SimpleNamespace(id=1, content={"kind": "final", "text": "x", "role": "assistant"}, created_at=aware)
    r_naive = SimpleNamespace(id=1, content={"kind": "final", "text": "x", "role": "assistant"}, created_at=naive)
    assert history.row_to_sse(r_aware)["timestamp"] == history.row_to_sse(r_naive)["timestamp"]
    assert history.row_to_sse(r_naive)["timestamp"] == int(aware.timestamp())


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


# ── get_conversation_messages（读 conversation_manager）──

class FakeCM:
    """模拟 conversation_manager：get_curr_conversation_id + get_conversation。"""
    def __init__(self, history_items):
        self._history = history_items

    async def get_curr_conversation_id(self, umo):
        return "cid_1" if umo == "botapi:FriendMessage:tok" else None

    async def get_conversation(self, umo, cid):
        return SimpleNamespace(history=json.dumps(self._history))


@pytest.mark.asyncio
async def test_get_conversation_messages_skips_empty_content(monkeypatch):
    hist = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "这是回复"},
        {"role": "assistant", "content": None},        # 工具调用帧 → 空 content
        {"role": "assistant", "content": ""},          # 空字符串
        {"role": "system", "content": "system prompt"},  # system 跳过
    ]
    fake_rt = SimpleNamespace(conversation_manager=FakeCM(hist))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    msgs = await history.get_conversation_messages(fake_rt, "botapi", "tok", limit=50)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "你好"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "这是回复"


@pytest.mark.asyncio
async def test_get_conversation_messages_empty_history(monkeypatch):
    fake_rt = SimpleNamespace(conversation_manager=FakeCM([]))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)
    msgs = await history.get_conversation_messages(fake_rt, "botapi", "tok", limit=50)
    assert msgs == []


@pytest.mark.asyncio
async def test_get_conversation_messages_no_conversation(monkeypatch):
    # umo 不匹配 → get_curr_conversation_id 返回 None → 空
    fake_rt = SimpleNamespace(conversation_manager=FakeCM([{"role": "user", "content": "x"}]))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)
    msgs = await history.get_conversation_messages(fake_rt, "botapi", "other", limit=50)
    assert msgs == []
