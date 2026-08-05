# tests/test_migration.py — 平台条目账户数据 → 插件配置（全局）
import json
import os
import pytest


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [], "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


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


def test_migrate_platform_tokens_to_plugin(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1", "t2"], "enable": True},
    ], {"id": "botapi"})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_tokens() == ["t1", "t2"]          # 平台 tokens → 插件
    assert "tokens" not in fake.platforms[0]         # 平台条目清空


def test_migrate_target_platform_tokens_to_bindings(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
        {"id": "aiocqhttp_main", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_tokens() == ["t1"]
    assert pc.get_bindings() == [{"token": "t1", "platform_id": "aiocqhttp_main"}]
    assert "tokens" not in fake.platforms[1]


def test_migrate_plugin_tokens_already_present_is_noop(monkeypatch):
    """插件 tokens 非空 → 跳过平台条目迁移（幂等）。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"})
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_tokens(["t1"])
    a._migrate_accounts()
    assert fake.platforms[0].get("tokens") == ["t1"]   # 不动平台条目（已迁过）
    assert pc.get_tokens() == ["t1"]


def test_migrate_cleans_botapi_entry_old_keys(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True,
         "botapi_bindings": {"t1": "aiocqhttp_main"}, "nicknames": {"t1": "x"},
         "host": "0.0.0.0", "port": 9000, "sessions": {"t1": []}},
        {"id": "aiocqhttp_main", "tokens": [], "enable": True},
    ], {"id": "botapi", "botapi_bindings": {"t1": "aiocqhttp_main"}})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_tokens() == ["t1"]
    assert pc.get_bindings() == [{"token": "t1", "platform_id": "aiocqhttp_main"}]
    for key in ("tokens", "botapi_bindings", "nicknames", "host", "port", "sessions"):
        assert key not in fake.platforms[0], f"botapi 条目残留 {key}"


def test_migrate_skips_botapi_type_target(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
        {"id": "other_botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == []                    # botapi 类型平台不承接绑定
    assert pc.get_tokens() == ["t1"]
