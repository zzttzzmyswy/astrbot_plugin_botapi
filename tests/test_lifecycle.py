# tests/test_lifecycle.py — Star 类级 initialize/terminate 自起/自停服务器（纯插件自管）
# Task 6：插件 enable 即自建 adapter + 自起 Quart 服务器；禁用时停服务器 + 复位 runtime。
# 另含 Task 2 删自 test_singleton.py 的 sync_active_platforms 测试重建。
import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar
from astrbot_plugin_botapi.runtime import runtime as _get_runtime


@pytest.fixture(autouse=True)
def _cleanup_runtime():
    rt = _get_runtime()
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None
    yield
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None
    BotApiStar._server_task = None


@pytest.fixture(autouse=True)
def _conf(monkeypatch, tmp_path):
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


def _fake_context():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()
        _event_queue = asyncio.Queue()

        def register_web_api(self, route, handler, methods, desc):
            registered.append(route)

        def get_event_queue(self):
            return self._event_queue

    return FakeContext(), registered


def _bare_adapter():
    """构造仅含活跃平台注入所需属性的 adapter（避免 4.26 环境 __init__ 路径噪音）。"""
    import astrbot_plugin_botapi.adapter as adapter_mod
    a = object.__new__(adapter_mod.BotApiAdapter)
    a._active_platforms = set()
    return a


@pytest.mark.asyncio
async def test_initialize_builds_adapter_and_starts_server(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    ctx, _ = _fake_context()
    star = BotApiStar(ctx, None)
    started = []

    def fake_run(*a, **k):
        async def _never():
            await asyncio.Event().wait()
        started.append(True)
        return _never()

    monkeypatch.setattr(BotApiAdapter, "run", fake_run)
    await BotApiStar.initialize()
    rt = _get_runtime()
    assert rt.adapter is not None          # adapter 已建
    assert rt.adapter is not ctx._event_queue is not None
    assert started == [True]               # 服务器已起
    await BotApiStar.terminate()


@pytest.mark.asyncio
async def test_initialize_idempotent(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    ctx, _ = _fake_context()
    star = BotApiStar(ctx, None)
    started = []

    def fake_run(*a, **k):
        async def _never():
            await asyncio.Event().wait()
        started.append(True)
        return _never()

    monkeypatch.setattr(BotApiAdapter, "run", fake_run)
    await BotApiStar.initialize()
    await BotApiStar.initialize()          # 二次调用不重建
    assert started == [True]
    await BotApiStar.terminate()


@pytest.mark.asyncio
async def test_terminate_stops_server_and_resets_runtime(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    ctx, _ = _fake_context()
    star = BotApiStar(ctx, None)
    cancelled = []

    class _Task:
        def cancel(self):
            cancelled.append(True)

    BotApiStar._server_task = _Task()
    await BotApiStar.terminate()
    rt = _get_runtime()
    assert rt.adapter is None              # runtime 复位
    assert cancelled == [True]             # 服务器 task 被取消


def test_star_injects_active_platforms():
    """Star.sync_active_platforms 把已启用平台集合注入 adapter._active_platforms。"""
    ctx, _ = _fake_context()
    star = BotApiStar(ctx, None)
    rt = _get_runtime()
    a = _bare_adapter()
    rt.adapter = a
    try:
        star.sync_active_platforms({"aiocqhttp_main", "telegram_x"})
        assert a._active_platforms == {"aiocqhttp_main", "telegram_x"}
    finally:
        rt.adapter = None


def test_sync_active_platforms_no_adapter_is_noop():
    """star 初始化时 adapter 尚为 None（plugin_manager.reload 先于 platform_manager.initialize）→ 不抛异常。"""
    ctx, _ = _fake_context()
    star = BotApiStar(ctx, None)
    rt = _get_runtime()
    rt.adapter = None
    star.sync_active_platforms({"aiocqhttp_main"})   # 不应抛异常
