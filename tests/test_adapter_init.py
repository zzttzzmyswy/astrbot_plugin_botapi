import asyncio

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """重置插件配置单例 → tmp_path，预写空配置（账户数据源在插件配置）。"""
    import json
    import os
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: str(tmp_path))
    conf_path = os.path.join(str(tmp_path), "astrbot_plugin_botapi_config.json")
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump({"host": "0.0.0.0", "port": 9000, "tokens": [],
                   "bindings": [], "sessions": []}, f)
    yield
    pc.reset_plugin_conf()


@pytest.mark.asyncio
async def test_init_with_full_platform_config(tmp_path, monkeypatch):
    """__init__ 必须容忍 platform_config 里 @register_platform_adapter 自动补的 type/enable/id。
    回归测试：之前 BotApiConfig(**platform_config) 会 TypeError 'type'。
    tokens 账户注册表已迁入插件配置：platform_config 里的 tokens 不再喂 cfg（默认空）。"""
    monkeypatch.setattr(
        "astrbot_plugin_botapi.adapter.astrbot_config",
        {"data_path": str(tmp_path), "callback_api_base": ""},
    )
    platform_config = {
        "type": "botapi", "enable": True, "id": "botapi",
        "host": "127.0.0.1", "port": 8080,
    }
    adapter = BotApiAdapter(platform_config, {}, asyncio.Queue())
    assert adapter.cfg.tokens == []          # 账户从插件配置读（默认空）
    assert adapter.platform_id == "botapi"
    assert adapter._upload_dir.exists()        # mkdir 执行
    assert adapter._media_enabled is False     # callback_api_base 为空
