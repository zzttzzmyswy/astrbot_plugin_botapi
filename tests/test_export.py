# tests/test_export.py
import hashlib
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi import history
from astrbot_plugin_botapi.main import BotApiStar
from astrbot_plugin_botapi.runtime import runtime as _get_runtime


def _row(id_, role, kind, text, ts=1719234567):
    """构造一行 platform_message_history 记录（形如 manager.get 返回项）。"""
    return SimpleNamespace(
        id=id_,
        content={"role": role, "kind": kind, "text": text, "message_id": f"botapi_{id_}"},
        created_at=datetime.fromtimestamp(ts + id_),
    )


# ── to_markdown ──


def test_markdown_renders_user_and_assistant():
    rows = [
        {"message_id": "1", "role": "user", "type": "text", "content": "你好", "timestamp": 1719234567},
        {"message_id": "2", "role": "assistant", "type": "text", "content": "你好！有什么可以帮你？", "timestamp": 1719234568},
    ]
    md = history.to_markdown(rows, {"nickname": "Alice", "token_preview": "abc...1234",
                                    "exported_at": "2026-06-25 12:00:00"})
    assert "# BotAPI 对话记录 — Alice" in md
    assert "## 👤 用户" in md
    assert "## 🤖 助手" in md
    assert "你好" in md
    assert "你好！有什么可以帮你？" in md
    assert "> 消息数：2" in md
    assert "> 导出时间：2026-06-25 12:00:00" in md


def test_markdown_renders_thinking_in_details():
    rows = [{"message_id": "3", "role": "assistant", "type": "thinking",
             "content": "用户问的是...", "timestamp": 1719234570}]
    md = history.to_markdown(rows, {"nickname": "", "token_preview": "t", "exported_at": "x"})
    assert "<details" in md and "</details>" in md
    assert "用户问的是..." in md
    assert "## 🤖 助手" not in md   # thinking 不走助手标题


def test_markdown_renders_tool_status_as_blockquote():
    rows = [{"message_id": "4", "role": "assistant", "type": "tool_status",
             "content": "调用 web_search", "timestamp": 1719234571}]
    md = history.to_markdown(rows, {"nickname": "", "token_preview": "t", "exported_at": "x"})
    assert "> 🔨 工具状态" in md
    assert "调用 web_search" in md


def test_markdown_empty_rows():
    md = history.to_markdown([], {"nickname": "A", "token_preview": "p", "exported_at": "x"})
    assert "> 消息数：0" in md
    assert "## 👤" not in md


# ── get_export_rows：分页 + 排序 ──


class FakePMH:
    """模拟 PlatformMessageHistoryManager.get：按 page/page_size 切片返回，
    返回顺序为 desc(created_at)（即 id 大的在前，与真实 db 一致）。"""
    def __init__(self, all_rows):
        self._all = sorted(all_rows, key=lambda r: r.id, reverse=True)  # desc
        self.calls = []

    async def get(self, platform_id, user_id, page=1, page_size=200):
        self.calls.append((page, page_size))
        start = (page - 1) * page_size
        return self._all[start:start + page_size]   # 注意：真实 manager.get 会 reverse，见下方测试


class FakePMHReversing:
    """更贴近真实：manager.get 内部对每页做 reverse（升序）。
    构造返回前先 reverse，模拟 PlatformMessageHistoryManager.get 的行为。"""
    def __init__(self, all_rows):
        self._all = sorted(all_rows, key=lambda r: r.id, reverse=True)  # desc
        self.calls = []

    async def get(self, platform_id, user_id, page=1, page_size=200):
        self.calls.append((page, page_size))
        start = (page - 1) * page_size
        chunk = self._all[start:start + page_size]   # desc
        chunk = list(reversed(chunk))                # 升序（模拟真实 get 的 .reverse()）
        return chunk


