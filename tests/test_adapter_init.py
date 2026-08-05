import asyncio

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
    """重置插件配置单例 → tmp_path，预写空配置。"""
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
async def test_init_new_signature(tmp_path, monkeypatch):
    """__init__ 新签名 (host, port, event_queue)，不再接受 platform_config。"""
    monkeypatch.setattr(
        "astrbot_plugin_botapi.adapter.astrbot_config",
        {"data_path": str(tmp_path), "callback_api_base": ""},
    )
    adapter = BotApiAdapter("0.0.0.0", 9000, asyncio.Queue())
    assert adapter.platform_id == "botapi"
    assert adapter.client_self_id          # 自生成
    assert adapter.meta().id == "botapi"
    assert adapter.cfg.tokens == []
    assert adapter._upload_dir.exists()


@pytest.mark.asyncio
async def test_commit_event_puts_to_queue():
    from astrbot_plugin_botapi.models import SSEEvent
    q = asyncio.Queue(maxsize=10)
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter._event_queue = q
    evt = SSEEvent("message", {"x": 1})
    adapter.commit_event(evt)
    assert await q.get() is evt
