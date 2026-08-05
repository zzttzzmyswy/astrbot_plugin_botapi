# tests/test_binding_storage.py — 绑定=插件配置 bindings 表条目（token→platform 一对一）
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
                   "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _adapter(monkeypatch, bindings, active=None, platforms=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    from astrbot_plugin_botapi import plugin_conf as pc
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a.cfg = SimpleNamespace(tokens=list(pc.get_tokens()))
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = set(active if active is not None else {"aiocqhttp_main"})
    pc.set_bindings(list(bindings))
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "astrbot_config",
                        {"platform": platforms if platforms is not None else
                         [{"id": "aiocqhttp_main", "enable": True}]})
    return a


def test_binding_platform_for_returns_platform(monkeypatch):
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}])
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_unbound_returns_none(monkeypatch):
    a = _adapter(monkeypatch, [{"token": "tok2", "platform_id": "aiocqhttp_main"}])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_inactive_returns_none(monkeypatch):
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}],
                 active={"telegram_x"})
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_active_empty_fallback_enabled(monkeypatch):
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}],
                 active=set(),
                 platforms=[{"id": "aiocqhttp_main", "enable": True}])
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_active_empty_fallback_disabled(monkeypatch):
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}],
                 active=set(),
                 platforms=[{"id": "aiocqhttp_main", "enable": False}])
    assert a.binding_platform_for("tok1") is None


def test_bind_token_one_to_one_switches_platform(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}])
    a.bind_token("tok1", "aiocqhttp_backup")
    binds = pc.get_bindings()
    assert len(binds) == 1                      # 一对一：旧条目清除
    assert binds[0] == {"token": "tok1", "platform_id": "aiocqhttp_backup"}


def test_bind_token_allows_botapi_target(monkeypatch):
    """绑定到 botapi 条目（botapi_a）→ 允许（v3.0.3 禁止，现允许）。"""
    from astrbot_plugin_botapi import plugin_conf as pc
    a = _adapter(monkeypatch, [], active={"botapi_a", "aiocqhttp_main"},
                 platforms=[{"id": "botapi_a", "type": "botapi", "enable": True},
                            {"id": "aiocqhttp_main", "enable": True}])
    a.bind_token("tok1", "botapi_a")
    assert pc.get_bindings() == [{"token": "tok1", "platform_id": "botapi_a"}]


def test_binding_platform_for_botapi_entry(monkeypatch):
    """token 绑到 botapi_a 且活跃 → 返回 botapi_a。"""
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "botapi_a"}],
                 active={"botapi_a"})
    assert a.binding_platform_for("tok1") == "botapi_a"


def test_unbind_token_removes_entry(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"},
                               {"token": "tok2", "platform_id": "aiocqhttp_backup"}])
    a.unbind_token("tok1")
    binds = pc.get_bindings()
    assert all(b.get("token") != "tok1" for b in binds)
    assert len(binds) == 1


def test_unbind_token_no_change_no_save(monkeypatch):
    from astrbot_plugin_botapi import plugin_conf as pc
    a = _adapter(monkeypatch, [{"token": "tok1", "platform_id": "aiocqhttp_main"}])
    orig = list(pc.get_bindings())
    a.unbind_token("tok_absent")
    assert pc.get_bindings() == orig          # 无变更不落盘
