import sys
from pathlib import Path

# 让 `import astrbot` 可用（AstrBot 源码）
_ASTRBOT = Path("/home/zzt/workspace/AstrBot")
if _ASTRBOT.is_dir() and str(_ASTRBOT) not in sys.path:
    sys.path.insert(0, str(_ASTRBOT))

# 让 `import astrbot_plugin_botapi` 可用（插件父目录）
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))
