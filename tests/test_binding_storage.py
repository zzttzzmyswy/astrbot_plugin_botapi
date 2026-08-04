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


def test_binding_platform_for_config_fallback_enabled(monkeypatch):
    """_active_platforms 为空（重启后平台注入前）→ 回退 astrbot_config 里
    enable=True 的非 botapi 平台条目：绑定平台在配置且启用 → 返回 pid。"""
    import astrbot_plugin_botapi.adapter as adapter_mod
    a = _adapter(monkeypatch)
    a._active_platforms = set()
    a.config["botapi_bindings"] = {"tok1": "aiocqhttp_main"}
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "botapi", "type": "botapi", "enable": True},
        {"id": "aiocqhttp_main", "enable": True},
        {"id": "telegram_disabled", "enable": False},
    ]})
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_config_fallback_disabled(monkeypatch):
    """_active_platforms 为空 + 绑定平台在配置里 enable=False → 返回 None。"""
    import astrbot_plugin_botapi.adapter as adapter_mod
    a = _adapter(monkeypatch)
    a._active_platforms = set()
    a.config["botapi_bindings"] = {"tok1": "telegram_disabled"}
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "botapi", "type": "botapi", "enable": True},
        {"id": "aiocqhttp_main", "enable": True},
        {"id": "telegram_disabled", "enable": False},
    ]})
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_config_fallback_ignores_botapi(monkeypatch):
    """_active_platforms 为空 → 绑定到 type=botapi 平台条目（回环）不生效。"""
    import astrbot_plugin_botapi.adapter as adapter_mod
    a = _adapter(monkeypatch)
    a._active_platforms = set()
    a.config["botapi_bindings"] = {"tok1": "other_botapi"}
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "other_botapi", "type": "botapi", "enable": True},
    ]})
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_active_set_beats_config(monkeypatch):
    """_active_platforms 非空即以其为准：绑定的 pid 不在活跃集（即使配置里 enable）
    → 返回 None（平台实际未启动，不能靠配置回退）。"""
    import astrbot_plugin_botapi.adapter as adapter_mod
    a = _adapter(monkeypatch)
    a._active_platforms = {"telegram_x"}   # 非空 → 走活跃集判定，不走配置回退
    a.config["botapi_bindings"] = {"tok1": "aiocqhttp_main"}
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "aiocqhttp_main", "enable": True},
    ]})
    assert a.binding_platform_for("tok1") is None


def test_bind_token_persists(monkeypatch):
    a = _adapter(monkeypatch)
    a.config["botapi_bindings"] = {}
    a.bind_token("tok1", "telegram_x")
    assert a.config["botapi_bindings"]["tok1"] == "telegram_x"
