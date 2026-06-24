# adapter.py
import asyncio
import time
import uuid
from collections import defaultdict
from pathlib import Path

from astrbot.api.platform import (register_platform_adapter, Platform, PlatformMetadata,
    AstrBotMessage, MessageMember, MessageType)
from astrbot.api.event import MessageChain
from astrbot.core import astrbot_config

from .models import BotApiConfig, SSEEvent
from .serializer import MessageSerializer
from .runtime import runtime


@register_platform_adapter(
    "botapi",
    "BotAPI 自定义移动端适配器 — 一人一 Bot 极简移动端接入，支持弱网断连恢复",
    default_config_tmpl={"host": "0.0.0.0", "port": 9000, "tokens": [], "nicknames": {}},
    config_metadata={
        "host":   {"description": "监听地址", "type": "string", "hint": "0.0.0.0"},
        "port":   {"description": "监听端口", "type": "int", "hint": "9000"},
        "tokens": {"description": "允许的 Token 列表（空则允许所有非空 token）",
                   "type": "list", "items": {"type": "string"}},
        "nicknames": {"description": "账户昵称/备注（{token: 昵称}，仅管理展示，不注入对话）",
                      "type": "object", "hint": "{}"},
    },
    adapter_display_name="BotAPI 移动端",
    support_streaming_message=True,
)
class BotApiAdapter(Platform):
    def __init__(self, platform_config: dict, platform_settings: dict, event_queue: asyncio.Queue) -> None:
        super().__init__(platform_config, event_queue)
        self.settings = platform_settings
        # platform_config 含 @register_platform_adapter 自动补的 type/enable/id（register.py:34-41），
        # BotApiConfig 只收 host/port/tokens，故按字段取值而非 **platform_config（否则 TypeError 'type'）。
        self.cfg = BotApiConfig(
            host=platform_config.get("host", "0.0.0.0"),
            port=int(platform_config.get("port", 9000)),
            tokens=list(platform_config.get("tokens", [])),
            nicknames=dict(platform_config.get("nicknames", {})),
        )
        self.platform_id = self.meta().id
        self._token_to_origin: dict = {}
        self._sse_clients: dict = defaultdict(list)
        self._disabled_tokens: set = set()
        self._last_active: dict = {}
        self._uploaded_files: dict = {}
        self._upload_dir = Path(astrbot_config.get("data_path", "./data")) / "botapi_uploads"
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._shutdown = asyncio.Event()
        self._media_enabled = bool(astrbot_config.get("callback_api_base"))
        self._serializer = MessageSerializer(_media_enabled=self._media_enabled)
        runtime().adapter = self
        from quart import Quart
        self.app = Quart(__name__)   # 用 __name__（真实模块）；Quart("astrbot_plugin_botapi") 会因命名空间包在 Flask get_root_path 处 RuntimeError
        from .routes import _setup_routes
        self._setup_routes = lambda: _setup_routes(self)
        self._setup_routes()

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="botapi",
            description="BotAPI 自定义移动端适配器",
            id=self.config.get("id", "botapi"),
            adapter_display_name="BotAPI 移动端",
            support_streaming_message=True,
            support_proactive_message=True,
        )

    def run(self):
        return self.app.run_task(host=self.cfg.host, port=self.cfg.port,
                                 shutdown_trigger=self._shutdown.wait)

    async def terminate(self) -> None:
        self._shutdown.set()
        for token, queues in list(self._sse_clients.items()):
            for q in queues:
                self._put(q, None)

    async def send_by_session(self, session, message_chain) -> None:
        await super().send_by_session(session, message_chain)
        token = session.session_id
        mid = f"botapi_proactive_{uuid.uuid4().hex[:12]}"
        payload = await self._serializer.serialize_chain(message_chain, None)
        await self._broadcast_to(token, SSEEvent("message", {**payload, "streaming": False, "final": True}))
        await self._push_media(message_chain, token, mid)

    # ── 非阻塞 SSE 投递（spec §4.2）──
    def _put(self, q: asyncio.Queue, evt):
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(evt)
            except Exception:
                pass

    async def _broadcast_to(self, token: str, evt: SSEEvent):
        for q in list(self._sse_clients.get(token, [])):
            self._put(q, evt)

    async def _push_media(self, chain, token: str, message_id: str):
        if chain is None:
            return
        queues = list(self._sse_clients.get(token, []))
        for comp in (chain.chain or []):
            ct = comp.type.value.lower() if hasattr(comp.type, "value") else str(comp.type).lower()
            if ct not in ("image", "record", "file"):
                continue
            mtype = {"image": "image", "record": "audio", "file": "file"}[ct]
            for q in queues:   # 每队列铸独立 token
                url = await self._serializer._media_url(comp)
                if not url:
                    continue
                data = {"message_id": message_id, "type": mtype,
                        "content": ({"name": getattr(comp, "name", "file"), "url": url}
                                    if mtype == "file" else url),
                        "streaming": False, "final": False, "timestamp": int(time.time())}
                self._put(q, SSEEvent("message", data))
