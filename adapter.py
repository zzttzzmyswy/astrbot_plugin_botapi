# adapter.py
import asyncio
import json
import os
import threading
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


# 模块级单实例锁：一个 AstrBot 只允许一个 botapi 服务器。
# PlatformManager（manager.py:52）对每个 botapi platform 条目都调 inst.run()，
# 多个条目共享同一 host/port；锁保证只有第一个真正绑定端口，其余 run() 返回
# 永不完成的协程（等待 _shutdown），避免重复绑定端口冲突。
_server_lock = threading.Lock()
_SERVER_STARTED = False
_server_owner = None   # 当前拥有服务器（绑定端口）的 adapter 实例；仅其 terminate 可复位锁


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
        # 多 botapi platform 条目都会调 run()，但模块级单实例锁保证只有第一个
        # 真正绑定端口；后续条目返回永不完成的协程（_shutdown.wait()），
        # 由 PlatformManager 驻留，待 terminate() 一并结束。
        global _SERVER_STARTED, _server_owner
        with _server_lock:
            if _SERVER_STARTED:
                # 已起过：不重复绑定端口。
                # 若起锁者就是本实例（重启后旧协程尚在驻留），同样只等待自己的
                # _shutdown（不退出事件循环），避免在 _server_owner 仍是自己时
                # 误以为"锁被他人持有"而提前结束驻留协程。
                return self._shutdown.wait()
            _SERVER_STARTED = True
            _server_owner = self
        # 注意：_SERVER_STARTED 在 app.run_task 真正绑定端口成功前就已置 True；
        # 若初始绑定失败（如端口被占）锁会泄漏为 True，之后任何 botapi 都无法再
        # 起服务器。此为已接受的边界（绑定失败属配置错误，由平台 ERROR 状态暴露），
        # 不做复杂守卫。
        return self.app.run_task(host=self._host, port=self._port,
                                 shutdown_trigger=self._shutdown.wait)

    async def terminate(self) -> None:
        global _SERVER_STARTED, _server_owner
        with _server_lock:
            # 仅服务器拥有者复位锁；非拥有者（等待 _shutdown 的驻留条目）复位会
            # 导致拥有者重启后锁已放行，新实例再绑定同一端口 → address already in use。
            if _server_owner is self:
                _SERVER_STARTED = False
                _server_owner = None
        self._shutdown.set()
        for token, queues in list(self._sse_clients.items()):
            for q in queues:
                self._put(q, None)

    async def send_by_session(self, session, message_chain) -> None:
        await super().send_by_session(session, message_chain)
        # session.session_id 是 MessageSession 的第三段（裸 scoped key）：
        # 默认会话="{token}"，分会话="{token}:{sid}"（完整 umo 由 __str__ 拼前缀）。
        sess_id = session.session_id
        # 绑定平台 UMO：第三段带 botapi_ 前缀 → 反向解析回 botapi token
        if sess_id.startswith("botapi_"):
            rest = sess_id[len("botapi_"):]
            parts = rest.split(":")
            token = parts[0]
            sid = parts[1] if len(parts) > 1 else "default"
        else:
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
        """返回 token 绑定的 platform_id；未绑定或平台不活跃返回 None。

        活跃集合为空（重启后平台注入前，消息热路径 submit_inbound 会在此读到空集）
        时回退 astrbot_config 里 enable=True 的非 botapi 平台条目，避免绑定静默失效。
        """
        bindings = self.config.get("botapi_bindings") or {}
        pid = bindings.get(token)
        if not pid:
            return None
        active = getattr(self, "_active_platforms", None)
        if active is not None and active:
            if pid not in active:
                return None
            return pid
        # _active_platforms 为空（重启后平台注入前）：回退 astrbot_config enable=True 条目
        # 读模块级 astrbot_config（与 _legacy_port 同一引用），便于测试 monkeypatch。
        try:
            self_id = self.config.get("id")
            for p in astrbot_config.get("platform", []):
                if p.get("id") == self_id or p.get("type") == "botapi":
                    continue
                if p.get("enable") and p.get("id") == pid:
                    return pid
        except Exception:
            pass
        return None

    def bind_token(self, token: str, platform_id: str) -> None:
        bindings = dict(self.config.get("botapi_bindings") or {})
        bindings[token] = platform_id
        self.config["botapi_bindings"] = bindings

    def unbind_token(self, token: str) -> None:
        bindings = dict(self.config.get("botapi_bindings") or {})
        bindings.pop(token, None)
        self.config["botapi_bindings"] = bindings
