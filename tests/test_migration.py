# tests/test_migration.py — 平台条目账户数据 → 插件配置（全局），只做账户收敛 + 清理旧键
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
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


def _adapter(monkeypatch, platforms, self_config, plugin_conf=None):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    import astrbot_plugin_botapi.adapter as adapter_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.config = dict(self_config)
    a.platform_id = "botapi"
    if plugin_conf is not None:
        import json as _json
        import astrbot.core.utils.astrbot_path as astrbot_path_mod
        conf_path = os.path.join(astrbot_path_mod.get_astrbot_config_path(),
                                 "astrbot_plugin_botapi_config.json")
        with open(conf_path, "w", encoding="utf-8") as f:
            _json.dump(plugin_conf, f)
        pc.reset_plugin_conf()
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
    assert pc.get_tokens() == ["t1", "t2"]
    assert "tokens" not in fake.platforms[0]
    assert fake.saved is True


def test_migrate_plugin_tokens_already_present_is_noop(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": False},
    ], {"id": "botapi"}, plugin_conf={"host": "0.0.0.0", "port": 9000, "tokens": ["t1"], "sessions": []})
    a._migrate_accounts()
    assert fake.platforms[0].get("tokens") == ["t1"]
    assert fake.platforms[0]["enable"] is False
    assert fake.saved is None


def test_migrate_cleans_legacy_keys(monkeypatch):
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True,
         "botapi_bindings": {"t1": "aiocqhttp_main"}, "nicknames": {"t1": "x"},
         "host": "0.0.0.0", "port": 9000, "sessions": {"t1": []}},
    ], {"id": "botapi", "botapi_bindings": {"t1": "aiocqhttp_main"}})
    a._migrate_accounts()
    for key in ("tokens", "botapi_bindings", "nicknames", "host", "port", "sessions"):
        assert key not in fake.platforms[0], f"botapi 条目残留 {key}"
    # v3.0.3 起 botapi 非平台适配器，残留条目须禁用防启动报 adapter not found
    assert fake.platforms[0]["enable"] is False
    assert fake.saved is True


def test_migrate_disables_all_botapi_entries(monkeypatch):
    """所有 type==botapi 残留条目（含非 botapi id）迁移后 enable 变 False。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True,
         "host": "0.0.0.0", "port": 9000, "sessions": {"t1": []}},
        {"id": "botapi_legacy", "type": "botapi", "enable": True,
         "botapi_bindings": {"t2": "aiocqhttp_main"}},
    ], {"id": "botapi"})
    a._migrate_accounts()
    for p in fake.platforms:
        assert p["type"] == "botapi"
        assert p["enable"] is False
    # 旧键仍被清理
    assert "host" not in fake.platforms[0]
    assert "botapi_bindings" not in fake.platforms[1]
    assert fake.saved is True


def test_migrate_no_botapi_entries_is_noop(monkeypatch):
    """无 botapi 类型条目 → 迁移 no-op，不触发 save。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "aiocqhttp_main", "type": "aiocqhttp", "enable": True},
    ], {"id": "botapi"})
    a._migrate_accounts()
    assert fake.platforms[0] == {"id": "aiocqhttp_main", "type": "aiocqhttp", "enable": True}
    assert fake.saved is None


def test_migrate_keeps_bindings_intact(monkeypatch):
    """bindings 表已存在 → 迁移不动它（后台重建策略，不迁移平台 tokens）。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "aiocqhttp_main", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"}, plugin_conf={"host": "0.0.0.0", "port": 9000,
                                       "tokens": ["t1"], "bindings": [{"token": "t1", "platform_id": "aiocqhttp_main"}],
                                       "sessions": []})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_bindings() == [{"token": "t1", "platform_id": "aiocqhttp_main"}]
    assert fake.platforms[0].get("tokens") == ["t1"]   # 平台 tokens 不迁移/不删
