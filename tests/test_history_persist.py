from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi import history


class FakePMH:
    def __init__(self):
        self.inserted = []

    async def insert(self, platform_id, user_id, content, sender_id=None,
                     sender_name=None, llm_checkpoint_id=None):
        self.inserted.append({"platform_id": platform_id, "user_id": user_id,
                              "content": content, "sender_id": sender_id})
        return SimpleNamespace(id=len(self.inserted), content=content)


@pytest.mark.asyncio
async def test_persist_inbound_text(monkeypatch):
    fake_pmh = FakePMH()
    fake_rt = SimpleNamespace(message_history_manager=fake_pmh,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    await history.persist_inbound_text("tok", "m1", "hello")
    assert len(fake_pmh.inserted) == 1
    c = fake_pmh.inserted[0]["content"]
    assert c["role"] == "user" and c["text"] == "hello"
    assert fake_pmh.inserted[0]["platform_id"] == "botapi"


@pytest.mark.asyncio
async def test_persist_assistant_final(monkeypatch):
    fake_pmh = FakePMH()
    fake_rt = SimpleNamespace(message_history_manager=fake_pmh,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    await history.persist_assistant_text("tok", "m2", "answer", kind="final")
    c = fake_pmh.inserted[0]["content"]
    assert c["kind"] == "final" and c["text"] == "answer"


@pytest.mark.asyncio
async def test_persist_skips_empty(monkeypatch):
    fake_pmh = FakePMH()
    fake_rt = SimpleNamespace(message_history_manager=fake_pmh,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    await history.persist_assistant_text("tok", "m3", "", kind="final")
    await history.persist_inbound_text("tok", "m3", "")
    assert fake_pmh.inserted == []


@pytest.mark.asyncio
async def test_persist_thinking(monkeypatch):
    fake_pmh = FakePMH()
    fake_rt = SimpleNamespace(message_history_manager=fake_pmh,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    await history.persist_assistant_thinking("tok", "m4", "reasoning")
    assert fake_pmh.inserted[0]["content"]["kind"] == "thinking"


@pytest.mark.asyncio
async def test_persist_noop_when_no_manager(monkeypatch):
    fake_rt = SimpleNamespace(message_history_manager=None, adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)
    await history.persist_assistant_text("tok", "m5", "x", kind="final")   # 不崩
