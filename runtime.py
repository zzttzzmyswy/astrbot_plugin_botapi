class RuntimeState:
    adapter: "object | None" = None
    conversation_manager: "object | None" = None
    message_history_manager: "object | None" = None
    context: "object | None" = None


_runtime = RuntimeState()


def runtime() -> RuntimeState:
    return _runtime
