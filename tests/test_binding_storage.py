# tests/test_binding_storage.py — 绑定=token 出现在目标平台 tokens 列表
from types import SimpleNamespace
import pytest


def _adapter(monkeypatch, platforms=None, active=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": ["tok1"]}
    a.cfg = SimpleNamespace(tokens=["tok1"])
    a._sse_clients = {}
    a._token_to_origin = {}
    a._active_platforms = set(active if active is not None else {"aiocqhttp_main"})
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "astrbot_config", {
        "platform": list(platforms if platforms is not None else [
            {"id": "botapi", "type": "botapi", "tokens": ["tok1"], "enable": True},
            {"id": "aiocqhttp_main", "type": "aiocqhttp", "tokens": ["tok1"], "enable": True},
        ]),
    })
    return a


def test_binding_platform_for_returns_platform(monkeypatch):
    a = _adapter(monkeypatch)
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_unbound_returns_none(monkeypatch):
    a = _adapter(monkeypatch, platforms=[
        {"id": "botapi", "type": "botapi", "tokens": ["tok1"], "enable": True},
        {"id": "aiocqhttp_main", "tokens": [], "enable": True},
    ])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_inactive_returns_none(monkeypatch):
    """绑定的平台不在活跃列表 → 回退。"""
    a = _adapter(monkeypatch, active={"telegram_x"}, platforms=[
        {"id": "aiocqhttp_main", "tokens": ["tok1"], "enable": True},
    ])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_active_empty_fallback_enabled(monkeypatch):
    """_active_platforms 为空（重启后平台注入前）→ 回退 enable=True。"""
    a = _adapter(monkeypatch, active=set(), platforms=[
        {"id": "botapi", "type": "botapi", "enable": True},
        {"id": "aiocqhttp_main", "tokens": ["tok1"], "enable": True},
        {"id": "telegram_disabled", "tokens": ["tok1"], "enable": False},
    ])
    # 一对一配置：tok1 在 aiocqhttp_main（enable）+ telegram_disabled（disable）→ 第一个命中优先
    assert a.binding_platform_for("tok1") == "aiocqhttp_main"


def test_binding_platform_for_active_empty_fallback_disabled(monkeypatch):
    a = _adapter(monkeypatch, active=set(), platforms=[
        {"id": "telegram_disabled", "tokens": ["tok1"], "enable": False},
    ])
    assert a.binding_platform_for("tok1") is None


def test_binding_platform_for_skips_botapi_type(monkeypatch):
    """type=botapi 平台条目（回环）不参与绑定。"""
    a = _adapter(monkeypatch, platforms=[
        {"id": "other_botapi", "type": "botapi", "tokens": ["tok1"], "enable": True},
    ])
    assert a.binding_platform_for("tok1") is None


def test_unbind_token_removes_from_all_platforms(monkeypatch):
    fake = _FakeCfg([{"id": "botapi", "type": "botapi"},
                     {"id": "a", "tokens": ["tok1", "tok2"], "enable": True},
                     {"id": "b", "tokens": ["tok1"], "enable": True}])
    a = _adapter(monkeypatch)
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config", fake)
    a.unbind_token("tok1")
    assert fake.platforms[1]["tokens"] == ["tok2"]
    assert fake.platforms[2]["tokens"] == []
    assert fake.saved is True


def test_unbind_token_no_change_no_save(monkeypatch):
    fake = _FakeCfg([{"id": "botapi", "type": "botapi"},
                     {"id": "a", "tokens": [], "enable": True}])
    a = _adapter(monkeypatch)
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config", fake)
    a.unbind_token("tok1")
    assert fake.saved is None   # 无变更不落盘


def test_bind_token_writes_target_and_unbinds_old(monkeypatch):
    fake = _FakeCfg([{"id": "botapi", "type": "botapi"},
                     {"id": "a", "tokens": ["tok1"], "enable": True},
                     {"id": "b", "tokens": [], "enable": True}])
    a = _adapter(monkeypatch)
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config", fake)
    a.bind_token("tok1", "b")
    assert fake.platforms[1]["tokens"] == []    # 从旧平台移除（一对一）
    assert fake.platforms[2]["tokens"] == ["tok1"]
    assert fake.saved is True


def test_bind_token_skips_botapi_target(monkeypatch):
    fake = _FakeCfg([{"id": "botapi", "type": "botapi"},
                     {"id": "other_botapi", "type": "botapi", "tokens": []}])
    a = _adapter(monkeypatch)
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config", fake)
    a.bind_token("tok1", "other_botapi")   # 目标为 botapi 类型 → 不写入
    assert fake.platforms[1]["tokens"] == []


class _FakeCfg(dict):
    """模拟 astrbot_config：带 platform 列表 + save_config 计数。"""
    def __init__(self, platforms):
        super().__init__({"platform": platforms})
        self.platforms = platforms
        self.saved = None

    def save_config(self):
        self.saved = True
