# tests/test_admin_handlers.py
import hashlib
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar
from astrbot_plugin_botapi.runtime import runtime as _get_runtime


@pytest.fixture(autouse=True)
def _cleanup_runtime():
    """Reset global runtime state before each test to avoid cross-test leaks."""
    rt = _get_runtime()
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None
    yield
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None


def _fake_context():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()

        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, handler, methods, desc))

    return FakeContext(), registered


def _make_star(monkeypatch, tokens=None, platforms=None, nicknames=None):
    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    nicks = dict(nicknames or {})
    adapter = SimpleNamespace(
        cfg=SimpleNamespace(tokens=list(tokens or []), nicknames=dict(nicks)),
        config={"id": "botapi", "tokens": list(tokens or []), "nicknames": dict(nicks)},
        platform_id="botapi",
        _sse_clients={},
        _disabled_tokens=set(),
        _last_active={},
        _put=lambda q, evt: None,
    )
    from astrbot_plugin_botapi import runtime as rt_mod

    rt = rt_mod.runtime()
    rt.adapter = adapter
    fake_cfg = {
        "platform": list(
            platforms
            or [
                {
                    "id": "botapi",
                    "tokens": list(tokens or []),
                    "nicknames": dict(nicks),
                }
            ]
        )
    }

    class FakeAstrbotConfig:
        def __getitem__(self, k):
            return fake_cfg[k]

        def get(self, k, d=None):
            return fake_cfg.get(k, d)

        def save_config(self):
            fake_cfg["_saved"] = True

    import astrbot_plugin_botapi.main as main_mod

    monkeypatch.setattr(main_mod, "_cfg_singleton", FakeAstrbotConfig())
    return star, adapter, fake_cfg, registered


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


@pytest.mark.asyncio
async def test_create_account_persists(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=[])
    token = await star._do_create("newtok")
    assert token["status"] == "ok"
    assert token["data"]["token"] == "newtok"
    assert "newtok" in adapter.config["tokens"]
    assert "newtok" in adapter.cfg.tokens
    assert fake_cfg.get("_saved") is True  # save_config 被调
    assert "newtok" in fake_cfg["platform"][0]["tokens"]


@pytest.mark.asyncio
async def test_delete_account(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a", "b"])
    result = await star._do_delete(_hash("a"))
    assert result["status"] == "ok"
    assert "a" not in adapter.config["tokens"]
    assert "a" not in adapter.cfg.tokens
    assert fake_cfg.get("_saved") is True


@pytest.mark.asyncio
async def test_toggle_disable(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])
    result = await star._do_toggle(_hash("a"), disabled=True)
    assert result["status"] == "ok"
    assert "a" in adapter._disabled_tokens


@pytest.mark.asyncio
async def test_stats_envelope(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a", "b"])
    result = await star._do_stats()
    assert result["status"] == "ok"
    assert result["data"]["total_accounts"] == 2


@pytest.mark.asyncio
async def test_create_with_nickname(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=[])
    res = await star._do_create("tok", "张三的Bot")
    assert res["status"] == "ok"
    assert adapter.cfg.nicknames.get("tok") == "张三的Bot"
    assert adapter.config["nicknames"]["tok"] == "张三的Bot"
    assert fake_cfg["platform"][0]["nicknames"]["tok"] == "张三的Bot"
    assert fake_cfg.get("_saved") is True


@pytest.mark.asyncio
async def test_set_nickname(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])
    await star._do_set_nickname(_hash("a"), "新昵称")
    assert adapter.cfg.nicknames.get("a") == "新昵称"
    assert fake_cfg["platform"][0]["nicknames"]["a"] == "新昵称"
    # 空昵称=清除
    await star._do_set_nickname(_hash("a"), "")
    assert "a" not in adapter.cfg.nicknames


@pytest.mark.asyncio
async def test_delete_removes_nickname(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(
        monkeypatch, tokens=["a"], nicknames={"a": "要被删的"}
    )
    await star._do_delete(_hash("a"))
    assert "a" not in adapter.cfg.nicknames
    assert "a" not in adapter.config["nicknames"]


@pytest.mark.asyncio
async def test_stats_includes_nickname(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(
        monkeypatch, tokens=["a"], nicknames={"a": "Alice"}
    )
    result = await star._do_stats()
    per = result["data"]["per_account"]
    assert per[0]["nickname"] == "Alice"
