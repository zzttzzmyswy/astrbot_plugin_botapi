# tests/test_star.py
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar


def test_star_registers_web_apis_and_injects_runtime():
    registered = []

    class FakeContext:
        conversation_manager = "CM"
        message_history_manager = "PMH"

        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, tuple(methods), desc))

    # BotApiStar.__init__(context, config=None)
    star = BotApiStar(FakeContext(), None)
    from astrbot_plugin_botapi.runtime import runtime

    rt = runtime()
    assert rt.conversation_manager == "CM"
    assert rt.message_history_manager == "PMH"
    # 验证注册的路由
    routes = {r for r, _, _ in registered}
    assert "/astrbot_plugin_botapi/stats" in routes
    assert "/astrbot_plugin_botapi/accounts" in routes
    assert "/astrbot_plugin_botapi/accounts/<token_hash>/delete" in routes
    assert "/astrbot_plugin_botapi/sessions/<token_hash>/disconnect" in routes
    # 所有方法都是 GET 或 POST，无 DELETE/PATCH
    for _, methods, _ in registered:
        assert set(methods) <= {"GET", "POST"}
    # 清理 runtime
    rt.conversation_manager = None
    rt.message_history_manager = None
