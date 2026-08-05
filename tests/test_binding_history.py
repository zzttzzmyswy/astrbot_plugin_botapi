# tests/test_binding_history.py — Task 4: 历史/清空/统计读绑定平台 conversation
import hashlib
import json
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar
from astrbot_plugin_botapi.runtime import runtime as _get_runtime
from astrbot_plugin_botapi import sessions as S


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


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """重置插件配置单例 → tmp_path，预写空配置（隔离真实磁盘 data/config）。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    import json, os
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [], "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def _fake_context():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()

        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, handler, methods, desc))

    return FakeContext(), registered


def _adapter(monkeypatch):
    """真实 BotApiAdapter（免 __init__）实例，供函数契约层测试。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": ["tok"], "sessions": {}}
    a.cfg = SimpleNamespace(tokens=["tok"], sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = {"aiocqhttp_main"}
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_sessions_map(a.config.get("sessions") or {})
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "botapi", "type": "botapi", "enable": True},
        {"id": "aiocqhttp_main", "tokens": ["tok"], "enable": True},
    ]})
    return a


def _make_star(monkeypatch, tokens=None, bindings=None, sessions=None):
    """仿 test_binding_handlers.py::_make_star：带 binding_platform_for 的假 adapter。"""
    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    binds = dict(bindings or {})
    all_s = dict(sessions or {})
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_sessions_map(all_s)
    adapter = SimpleNamespace(
        cfg=SimpleNamespace(tokens=list(tokens or []), nicknames={}, sessions=dict(all_s)),
        config={"id": "botapi", "tokens": list(tokens or []), "nicknames": {},
                "sessions": dict(all_s)},
        platform_id="botapi",
        _sse_clients={},
        _disabled_tokens=set(),
        _last_active={},
        _active_platforms={"aiocqhttp_main"},
        _put=lambda q, evt: None,
    )

    # 复刻真实 adapter 的 binding_platform_for 语义（绑定 + 平台活跃才返回）
    def binding_platform_for(t):
        pid = binds.get(t)
        if not pid:
            return None
        return pid if pid in adapter._active_platforms else None
    adapter.binding_platform_for = binding_platform_for

    from astrbot_plugin_botapi import runtime as rt_mod

    rt = rt_mod.runtime()
    rt.adapter = adapter
    fake_cfg = {
        "platform": [
            {
                "id": "botapi",
                "tokens": list(tokens or []),
                "nicknames": {},
                "sessions": dict(all_s),
            }
        ]
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


# ── 函数契约层：get_conversation_messages 拼绑定平台 umo ──


@pytest.mark.asyncio
async def test_history_uses_bound_platform_umo(monkeypatch):
    """绑定后 /history 读绑定平台 conversation（函数契约层）。"""
    from astrbot_plugin_botapi import history as H
    from astrbot_plugin_botapi.runtime import runtime
    from astrbot_plugin_botapi import plugin_conf as pc
    rt = runtime()
    seen = {}

    class FakeCM:
        async def get_curr_conversation_id(self, umo):
            seen["umo"] = umo
            return None
    rt.conversation_manager = FakeCM()
    a = _adapter(monkeypatch)
    pc.set_bindings([{"token": "tok", "platform_id": "aiocqhttp_main"}])
    await H.get_conversation_messages(rt, a.binding_platform_for("tok") or a.platform_id,
                                      "botapi_tok", 50)
    assert seen["umo"] == "aiocqhttp_main:FriendMessage:botapi_tok"


# ── _do_history：绑定 token 读绑定平台 conversation ──


@pytest.mark.asyncio
async def test_do_history_bound_uses_bound_platform_umo(monkeypatch):
    """绑定 token 的 /history：get_conversation_messages 收到绑定平台 + botapi_ 前缀。"""
    star, adapter, _, _ = _make_star(monkeypatch, tokens=["tok"], bindings={"tok": "aiocqhttp_main"})
    seen = {}

    async def fake_get(rt, pid, tok, limit):
        seen["pid"] = pid
        seen["tok"] = tok
        return []

    monkeypatch.setattr("astrbot_plugin_botapi.history.get_conversation_messages", fake_get)
    res = await star._do_history(_hash("tok"), since=None, limit=50, session_id="")
    assert res["status"] == "ok"
    assert seen == {"pid": "aiocqhttp_main", "tok": "botapi_tok"}


@pytest.mark.asyncio
async def test_do_history_unbound_keeps_botapi_umo(monkeypatch):
    """未绑定 token 的 /history：仍读 botapi 平台 conversation。"""
    star, adapter, _, _ = _make_star(monkeypatch, tokens=["tok"])
    seen = {}

    async def fake_get(rt, pid, tok, limit):
        seen["pid"] = pid
        seen["tok"] = tok
        return []

    monkeypatch.setattr("astrbot_plugin_botapi.history.get_conversation_messages", fake_get)
    res = await star._do_history(_hash("tok"), since=None, limit=50, session_id="")
    assert res["status"] == "ok"
    assert seen == {"pid": "botapi", "tok": "tok"}


@pytest.mark.asyncio
async def test_do_history_bound_scoped_sid(monkeypatch):
    """绑定 + 分会话：botapi_ 前缀包住 scoped key。"""
    star, adapter, _, _ = _make_star(
        monkeypatch, tokens=["tok"], bindings={"tok": "aiocqhttp_main"},
        sessions={"tok": [{"id": "abc", "name": "x", "created_at": 1}]},
    )
    seen = {}

    async def fake_get(rt, pid, tok, limit):
        seen["pid"] = pid
        seen["tok"] = tok
        return []

    monkeypatch.setattr("astrbot_plugin_botapi.history.get_conversation_messages", fake_get)
    await star._do_history(_hash("tok"), since=None, limit=50, session_id="abc")
    assert seen == {"pid": "aiocqhttp_main", "tok": "botapi_tok:abc"}


# ── _do_clear：绑定 token 清绑定平台 conversation ──


@pytest.mark.asyncio
async def test_do_clear_bound_uses_bound_platform_umo(monkeypatch):
    """绑定 token 的 /clear：new_conversation 用绑定平台 umo。"""
    star, adapter, _, _ = _make_star(monkeypatch, tokens=["tok"], bindings={"tok": "aiocqhttp_main"})

    class FakeCM:
        async def new_conversation(self, umo):
            self.umo = umo

    cm = FakeCM()
    from astrbot_plugin_botapi import runtime as rt_mod
    rt_mod.runtime().conversation_manager = cm
    res = await star._do_clear(_hash("tok"), session_id="")
    assert res["status"] == "ok"
    assert cm.umo == "aiocqhttp_main:FriendMessage:botapi_tok"


@pytest.mark.asyncio
async def test_do_clear_unbound_keeps_botapi_umo(monkeypatch):
    """未绑定 token 的 /clear：仍清 botapi 平台 conversation。"""
    star, adapter, _, _ = _make_star(monkeypatch, tokens=["tok"])

    class FakeCM:
        async def new_conversation(self, umo):
            self.umo = umo

    cm = FakeCM()
    from astrbot_plugin_botapi import runtime as rt_mod
    rt_mod.runtime().conversation_manager = cm
    res = await star._do_clear(_hash("tok"), session_id="")
    assert res["status"] == "ok"
    assert cm.umo == "botapi:FriendMessage:tok"


# ── _do_stats：绑定 token 计数读绑定平台 conversation ──


@pytest.mark.asyncio
async def test_do_stats_bound_counts_from_bound_platform(monkeypatch):
    """绑定 token 的 /stats：message_count 读绑定平台 conversation。"""
    star, adapter, _, _ = _make_star(monkeypatch, tokens=["tok"], bindings={"tok": "aiocqhttp_main"})
    seen = []

    class FakeCM:
        async def get_curr_conversation_id(self, umo):
            seen.append(umo)
            return "cid1"

        async def get_conversation(self, umo, cid):
            return SimpleNamespace(history=json.dumps([
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "hi"},
            ]))

    from astrbot_plugin_botapi import runtime as rt_mod
    rt_mod.runtime().conversation_manager = FakeCM()
    res = await star._do_stats()
    assert res["status"] == "ok"
    assert seen == ["aiocqhttp_main:FriendMessage:botapi_tok"]
    assert res["data"]["per_account"][0]["message_count"] == 2
    assert res["data"]["total_messages"] == 2


@pytest.mark.asyncio
async def test_do_stats_unbound_counts_from_botapi(monkeypatch):
    """未绑定 token 的 /stats：message_count 仍读 botapi 平台 conversation。"""
    star, adapter, _, _ = _make_star(monkeypatch, tokens=["tok"])
    seen = []

    class FakeCM:
        async def get_curr_conversation_id(self, umo):
            seen.append(umo)
            return "cid1"

        async def get_conversation(self, umo, cid):
            return SimpleNamespace(history=json.dumps([
                {"role": "user", "content": "你好"},
            ]))

    from astrbot_plugin_botapi import runtime as rt_mod
    rt_mod.runtime().conversation_manager = FakeCM()
    res = await star._do_stats()
    assert res["status"] == "ok"
    assert seen == ["botapi:FriendMessage:tok"]
    assert res["data"]["per_account"][0]["message_count"] == 1