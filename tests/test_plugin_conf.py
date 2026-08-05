# tests/test_plugin_conf.py — 全局插件配置单例（账户数据源 + host/port）
import json
import os
import pytest


@pytest.fixture(autouse=True)
def _reset_conf(monkeypatch, tmp_path):
    """每个测试前重置单例，patch get_astrbot_config_path → tmp_path。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    yield
    pc.reset_plugin_conf()


def _conf_path():
    from astrbot_plugin_botapi import plugin_conf as pc
    import astrbot.core.utils.astrbot_path as p
    return os.path.join(p.get_astrbot_config_path(), "astrbot_plugin_botapi_config.json")


def test_load_creates_file_with_defaults():
    from astrbot_plugin_botapi import plugin_conf as pc
    conf = pc.load_plugin_conf()
    assert conf.get("host") == "0.0.0.0"
    assert conf.get("port") == 9000
    assert conf.get("tokens") == []
    assert conf.get("sessions") == []
    assert os.path.exists(_conf_path())


def test_load_is_singleton():
    from astrbot_plugin_botapi import plugin_conf as pc
    assert pc.load_plugin_conf() is pc.load_plugin_conf()


def test_reset_clears_singleton():
    from astrbot_plugin_botapi import plugin_conf as pc
    first = pc.load_plugin_conf()
    pc.reset_plugin_conf()
    assert pc.load_plugin_conf() is not first


def test_tokens_roundtrip_persists(tmp_path):
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_tokens(["t1", "t2"])
    pc.save()
    # 重新加载（reset 后从磁盘读）
    pc.reset_plugin_conf()
    assert pc.get_tokens() == ["t1", "t2"]


def test_sessions_map_roundtrip_persists(tmp_path):
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.set_sessions_map({"t1": [{"id": "abc", "name": "x", "created_at": 1}]})
    pc.save()
    pc.reset_plugin_conf()
    assert pc.get_sessions_map()["t1"][0]["id"] == "abc"


def test_schema_declares_account_keys():
    from pathlib import Path
    import json as _json
    schema = _json.loads(
        (Path(__file__).resolve().parent.parent / "_conf_schema.json").read_text(encoding="utf-8-sig")
    )
    for key in ("host", "port", "tokens", "sessions"):
        assert key in schema, f"schema 缺 {key}"
    assert "bindings" not in schema          # 绑定已由平台 tokens 承载
    assert schema["tokens"]["type"] == "list"
    assert schema["sessions"]["type"] == "list"
