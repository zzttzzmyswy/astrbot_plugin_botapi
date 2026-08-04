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


# 模块级 _SERVER_STARTED 跨测试持久（任务 5 单实例锁），每测前复位避免污染
@pytest.fixture(autouse=True)
def _reset_server_started(monkeypatch):
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "_SERVER_STARTED", False)


# 插件配置单例跨测试持久（任务 2 后 adapter.__init__ 读全局单例），每测前复位，
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
    """adapter.__init__ 从插件配置文件读 host/port，而非平台配置（127.0.0.1:7777）。"""
    a, conf_path = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    assert a._host == "0.0.0.0"
    assert a._port == 9000
    assert os.path.exists(conf_path)


@pytest.mark.asyncio
async def test_init_plugin_config_overrides_platform_config(tmp_path, monkeypatch):
    """插件配置里显式 host/port 优先于平台配置。"""
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
    c["conf"] = {"host": "", "port": 0}
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
    """单实例化前提：run() 返回协程（供 PlatformManager 驻留），构造本身不绑定端口。"""
    c = _base_plugin_conf(str(tmp_path))
    c["conf"] = {"host": "127.0.0.1", "port": 0}
    a, _ = _adapter(monkeypatch, c)
    coro = a.run()
    assert inspect.iscoroutine(coro)   # 返回协程对象
    assert not coro.cr_running         # 未开始执行 → 未绑定端口
    coro.close()


# ── 防御：非数字端口不抛异常 ──

@pytest.mark.asyncio
async def test_legacy_port_non_numeric_port_returns_none(monkeypatch, tmp_path):
    """_legacy_port 遇到非数字端口（如 "abc"）返回 None 而非抛异常，供回退链用 or 9000 处理。"""
    import astrbot_plugin_botapi.adapter as adapter_mod
    a, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    monkeypatch.setattr(adapter_mod, "astrbot_config", {"platform": [
        {"id": "botapi", "type": "botapi", "host": "1.2.3.4", "port": "abc"}]})
    assert a._legacy_port() == ("1.2.3.4", None)


@pytest.mark.asyncio
async def test_init_non_numeric_legacy_port_does_not_crash(tmp_path, monkeypatch):
    """_legacy_port 遇到非数字端口不抛异常，防止回退链崩溃。"""
    import astrbot.core.utils.astrbot_path as astrbot_path_mod
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    import astrbot_plugin_botapi.adapter as adapter_mod

    conf_dir = str(tmp_path)
    # 不写配置文件，让插件配置按 schema 创建默认配置
    monkeypatch.setattr(astrbot_path_mod, "get_astrbot_config_path", lambda: conf_dir)

    # 旧平台配置有非数字 port，直接替换 astrbot_config
    mock_astrbot_config = {"platform": [
        {"id": "botapi", "type": "botapi", "host": "1.2.3.4", "port": "xyz"}]}
    monkeypatch.setattr(adapter_mod, "astrbot_config", mock_astrbot_config)

    platform_config = {"type": "botapi", "id": "botapi", "tokens": ["t1"]}
    # 关键：这个构造应该不抛异常，即使 _legacy_port 遇到非数字 port 也能安全降级
    a = BotApiAdapter(platform_config, {}, asyncio.Queue())
    # 插件配置存在且有默认值，所以 _host/_port 来自插件配置
    assert a._host == "0.0.0.0"
    assert a._port == 9000


# ── Task 5：活跃平台注入 + 单实例 Quart 模块级锁 ──

@pytest.fixture()
def _reset_server_owner(monkeypatch):
    import astrbot_plugin_botapi.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "_server_owner", None)


def _active_adapter(monkeypatch):
    """构造仅含活跃平台注入所需属性的 adapter（避免 4.26 环境 _legacy_port 等路径噪音）。"""
    import astrbot_plugin_botapi.adapter as adapter_mod
    a = object.__new__(adapter_mod.BotApiAdapter)
    a._active_platforms = set()
    return a


def test_star_injects_active_platforms(monkeypatch):
    """Star.sync_active_platforms 把已启用平台集合注入 adapter._active_platforms。"""
    from types import SimpleNamespace
    from astrbot_plugin_botapi.main import BotApiStar
    from astrbot_plugin_botapi.runtime import runtime

    class FakeCM:
        pass

    def _reg(self, route, handler, methods, **kw):
        pass

    ctx = SimpleNamespace(conversation_manager=FakeCM(),
                          message_history_manager=SimpleNamespace(),
                          register_web_api=_reg)
    star = BotApiStar(ctx, {"host": "0.0.0.0", "port": 9001})
    rt = runtime()
    a = _active_adapter(monkeypatch)
    rt.adapter = a
    try:
        star.sync_active_platforms({"aiocqhttp_main", "telegram_x"})
        assert a._active_platforms == {"aiocqhttp_main", "telegram_x"}
    finally:
        rt.adapter = None


def test_sync_active_platforms_no_adapter_is_noop(monkeypatch):
    """star 初始化时 adapter 尚为 None（plugin_manager.reload 先于 platform_manager.initialize）→ 不抛异常。"""
    from types import SimpleNamespace
    from astrbot_plugin_botapi.main import BotApiStar
    from astrbot_plugin_botapi.runtime import runtime

    class FakeCM:
        pass

    def _reg(self, route, handler, methods, **kw):
        pass

    ctx = SimpleNamespace(conversation_manager=FakeCM(),
                          message_history_manager=SimpleNamespace(),
                          register_web_api=_reg)
    star = BotApiStar(ctx, {"host": "0.0.0.0", "port": 9001})
    rt = runtime()
    rt.adapter = None
    star.sync_active_platforms({"aiocqhttp_main"})   # 不应抛异常


