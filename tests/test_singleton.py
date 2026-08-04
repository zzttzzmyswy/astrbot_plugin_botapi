# tests/test_singleton.py
"""单实例化 Task 1：插件配置 schema + adapter 从插件配置文件读 host/port 起单例 Quart。

不实际监听端口；验证 adapter.__init__ 正确从插件配置文件（{get_astrbot_config_path()}/
astrbot_plugin_botapi_config.json）解析出 host/port，并落到 self._host/_port，
供 run() 使用（run() 仍由 PlatformManager 调用，但 host/port 来自插件配置而非平台配置）。
"""
import asyncio
import inspect
import json
import os
from pathlib import Path

import pytest


# ── 工具 ──

def _plugin_schema():
    schema_path = Path(__file__).resolve().parent.parent / "_conf_schema.json"
    assert schema_path.exists(), f"{schema_path} 不存在"
    return json.loads(schema_path.read_text(encoding="utf-8-sig"))


def _adapter(monkeypatch, plugin_conf):
    """构造真实 adapter：monkeypatch get_astrbot_config_path 指向临时目录，写插件配置文件。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi.adapter import BotApiAdapter

    # 写插件配置文件（临时目录/astrbot_plugin_botapi_config.json）
    conf_dir = plugin_conf["conf_dir"]
    conf_path = os.path.join(conf_dir, "astrbot_plugin_botapi_config.json")
    os.makedirs(conf_dir, exist_ok=True)
    with open(conf_path, "w", encoding="utf-8-sig") as f:
        json.dump(plugin_conf["conf"], f, ensure_ascii=False)
    # adapter.__init__ 内部 lazy import get_astrbot_config_path → 必须 patch 源模块
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: conf_dir)

    platform_config = {
        "type": "botapi", "enable": True, "id": "botapi",
        "host": "127.0.0.1", "port": 7777, "tokens": ["t1"],
    }
    a = BotApiAdapter(platform_config, {}, asyncio.Queue())
    return a, conf_path


def _base_plugin_conf(conf_dir):
    return {
        "conf_dir": conf_dir,
        "conf": {"host": "0.0.0.0", "port": 9000, "botapi_bindings": {}},
    }


# ── schema 声明 ──

def test_conf_schema_declares_host_port_bindings():
    """插件配置 schema 必须声明 host/port/botapi_bindings。"""
    schema = _plugin_schema()
    assert "host" in schema and "port" in schema and "botapi_bindings" in schema
    assert schema["host"]["type"] == "string"
    assert schema["port"]["type"] == "int"
    assert schema["botapi_bindings"]["type"] == "object"


# ── adapter 读插件配置 host/port ──

@pytest.mark.asyncio
async def test_init_reads_host_port_from_plugin_config(tmp_path, monkeypatch):
    """adapter.__init__ 从插件配置文件读 host/port，而非平台配置（127.0.0.1:7777）。"""
    a, conf_path = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    assert a._host == "0.0.0.0"
    assert a._port == 9000
    assert os.path.exists(conf_path)


@pytest.mark.asyncio
async def test_init_plugin_config_overrides_platform_config(tmp_path, monkeypatch):
    """插件配置里显式 host/port 优先于平台配置。"""
    c = _base_plugin_conf(str(tmp_path))
    c["conf"] = {"host": "10.1.1.1", "port": 8888, "botapi_bindings": {}}
    a, _ = _adapter(monkeypatch, c)
    assert a._host == "10.1.1.1"
    assert a._port == 8888


@pytest.mark.asyncio
async def test_init_missing_plugin_config_file_uses_defaults(tmp_path, monkeypatch):
    """插件配置文件不存在（首次启动）→ AstrBotConfig 按 schema 创建，取到默认 0.0.0.0:9000。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    conf_dir = str(tmp_path)
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: conf_dir)
    platform_config = {"type": "botapi", "id": "botapi", "tokens": ["t1"]}
    a = BotApiAdapter(platform_config, {}, asyncio.Queue())
    assert a._host == "0.0.0.0"
    assert a._port == 9000
    # 首次启动按 schema 落盘默认配置
    assert os.path.exists(os.path.join(conf_dir, "astrbot_plugin_botapi_config.json"))


# ── 迁移回退：旧平台配置 host/port ──

@pytest.mark.asyncio
async def test_legacy_port_reads_platform_config(monkeypatch, tmp_path):
    """_legacy_port 从 astrbot_config['platform'] 里 type=botapi 条目读 host/port。"""
    import astrbot_plugin_botapi.adapter as adapter_mod
    a, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "botapi", "type": "botapi", "host": "1.2.3.4", "port": 6666}]})
    assert a._legacy_port() == ("1.2.3.4", 6666)


@pytest.mark.asyncio
async def test_legacy_port_ignores_other_platform_types(monkeypatch, tmp_path):
    """_legacy_port 忽略非 botapi 平台条目。"""
    import astrbot_plugin_botapi.adapter as adapter_mod
    a, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "qq", "type": "aiocqhttp", "host": "9.9.9.9", "port": 1234}]})
    assert a._legacy_port() == (None, None)


@pytest.mark.asyncio
async def test_init_legacy_fallback_when_plugin_host_port_empty(tmp_path, monkeypatch):
    """插件配置 host/port 显式置空时回退旧平台配置（迁移场景）。"""
    c = _base_plugin_conf(str(tmp_path))
    c["conf"] = {"host": "", "port": 0, "botapi_bindings": {}}
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "botapi", "type": "botapi", "host": "1.2.3.4", "port": 6666}]})
    a, _ = _adapter(monkeypatch, c)
    assert a._host == "1.2.3.4"
    assert a._port == 6666


# ── run() 使用插件配置 host/port ──

@pytest.mark.asyncio
async def test_run_uses_plugin_config_host_port(tmp_path, monkeypatch):
    """run() 把插件配置的 host/port 传给 app.run_task（而非平台配置的 127.0.0.1:7777）。"""
    c = _base_plugin_conf(str(tmp_path))
    c["conf"] = {"host": "0.0.0.0", "port": 9000, "botapi_bindings": {}}
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
    """单实例化前提：run() 返回协程（供 PlatformManager 驻留），构造本身不绑定端口。"""
    c = _base_plugin_conf(str(tmp_path))
    c["conf"] = {"host": "127.0.0.1", "port": 0, "botapi_bindings": {}}
    a, _ = _adapter(monkeypatch, c)
    coro = a.run()
    assert inspect.iscoroutine(coro)   # 返回协程对象
    assert not coro.cr_running         # 未开始执行 → 未绑定端口
    coro.close()
