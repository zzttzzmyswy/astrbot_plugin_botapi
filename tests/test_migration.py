# tests/test_migration.py — v3.0.0 旧 botapi_bindings/host/port/nicknames → 新布局
import pytest


def _adapter(monkeypatch, platforms, self_config):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.config = dict(self_config)
    import astrbot_plugin_botapi.adapter as adapter_mod
    fake = _FakeCfg(platforms)
    monkeypatch.setattr(adapter_mod, "astrbot_config", fake)
    return a, fake


class _FakeCfg(dict):
    def __init__(self, platforms):
        super().__init__({"platform": platforms})
        self.platforms = platforms
        self.saved = None

    def save_config(self):
        self.saved = True


def test_migrate_expands_bindings_into_platform_tokens(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1", "t2"],
         "botapi_bindings": {"t1": "aiocqhttp_main"}, "nicknames": {"t1": "x"},
         "host": "0.0.0.0", "port": 9000},
        {"id": "aiocqhttp_main", "enable": True},
    ], {"id": "botapi", "botapi_bindings": {"t1": "aiocqhttp_main"}})
    a._migrate_legacy_bindings()
    assert fake.platforms[1]["tokens"] == ["t1"]
    assert "botapi_bindings" not in fake.platforms[0]
    assert "nicknames" not in fake.platforms[0]
    assert "host" not in fake.platforms[0]
    assert "port" not in fake.platforms[0]
    assert fake.saved is True


def test_migrate_idempotent(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"]},
        {"id": "aiocqhttp_main", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"})
    a._migrate_legacy_bindings()
    a._migrate_legacy_bindings()
    assert fake.saved is None            # 无旧键 → 不落盘
    assert fake.platforms[1]["tokens"] == ["t1"]


def test_migrate_missing_target_skipped(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"],
         "botapi_bindings": {"t1": "missing_platform"}},
    ], {"id": "botapi", "botapi_bindings": {"t1": "missing_platform"}})
    a._migrate_legacy_bindings()
    assert fake.saved is True            # 清理键也算变更
    assert "botapi_bindings" not in fake.platforms[0]


def test_migrate_skips_own_botapi_entry(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"],
         "botapi_bindings": {"t1": "other_botapi"}},
        {"id": "other_botapi", "type": "botapi", "tokens": [], "enable": True},
    ], {"id": "botapi", "botapi_bindings": {"t1": "other_botapi"}})
    a._migrate_legacy_bindings()
    assert fake.platforms[1]["tokens"] == []   # botapi 类型平台不承接绑定