def test_run_single_server_module_lock(tmp_path, monkeypatch):
    """两个 botapi platform 条目（两个 adapter 实例）都调 run() → 模块级锁只让第一个真正绑定端口；
    第二个返回永不完成的协程（不触发 app.run_task）。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter

    a1, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    a2, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))

    calls = []

    def fake_run_task(*, host, port, shutdown_trigger=None):
        calls.append((host, port))

        async def _never():
            await asyncio.Event().wait()

        return _never()

    a1.app.run_task = fake_run_task
    a2.app.run_task = fake_run_task

    coro1 = a1.run()
    coro2 = a2.run()
    # 第二个实例 entered 时 _SERVER_STARTED 已为 True → 直接走 _shutdown.wait()，不碰 app.run_task
    assert calls == [("0.0.0.0", 9000)]
    assert inspect.iscoroutine(coro1) and inspect.iscoroutine(coro2)
    assert not coro1.cr_running and not coro2.cr_running
    coro1.close()
    coro2.close()


def test_run_lock_after_terminate_allows_rebind(tmp_path, monkeypatch):
    """terminate() 复位 _SERVER_STARTED → 重启后 run() 可重新绑定（单实例锁可重入）。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter

    a, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    calls = []

    def fake_run_task(*, host, port, shutdown_trigger=None):
        calls.append((host, port))

        async def _never():
            await asyncio.Event().wait()

        return _never()

    a.app.run_task = fake_run_task

    coro = a.run()
    assert calls == [("0.0.0.0", 9000)]
    coro.close()

    # 模拟关闭：owner（a）terminate 复位全局锁 + 清 owner
    async def _terminate():
        await a.terminate()
    asyncio.run(_terminate())

    # 重启（新实例）：锁已复位 → 再次真实绑定
    a2, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    a2.app.run_task = fake_run_task
    coro2 = a2.run()
    assert calls == [("0.0.0.0", 9000), ("0.0.0.0", 9000)]
    coro2.close()


def test_run_owner_same_instance_rebind_allowed(tmp_path, monkeypatch):
    """_SERVER_STARTED=True 但拥有者仍是同一实例（重启旧协程驻留）→ 该实例 run()
    仍返回等待协程，不误判为他人持有锁而提前退出。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter

    a, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    calls = []

    def fake_run_task(*, host, port, shutdown_trigger=None):
        calls.append((host, port))

        async def _never():
            await asyncio.Event().wait()

        return _never()

    a.app.run_task = fake_run_task
    coro1 = a.run()
    assert calls == [("0.0.0.0", 9000)]
    coro1.close()
    # 同实例再次 run()：_SERVER_STARTED=True 且 _server_owner is self → 走 _shutdown.wait()，不重复绑定
    coro2 = a.run()
    assert calls == [("0.0.0.0", 9000)]   # app.run_task 未被再次调用
    coro2.close()


def test_terminate_non_owner_does_not_reset_server_started(tmp_path, monkeypatch,
                                                           _reset_server_owner):
    """非 owner 的 adapter 调 terminate() 不得复位 _SERVER_STARTED / _server_owner：
    owner 的服务器仍在驻留占端口，若锁被放行，owner 重启的新实例会重复绑定 →
    address already in use。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    import astrbot_plugin_botapi.adapter as adapter_mod

    a_owner, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    a_follower, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    calls = []

    def fake_run_task(*, host, port, shutdown_trigger=None):
        calls.append((host, port))

        async def _never():
            await asyncio.Event().wait()

        return _never()

    a_owner.app.run_task = fake_run_task
    a_follower.app.run_task = fake_run_task
    coro1 = a_owner.run()
    coro2 = a_follower.run()
    assert calls == [("0.0.0.0", 9000)]
    assert adapter_mod._server_owner is a_owner
    coro1.close()
    coro2.close()

    async def _terminate_non_owner():
        await a_follower.terminate()
    asyncio.run(_terminate_non_owner())
    # owner 的锁未被非 owner 复位
    assert adapter_mod._SERVER_STARTED is True
    assert adapter_mod._server_owner is a_owner

    # owner 重启（新实例）：锁仍持有 → 不重复绑定
    a_owner2, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    a_owner2.app.run_task = fake_run_task
    coro3 = a_owner2.run()
    assert calls == [("0.0.0.0", 9000)]   # app.run_task 未被再次调用
    coro3.close()


def test_terminate_owner_resets_server_started(tmp_path, monkeypatch, _reset_server_owner):
    """owner 调 terminate() → 复位 _SERVER_STARTED + 清 _server_owner，允许重启重新绑定。"""
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    import astrbot_plugin_botapi.adapter as adapter_mod

    a_owner, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    calls = []

    def fake_run_task(*, host, port, shutdown_trigger=None):
        calls.append((host, port))

        async def _never():
            await asyncio.Event().wait()

        return _never()

    a_owner.app.run_task = fake_run_task
    coro1 = a_owner.run()
    assert calls == [("0.0.0.0", 9000)]
    coro1.close()

    async def _terminate_owner():
        await a_owner.terminate()
    asyncio.run(_terminate_owner())
    assert adapter_mod._SERVER_STARTED is False
    assert adapter_mod._server_owner is None

    # 新实例可重新绑定端口
    a2, _ = _adapter(monkeypatch, _base_plugin_conf(str(tmp_path)))
    a2.app.run_task = fake_run_task
    coro2 = a2.run()
    assert calls == [("0.0.0.0", 9000), ("0.0.0.0", 9000)]
    coro2.close()
