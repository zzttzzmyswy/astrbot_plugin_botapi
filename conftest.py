import sys
from pathlib import Path

import pytest

# 让 `import astrbot` 可用（AstrBot 源码）
_ASTRBOT = Path("/home/zzt/workspace/AstrBot")
if _ASTRBOT.is_dir() and str(_ASTRBOT) not in sys.path:
    sys.path.insert(0, str(_ASTRBOT))

# 让 `import astrbot_plugin_botapi` 可用（插件父目录）
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))


@pytest.fixture(autouse=True)
def _reset_runtime_singleton():
    """每个测试前重置全局 RuntimeState 单例，防跨测试污染
    （test_star/test_admin_handlers/test_admin_routing 注入 adapter/managers 到单例）。"""
    from astrbot_plugin_botapi.runtime import runtime
    rt = runtime()
    rt.adapter = None
    rt.conversation_manager = None
    rt.message_history_manager = None
    rt.context = None
    yield

