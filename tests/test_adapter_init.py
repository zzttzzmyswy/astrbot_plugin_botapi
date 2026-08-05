import asyncio

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi.runtime import runtime


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    import json, os
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


@pytest.mark.asyncio
async def test_plugin_mode_full_init(monkeypatch):
    """_star_managed=True → 插件模式：完整初始化，设 runtime().adapter，platform_id=botapi。"""
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config",
                        {"data_path": str(tmp_path := "/tmp/botapi-test"), "callback_api_base": ""})
    import os
    os.makedirs(tmp_path, exist_ok=True)
    adapter = BotApiAdapter(
        {"id": "botapi", "_star_managed": True, "host": "0.0.0.0", "port": 9000},
        {}, asyncio.Queue())
    assert adapter._is_entry is False
    assert adapter.platform_id == "botapi"
    assert runtime().adapter is adapter
    assert adapter.app is not None          # 建了 Quart
    assert adapter.cfg.tokens == []
    # cleanup
    runtime().adapter = None


@pytest.mark.asyncio
async def test_entry_mode_minimal(monkeypatch):
    """无 _star_managed → 条目模式：极简壳，不设 runtime().adapter、不建 app、platform_id=条目id。"""
    monkeypatch.setattr("astrbot_plugin_botapi.adapter.astrbot_config",
                        {"data_path": "/tmp/botapi-test2", "callback_api_base": ""})
    import os
    os.makedirs("/tmp/botapi-test2", exist_ok=True)
    adapter = BotApiAdapter({"id": "botapi_a", "type": "botapi"}, {}, asyncio.Queue())
    assert adapter._is_entry is True
    assert adapter.platform_id == "botapi_a"
    assert runtime().adapter is None        # 不设 runtime
    assert not hasattr(adapter, "app")      # 不建 app