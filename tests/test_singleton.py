# tests/test_singleton.py
"""插件配置 schema + adapter 从插件配置文件读 host/port（纯插件自管，无单实例锁）。"""
import asyncio
import inspect
import json
import os
from pathlib import Path

import pytest


# 插件配置单例跨测试持久（adapter.__init__ 读全局单例），每测前复位，
# 否则上一测试的 tmp_path 插件配置会污染本测试（get_astrbot_config_path 已换新 tmp_path，
# 但单例仍缓存旧路径的配置）。
@pytest.fixture(autouse=True)
def _reset_plugin_conf():
    from astrbot_plugin_botapi import plugin_conf as pc
    pc.reset_plugin_conf()
    yield
    pc.reset_plugin_conf()


# ── 工具 ──

def _plugin_schema():
    schema_path = Path(__file__).resolve().parent.parent / "_conf_schema.json"
    assert schema_path.exists(), f"{schema_path} 不存在"
    return json.loads(schema_path.read_text(encoding="utf-8-sig"))


def _adapter(monkeypatch, plugin_conf):
    """构造真实 adapter（新签名）：monkeypatch get_astrbot_config_path 指向临时目录，写插件配置文件。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi.adapter import BotApiAdapter

    conf_dir = plugin_conf["conf_dir"]
    conf_path = os.path.join(conf_dir, "astrbot_plugin_botapi_config.json")
    os.makedirs(conf_dir, exist_ok=True)
    with open(conf_path, "w", encoding="utf-8-sig") as f:
        json.dump(plugin_conf["conf"], f, ensure_ascii=False)
    # adapter.__init__ 内部 lazy import get_astrbot_config_path → 必须 patch 源模块
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: conf_dir)
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "astrbot_config",
                        {"data_path": conf_dir, "callback_api_base": ""})
    a = BotApiAdapter("0.0.0.0", 9000, asyncio.Queue())
    return a, conf_path


def _base_plugin_conf(conf_dir):
    return {
        "conf_dir": conf_dir,
        "conf": {"host": "0.0.0.0", "port": 9000},
    }


# ── schema 声明 ──

def test_conf_schema_declares_host_port():
    """插件配置 schema 必须声明 host/port，不再有 botapi_bindings。"""
    schema = _plugin_schema()
    assert "host" in schema and "port" in schema
    assert "botapi_bindings" not in schema
    assert schema["host"]["type"] == "string"
    assert schema["port"]["type"] == "int"


# ── adapter 读插件配置 host/port ──

@pytest.mark.asyncio
async def test_init_reads_host_port_from_plugin_config(tmp_path, monkeypatch):
    """adapter.__init__ 从插件配置文件读 host/port。"""
    a, conf_path = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    assert a._host == "0.0.0.0"
    assert a._port == 9000
    assert os.path.exists(conf_path)


@pytest.mark.asyncio
async def test_init_plugin_config_overrides_platform_config(tmp_path, monkeypatch):
    """插件配置里显式 host/port 优先于构造参数。"""
    c = _base_plugin_conf(str(tmp_path))
    c["conf"] = {"host": "10.1.1.1", "port": 8888}
    a, _ = _adapter(monkeypatch, c)
    assert a._host == "10.1.1.1"
    assert a._port == 8888


@pytest.mark.asyncio
async def test_init_missing_plugin_config_file_uses_defaults(tmp_path, monkeypatch):
    """插件配置文件不存在（首次启动）→ AstrBotConfig 按 schema 创建，取到默认 0.0.0.0:9000。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    import astrbot_plugin_botapi.adapter as adapter_mod
    conf_dir = str(tmp_path)
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: conf_dir)
    monkeypatch.setattr(adapter_mod, "astrbot_config",
                        {"data_path": conf_dir, "callback_api_base": ""})
    a = BotApiAdapter("0.0.0.0", 9000, asyncio.Queue())
    assert a._host == "0.0.0.0"
    assert a._port == 9000
    # 首次启动按 schema 落盘默认配置
    assert os.path.exists(os.path.join(conf_dir, "astrbot_plugin_botapi_config.json"))


# ── run() 使用插件配置 host/port ──

@pytest.mark.asyncio
async def test_run_uses_plugin_config_host_port(tmp_path, monkeypatch):
    """run() 把插件配置的 host/port 传给 app.run_task。"""
    c = _base_plugin_conf(str(tmp_path))
    c["conf"] = {"host": "0.0.0.0", "port": 9000}
    a, _ = _adapter(monkeypatch, c)

    captured = {}

    def fake_run_task(*, host, port, shutdown_trigger=None):
        captured["host"] = host
        captured["port"] = port

        async def _never():
            await asyncio.Event().wait()

        return _never()

    a.app.run_task = fake_run_task
    coro = a.run()
    assert captured == {"host": "0.0.0.0", "port": 9000}
    coro.close()


@pytest.mark.asyncio
async def test_run_returns_coroutine_without_binding(tmp_path, monkeypatch):
    """run() 返回协程（供 Star 拉起），构造本身不绑定端口。"""
    c = _base_plugin_conf(str(tmp_path))
    c["conf"] = {"host": "127.0.0.1", "port": 0}
    a, _ = _adapter(monkeypatch, c)
    coro = a.run()
    assert inspect.iscoroutine(coro)   # 返回协程对象
    assert not coro.cr_running         # 未开始执行 → 未绑定端口
    coro.close()
