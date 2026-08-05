# tests/test_binding_storage.py — 绑定=token 出现在非 botapi 平台条目 tokens 列表（一对一）
import json
import os
from types import SimpleNamespace
import pytest


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """重置插件配置单例 → tmp_path，预写初始配置（账户注册表）。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": ["tok1"],
                   "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _adapter(monkeypatch, platforms, active=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "type": "botapi"}
    a.cfg = SimpleNamespace(tokens=list(pc.get_tokens()))
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = set(active if active is not None else {"aiocqhttp_main"})
    import astrbot_plugin_botapi.adapter as adapter_mod

    class _FakeCfg(dict):
        def __init__(self, plats):
            super().__init__({"platform": plats})
            self.saved = False

        def save_config(self):
            self.saved = True

    fake = _FakeCfg(platforms)
    monkeypatch.setattr(adapter_mod, "astrbot_config", fake)
    return a, fake


def test_binding_platform_for_returns_platform(monkeypatch):
    a, _ = _adapter(monkeypatch, [
        {"id": "aiocqhttp_main", "tokens": ["tok1"], "enable": True},
    ])
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_unbound_returns_none(monkeypatch):
    a, _ = _adapter(monkeypatch, [
        {"id": "aiocqhttp_main", "tokens": ["tok2"], "enable": True},
    ])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_inactive_returns_none(monkeypatch):
    a, _ = _adapter(monkeypatch, [
        {"id": "aiocqhttp_main", "tokens": ["tok1"], "enable": True},
    ], active={"telegram_x"})
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_skips_botapi_type(monkeypatch):
    """botapi 类型平台条目 tokens 含 token 不算绑定（排除自身回环）。"""
    a, _ = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["tok1"], "enable": True},
        {"id": "aiocqhttp_main", "tokens": ["tok2"], "enable": True},
    ])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_active_empty_fallback_enabled(monkeypatch):
    """_active_platforms 为空 → 回退该平台 enable=True。"""
    a, _ = _adapter(monkeypatch, [
        {"id": "aiocqhttp_main", "tokens": ["tok1"], "enable": True},
    ], active=set())
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_active_empty_fallback_disabled(monkeypatch):
    a, _ = _adapter(monkeypatch, [
        {"id": "aiocqhttp_main", "tokens": ["tok1"], "enable": False},
    ], active=set())
    assert a.binding_platform_for("tok1") is None


def test_unbind_token_removes_from_all_platforms(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "aiocqhttp_main", "tokens": ["tok1", "tok2"], "enable": True},
    ])
    a.unbind_token("tok1")
    assert fake["platform"][0]["tokens"] == ["tok2"]
    assert fake.saved is True


def test_unbind_token_no_change_no_save(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "aiocqhttp_main", "tokens": ["tok2"], "enable": True},
    ])
    a.unbind_token("tok1")
    assert fake.saved is False          # 无变更不落盘


def test_unbind_token_skips_botapi_entry(monkeypatch):
    """botapi 条目自身 tokens 不被动（账户注册表）。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["tok1"], "enable": True},
        {"id": "aiocqhttp_main", "tokens": ["tok1"], "enable": True},
    ])
    a.unbind_token("tok1")
    assert fake["platform"][0]["tokens"] == ["tok1"]   # botapi 条目保留
    assert fake["platform"][1]["tokens"] == []
