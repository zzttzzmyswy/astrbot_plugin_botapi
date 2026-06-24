# tests/test_admin_routing.py
from types import SimpleNamespace

from astrbot_plugin_botapi.main import BotApiStar


def test_all_admin_routes_are_get_or_post():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()
        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, methods))

    BotApiStar(FakeContext(), None)
    assert len(registered) >= 7
    for route, methods in registered:
        assert set(methods) <= {"GET", "POST"}, f"{route} 含非 GET/POST: {methods}"


def test_routes_prefixed_with_plugin_name():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()
        def register_web_api(self, route, handler, methods, desc):
            registered.append(route)

    BotApiStar(FakeContext(), None)
    for route in registered:
        assert route.startswith("/astrbot_plugin_botapi/"), f"{route} 缺插件名前缀"
