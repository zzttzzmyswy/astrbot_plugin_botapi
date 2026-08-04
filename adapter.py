# adapter.py
import asyncio
import json
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path

from astrbot.api.platform import (register_platform_adapter, Platform, PlatformMetadata,
    AstrBotMessage, MessageMember, MessageType)
from astrbot.api.event import MessageChain
from astrbot.core import astrbot_config
from astrbot.core.config.astrbot_config import AstrBotConfig

from .models import BotApiConfig, SSEEvent
from .serializer import MessageSerializer
from .runtime import runtime
from . import sessions as _sessions


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
            sessions=dict(platform_config.get("sessions", {})),
        )
        self.platform_id = self.meta().id
        self._token_to_origin: dict = {}
        self._sse_clients: dict = defaultdict(list)
        # token→platform 绑定表：存 adapter.config（经 astrbot_config 平台子树持久化，
        # 不经过插件配置 schema，避免 check_config_integrity 剔除任意键）。
        # 空键确保 config 里始终存在 botapi_bindings；绑定内容 Task 5 前从旧配置迁移。
        self.config.setdefault("botapi_bindings", {})
        # 活跃平台 id 集合（PlatformManager._inst_map），Task 5 注入真实值；
        # 绑定查询时若目标平台不在活跃集合则回退（未绑定处理）。
        self._active_platforms: set = set()
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

        # 单实例化：host/port 从插件配置文件读取（插件配置页可编辑），而非本平台配置。
        # 插件配置 schema 由 _conf_schema.json 声明；AstrBotConfig 会自动创建缺失的配置文件。
        # 回退链：插件配置 host/port → 旧平台配置（type=botapi 条目）→ 默认 0.0.0.0:9000。
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_config_path
            conf = AstrBotConfig(
                config_path=os.path.join(get_astrbot_config_path(),
                                         "astrbot_plugin_botapi_config.json"),
                schema=self._load_plugin_schema(),
            )
            legacy_host, legacy_port = self._legacy_port()
            self._host = conf.get("host") or legacy_host or "0.0.0.0"
            self._port = conf.get("port") or legacy_port or 9000
        except Exception:
            legacy_host, legacy_port = self._legacy_port()
            self._host = legacy_host or "0.0.0.0"
            self._port = legacy_port or 9000
        self._server_started = False

    def _load_plugin_schema(self):
        """读插件目录 _conf_schema.json 的插件配置 schema。"""
        schema_path = Path(__file__).parent / "_conf_schema.json"
        if schema_path.exists():
            return json.loads(schema_path.read_text(encoding="utf-8-sig"))
        return {}

    def _legacy_port(self):
        """迁移回退：读旧平台配置（astrbot_config["platform"] 里 type=botapi 条目）的 host/port；port 非数字时返回 None。"""
        for p in (astrbot_config.get("platform") or []):
            if p.get("type") == "botapi":
                port = p.get("port")
                try:
                    port = int(port) if port else None
                except (TypeError, ValueError):
                    port = None
                return p.get("host"), port
        return None, None

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
        # 单实例化：host/port 来自插件配置（插件配置页），而非平台配置。
        # 多 botapi platform 条目都会调 run()，但模块级单实例锁由 Task 5 保证只起一个服务器。
        return self.app.run_task(host=self._host, port=self._port,
                                 shutdown_trigger=self._shutdown.wait)

    async def terminate(self) -> None:
        self._shutdown.set()
        for token, queues in list(self._sse_clients.items()):
            for q in queues:
                self._put(q, None)

    async def send_by_session(self, session, message_chain) -> None:
        await super().send_by_session(session, message_chain)
        # session.session_id 是 MessageSession 的第三段（裸 scoped key）：
        # 默认会话="{token}"，分会话="{token}:{sid}"（完整 umo 由 __str__ 拼前缀）。
        sess_id = session.session_id
        parts = sess_id.split(":")
        token = parts[0] if len(parts) > 0 else sess_id
        sid = parts[1] if len(parts) > 1 else "default"
        mid = f"botapi_proactive_{uuid.uuid4().hex[:12]}"
        payload = await self._serializer.serialize_chain(message_chain, None)
        scoped = _sessions.scoped_key_for(self, token, sid)
        evt = SSEEvent("message", {**payload, "streaming": False, "final": True,
                                   "session_id": "" if sid == "default" else sid})
        await self._broadcast_to(scoped, evt)
        await self._push_media(message_chain, token, mid, sid)

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

    async def _push_media(self, chain, token: str, message_id: str, sid="default"):
        if chain is None:
            return
        scoped = _sessions.scoped_key_for(self, token, sid)
        queues = list(self._sse_clients.get(scoped, []))
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
                        "streaming": False, "final": False, "timestamp": int(time.time()),
                        "session_id": "" if sid == "default" else sid}
                self._put(q, SSEEvent("message", data))

    # ── token→platform 绑定（多机器人）──

    def binding_platform_for(self, token: str) -> str | None:
        """返回 token 绑定的 platform_id；未绑定或平台不活跃返回 None。"""
        bindings = self.config.get("botapi_bindings") or {}
        pid = bindings.get(token)
        if not pid:
            return None
        active = getattr(self, "_active_platforms", None)
        if active is not None and pid not in active:
            return None
        return pid

    def bind_token(self, token: str, platform_id: str) -> None:
        bindings = dict(self.config.get("botapi_bindings") or {})
        bindings[token] = platform_id
        self.config["botapi_bindings"] = bindings

    def unbind_token(self, token: str) -> None:
        bindings = dict(self.config.get("botapi_bindings") or {})
        bindings.pop(token, None)
        self.config["botapi_bindings"] = bindings
