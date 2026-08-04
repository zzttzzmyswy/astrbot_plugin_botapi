# tests/test_binding_storage.py
from types import SimpleNamespace
import pytest


def _adapter(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": ["tok1"], "nicknames": {}, "sessions": {}}
    a.cfg = SimpleNamespace(tokens=["tok1"], nicknames={}, sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    # 模拟活跃平台列表（PlatformManager._inst_map）
    a._active_platforms = {"aiocqhttp_main", "telegram_x"}
    return a


def test_binding_platform_for_returns_platform(monkeypatch):
    a = _adapter(monkeypatch)
    # 从插件配置读绑定表（此处模拟）
    a.config["botapi_bindings"] = {"tok1": "aiocqhttp_main"}
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_unbound_returns_none(monkeypatch):
    a = _adapter(monkeypatch)
    a.config["botapi_bindings"] = {}
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_inactive_returns_none(monkeypatch):
    """绑定的 platform 不在活跃列表 → 回退。"""
    a = _adapter(monkeypatch)
    a.config["botapi_bindings"] = {"tok1": "dead_platform"}
    assert a.binding_platform_for("tok1") is None


def test_bind_token_persists(monkeypatch):
    a = _adapter(monkeypatch)
    a.config["botapi_bindings"] = {}
    a.bind_token("tok1", "telegram_x")
    assert a.config["botapi_bindings"]["tok1"] == "telegram_x"
