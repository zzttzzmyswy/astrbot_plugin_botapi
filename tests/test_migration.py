# tests/test_migration.py — 平台条目账户数据 → 插件配置（全局）；遗留绑定 → 平台 tokens
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
    a._legacy_bindings = list((plugin_conf or {}).get("bindings") or [])
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
    assert pc.get_tokens() == ["t1", "t2"]          # 平台 tokens → 插件
    assert "tokens" not in fake.platforms[0]         # 平台条目清空
    assert fake.saved is True                        # 平台条目清理持久化到 astrbot_config（防复活）


def test_migrate_plugin_tokens_already_present_is_noop(monkeypatch):
    """插件 tokens 非空 → 跳过平台条目迁移（幂等）。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"}, plugin_conf={"host": "0.0.0.0", "port": 9000, "tokens": ["t1"], "sessions": []})
    a._migrate_accounts()
    assert fake.platforms[0].get("tokens") == ["t1"]   # 不动平台条目（已迁过）
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_tokens() == ["t1"]
    assert fake.saved is None                          # 无清理变更 → 不持久化


def test_migrate_bindings_to_platform_tokens(monkeypatch):
    """插件配置遗留 bindings → 目标平台 tokens，随后删除 bindings 键。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
        {"id": "aiocqhttp_main", "tokens": [], "enable": True},
    ], {"id": "botapi"}, plugin_conf={
        "host": "0.0.0.0", "port": 9000, "tokens": ["t1"], "sessions": [],
        "bindings": [{"token": "t1", "platform_id": "aiocqhttp_main"}],
    })
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert fake.platforms[1]["tokens"] == ["t1"]       # 绑定展开到目标平台 tokens
    assert "bindings" not in pc.load_plugin_conf()      # bindings 键已删除
    assert pc.get_tokens() == ["t1"]


def test_migrate_bindings_merge_dedup(monkeypatch):
    """目标平台 tokens 已有该 token → 迁移不重复追加。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
        {"id": "aiocqhttp_main", "tokens": ["t1"], "enable": True},
    ], {"id": "botapi"}, plugin_conf={
        "host": "0.0.0.0", "port": 9000, "tokens": ["t1"], "sessions": [],
        "bindings": [{"token": "t1", "platform_id": "aiocqhttp_main"}],
    })
    a._migrate_accounts()
    assert fake.platforms[1]["tokens"] == ["t1"]


def test_migrate_legacy_botapi_bindings_dict_to_platform_tokens(monkeypatch):
    """旧 botapi_bindings dict（botapi 条目）→ 目标平台 tokens。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True,
         "botapi_bindings": {"t1": "aiocqhttp_main"}, "nicknames": {"t1": "x"},
         "host": "0.0.0.0", "port": 9000, "sessions": {"t1": []}},
        {"id": "aiocqhttp_main", "tokens": [], "enable": True},
    ], {"id": "botapi", "botapi_bindings": {"t1": "aiocqhttp_main"}})
    a._migrate_accounts()
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_tokens() == ["t1"]
    assert fake.platforms[1]["tokens"] == ["t1"]       # 旧 dict → 目标平台 tokens
    for key in ("tokens", "botapi_bindings", "nicknames", "host", "port", "sessions"):
        assert key not in fake.platforms[0], f"botapi 条目残留 {key}"
    assert fake.saved is True                          # 旧键清理持久化到 astrbot_config


def test_migrate_skips_botapi_type_target(monkeypatch):
    """bindings 指向 botapi 类型平台 → 不写入（排除自身回环）。"""
    a, fake = _adapter(monkeypatch, [
        {"id": "botapi", "type": "botapi", "tokens": ["t1"], "enable": True},
        {"id": "other_botapi", "type": "botapi", "tokens": [], "enable": True},
    ], {"id": "botapi"}, plugin_conf={
        "host": "0.0.0.0", "port": 9000, "tokens": ["t1"], "sessions": [],
        "bindings": [{"token": "t1", "platform_id": "other_botapi"}],
    })
    a._migrate_accounts()
    assert fake.platforms[1].get("tokens") in ([], None)   # botapi 类型平台不承接绑定
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.get_tokens() == ["t1"]