@pytest.mark.asyncio
async def test_get_export_rows_paginates_and_sorts(monkeypatch):
    rows = [_row(i, "user" if i % 2 else "assistant", "final", f"msg{i}") for i in range(1, 13)]
    fake_pmh = FakePMHReversing(rows)
    fake_rt = SimpleNamespace(message_history_manager=fake_pmh,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    out = await history.get_export_rows("botapi", "tok", page_size=5)
    # 12 行 / page_size=5 → 3 页（5,5,2），page_size=5 < 时停
    assert len(fake_pmh.calls) == 3
    assert [r["message_id"] for r in out] == [str(i) for i in range(1, 13)]  # 按 id 升序


@pytest.mark.asyncio
async def test_get_export_rows_empty(monkeypatch):
    fake_pmh = FakePMHReversing([])
    fake_rt = SimpleNamespace(message_history_manager=fake_pmh,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)
    out = await history.get_export_rows("botapi", "tok")
    assert out == []


@pytest.mark.asyncio
async def test_get_export_rows_no_manager(monkeypatch):
    fake_rt = SimpleNamespace(message_history_manager=None,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)
    out = await history.get_export_rows("botapi", "tok")
    assert out == []


# ── _do_export ──


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def _make_star(monkeypatch, tokens=None, nicknames=None, rows=None):
    ctx = SimpleNamespace(
        conversation_manager=SimpleNamespace(),
        message_history_manager=SimpleNamespace(),
        register_web_api=lambda r, h, m, d: None,
    )
    star = BotApiStar(ctx, None)
    nicks = dict(nicknames or {})
    adapter = SimpleNamespace(
        cfg=SimpleNamespace(tokens=list(tokens or []), nicknames=dict(nicks)),
        config={"id": "botapi", "tokens": list(tokens or []), "nicknames": dict(nicks)},
        platform_id="botapi",
    )
    rt = _get_runtime()
    rt.adapter = adapter

    fake_pmh = FakePMHReversing(rows or [])
    rt.message_history_manager = fake_pmh
    return star, adapter, fake_pmh


@pytest.mark.asyncio
async def test_export_markdown(monkeypatch):
    rows = [_row(1, "user", "user", "你好"), _row(2, "assistant", "final", "你好！")]
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"], nicknames={"tok": "Alice"}, rows=rows)
    res = await star._do_export(_hash("tok"), "md")
    assert res["status"] == "ok"
    assert res["data"]["filename"].endswith(".md")
    assert "Alice" in res["data"]["filename"]
    assert res["data"]["mime"] == "text/markdown"
    assert "## 👤 用户" in res["data"]["content"]
    assert "你好" in res["data"]["content"]


@pytest.mark.asyncio
async def test_export_json(monkeypatch):
    rows = [_row(1, "user", "user", "你好"), _row(2, "assistant", "final", "你好！")]
    star, adapter, _ = _make_star(monkeypatch, tokens=["tok"], rows=rows)
    res = await star._do_export(_hash("tok"), "json")
    assert res["status"] == "ok"
    assert res["data"]["filename"].endswith(".json")
    assert res["data"]["mime"] == "application/json"
    parsed = json.loads(res["data"]["content"])
    assert len(parsed) == 2
    assert parsed[0]["content"] == "你好"
    assert parsed[0]["role"] == "user"


@pytest.mark.asyncio
async def test_export_unknown_account(monkeypatch):
    star, _, _ = _make_star(monkeypatch, tokens=["tok"])
    res = await star._do_export(_hash("other"), "md")
    assert res["status"] == "error"
    assert "未找到账户" in res["message"]


@pytest.mark.asyncio
async def test_export_unknown_format(monkeypatch):
    star, _, _ = _make_star(monkeypatch, tokens=["tok"], rows=[_row(1, "user", "user", "hi")])
    res = await star._do_export(_hash("tok"), "csv")
    assert res["status"] == "error"
    assert "格式" in res["message"]


@pytest.mark.asyncio
async def test_export_adapter_not_ready(monkeypatch):
    rt = _get_runtime()
    rt.adapter = None
    rt.message_history_manager = None
    star = BotApiStar(SimpleNamespace(conversation_manager=SimpleNamespace(),
                                      message_history_manager=SimpleNamespace(),
                                      register_web_api=lambda *a, **k: None), None)
    res = await star._do_export(_hash("tok"), "md")
    assert res["status"] == "error"
    assert "适配器" in res["message"]
