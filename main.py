# main.py
from astrbot.api.star import Star, Context

from .adapter import BotApiAdapter  # 触发 @register_platform_adapter 注册到 platform_cls_map
from .runtime import runtime
from . import routes as _routes  # noqa: F401  保证模块加载


class BotApiStar(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        rt = runtime()
        rt.context = context
        rt.conversation_manager = context.conversation_manager
        rt.message_history_manager = context.message_history_manager
        P = "astrbot_plugin_botapi"
        context.register_web_api(f"/{P}/stats", self._stats, ["GET"], "统计")
        context.register_web_api(f"/{P}/accounts", self._accounts, ["GET"], "账户列表")
        context.register_web_api(f"/{P}/accounts", self._create, ["POST"], "新增账户")
        context.register_web_api(
            f"/{P}/accounts/<token_hash>/delete", self._delete, ["POST"], "删除账户"
        )
        context.register_web_api(
            f"/{P}/accounts/<token_hash>/status", self._toggle, ["POST"], "启停账户"
        )
        context.register_web_api(
            f"/{P}/sessions/<token_hash>/disconnect",
            self._disconnect,
            ["POST"],
            "断开会话",
        )
        context.register_web_api(
            f"/{P}/sessions/<token_hash>/clear",
            self._clear,
            ["POST"],
            "清空历史",
        )

    # ── stub handlers (Task 15 替换为真实实现) ──

    async def _stats(self):
        raise NotImplementedError("Task 15")

    async def _accounts(self):
        raise NotImplementedError("Task 15")

    async def _create(self):
        raise NotImplementedError("Task 15")

    async def _delete(self, token_hash):
        raise NotImplementedError("Task 15")

    async def _toggle(self, token_hash):
        raise NotImplementedError("Task 15")

    async def _disconnect(self, token_hash):
        raise NotImplementedError("Task 15")

    async def _clear(self, token_hash):
        raise NotImplementedError("Task 15")
