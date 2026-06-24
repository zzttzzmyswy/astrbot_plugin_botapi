from astrbot_plugin_botapi.runtime import runtime, RuntimeState


def test_runtime_singleton():
    rt1 = runtime()
    rt2 = runtime()
    assert rt1 is rt2


def test_runtime_initial_state_none():
    rt = runtime()
    assert rt.adapter is None
    assert rt.conversation_manager is None
    assert rt.message_history_manager is None


def test_runtime_set_get():
    rt = runtime()
    rt.adapter = "fake_adapter"
    assert runtime().adapter == "fake_adapter"
    rt.adapter = None  # 清理
