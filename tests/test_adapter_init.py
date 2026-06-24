import asyncio

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter


@pytest.mark.asyncio
async def test_init_with_full_platform_config(tmp_path, monkeypatch):
    """__init__ 必须容忍 platform_config 里 @register_platform_adapter 自动补的 type/enable/id。
    回归测试：之前 BotApiConfig(**platform_config) 会 TypeError 'type'。"""
    monkeypatch.setattr(
        "astrbot_plugin_botapi.adapter.astrbot_config",
        {"data_path": str(tmp_path), "callback_api_base": ""},
    )
    platform_config = {
        "type": "botapi", "enable": True, "id": "botapi",
        "host": "127.0.0.1", "port": 8080, "tokens": ["t1", "t2"],
    }
    adapter = BotApiAdapter(platform_config, {}, asyncio.Queue())
    assert adapter.cfg.host == "127.0.0.1"
    assert adapter.cfg.port == 8080
    assert adapter.cfg.tokens == ["t1", "t2"]
    assert adapter.platform_id == "botapi"
    assert adapter._upload_dir.exists()        # mkdir 执行
    assert adapter._media_enabled is False     # callback_api_base 为空
