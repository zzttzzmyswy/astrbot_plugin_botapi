# tests/test_binding_storage.py — 绑定=插件配置 bindings 列表（token→platform_id 一对一）
import json
import os
from types import SimpleNamespace
import pytest


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """重置插件配置单例 → tmp_path，预写初始配置（tokens + bindings）。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": ["tok1"],
                   "bindings": [{"token": "tok1", "platform_id": "aiocqhttp_main"}],
                   "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _adapter(active=None):
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
    return a


def _set_platform_config(monkeypatch, platforms):
    """monkeypatch adapter 模块的 astrbot_config（绑定回退 enable 判定用）。"""
    import astrbot_plugin_botapi.adapter as adapter_mod

    class _FakeCfg(dict):
        def __init__(self, plats):
            super().__init__({"platform": plats})

        def save_config(self):
            self.saved = True

    fake = _FakeCfg(platforms)
    monkeypatch.setattr(adapter_mod, "astrbot_config", fake)
    return fake


def test_binding_platform_for_returns_platform():
    a = _adapter()
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_unbound_returns_none():
    a = _adapter()
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_bindings([])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_inactive_returns_none():
    a = _adapter(active={"telegram_x"})
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_active_empty_fallback_enabled(monkeypatch):
    """_active_platforms 为空 → 回退该平台 enable=True。"""
    a = _adapter(active=set())
    _set_platform_config(monkeypatch, [{"id": "aiocqhttp_main", "enable": True}])
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_active_empty_fallback_disabled(monkeypatch):
    a = _adapter(active=set())
    _set_platform_config(monkeypatch, [{"id": "aiocqhttp_main", "enable": False}])
    assert a.binding_platform_for("tok1") is None


def test_unbind_token_removes_from_bindings():
    a = _adapter()
    a.unbind_token("tok1")
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == []


def test_unbind_token_no_change_no_save():
    a = _adapter()
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_bindings([])
    calls = []
    orig_save = pc.save
    pc.save = lambda: calls.append(True)
    try:
        a.unbind_token("tok1")
        assert calls == []          # 无变更不落盘
    finally:
        pc.save = orig_save


def test_bind_token_writes_bindings_one_to_one():
    a = _adapter()
    a.bind_token("tok1", "telegram_x")
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == [{"token": "tok1", "platform_id": "telegram_x"}]


def test_bind_token_moves_existing_binding():
    a = _adapter()
    a.bind_token("tok1", "telegram_x")
    a.bind_token("tok1", "discord_y")
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == [{"token": "tok1", "platform_id": "discord_y"}]


def test_bind_token_skips_botapi_target():
    a = _adapter()
    a.bind_token("tok1", "other_botapi")
    from astrbot_plugin_botapi import plugin_conf as pc
    # 目标为 botapi 类型平台条目 → 不写入，保留原绑定
    assert pc.get_bindings() == [{"token": "tok1", "platform_id": "aiocqhttp_main"}]
