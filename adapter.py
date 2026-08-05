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
    default_config_tmpl={"tokens": []},
    config_metadata={
        "tokens": {"description": "绑定 token 列表（账户注册表，空=拒连）",
                   "type": "list", "items": {"type": "string"}},
    },
    adapter_display_name="BotAPI 移动端",
    support_streaming_message=True,
)
class BotApiAdapter(Platform):
    def __init__(self, platform_config: dict, platform_settings: dict, event_queue: asyncio.Queue) -> None:
        super().__init__(platform_config, event_queue)
        self.settings = platform_settings
        # platform_config 含 @register_platform_adapter 自动补的 type/enable/id（register.py:34-41），
        # BotApiConfig 只收 tokens/sessions，故按字段取值而非 **platform_config（否则 TypeError 'type'）。
        self.cfg = BotApiConfig(
            tokens=list(platform_config.get("tokens", [])),
            sessions=dict(platform_config.get("sessions", {})),
        )
        self.platform_id = self.meta().id
        self._token_to_origin: dict = {}
        self._sse_clients: dict = defaultdict(list)
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

        # 插件配置单例：host/port + 账户数据（tokens/bindings/sessions）全局唯一。
        from .plugin_conf import load_plugin_conf, get_tokens, get_host, get_port
        self._conf = load_plugin_conf()
        legacy_host, legacy_port = self._legacy_port()
        self._host = get_host() or legacy_host or "0.0.0.0"
        self._port = get_port() or legacy_port or 9000
        self.cfg.tokens = get_tokens()   # 运行时缓存（auth 用）
        self._migrate_accounts()
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

    def _migrate_accounts(self):
        """把平台条目残留的账户数据收敛进插件配置（全局）。

        1. 插件 tokens 为空且 botapi 平台条目 tokens 非空 → 迁到插件（并剥离条目 tokens）。
        2. 非 botapi 平台条目 tokens 及遗留 botapi_bindings dict → 生成 bindings 条目。
        3. 清理 botapi 平台条目的 botapi_bindings/nicknames/host/port/sessions。
        幂等：插件 tokens 非空即视为已迁移，跳过 1（2/3 仍执行，无键即 no-op）；
        已迁移的 botapi 条目 tokens 保留不动（只经 1 剥离一次）。
        """
        from .plugin_conf import get_tokens, set_tokens, get_bindings, set_bindings, save
        try:
            platforms = astrbot_config.get("platform")
            if not isinstance(platforms, list):
                return
            changed = False
            plugin_tokens = get_tokens()
            if not plugin_tokens:
                # 1. botapi 平台条目 tokens → 插件 tokens
                for p in platforms:
                    if p.get("id") == self.config.get("id"):
                        pt = p.get("tokens") or []
                        if pt:
                            set_tokens(list(pt))
                            p.pop("tokens", None)
                            changed = True
                        break
            # 2. 非 botapi 平台条目 tokens / 遗留 botapi_bindings → bindings
            binds = list(get_bindings())
            existing = {b.get("token") for b in binds}
            # 遗留 botapi_bindings dict（self.config 或 botapi 平台条目）→ {token: platform_id}
            legacy_binds = dict(self.config.get("botapi_bindings") or {})
            for p in platforms:
                if p.get("id") == self.config.get("id"):
                    legacy_binds.update(dict(p.get("botapi_bindings") or {}))
                    break
            for p in platforms:
                if p.get("type") == "botapi" or p.get("id") == self.config.get("id"):
                    continue
                pt = list(p.get("tokens") or [])
                pt += [tok for tok, pid in legacy_binds.items()
                       if pid == p.get("id") and tok not in pt]
                if pt:
                    for tok in pt:
                        if tok not in existing:
                            binds.append({"token": tok, "platform_id": p.get("id")})
                            existing.add(tok)
                    p.pop("tokens", None)
                    changed = True
            if binds:
                set_bindings(binds)
            # 3. 清理 botapi 平台条目旧键（tokens 仅经 1 剥离，幂等场景保留）
            for p in platforms:
                if p.get("id") == self.config.get("id"):
                    for key in ("botapi_bindings", "nicknames", "host", "port", "sessions"):
                        if key in p:
                            p.pop(key, None)
                            changed = True
                    break
            for key in ("botapi_bindings", "nicknames", "host", "port", "sessions"):
                if key in self.config:
                    self.config.pop(key, None)
                    changed = True
            if changed:
                save()
        except Exception:
            pass

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
    # 绑定存插件配置 bindings 列表（[{token, platform_id}]，一对一）。全局数据不存
    # 平台条目（update_bot 会整体覆盖导致丢失）。

    def binding_platform_for(self, token: str) -> str | None:
        """返回 token 绑定的 platform_id；未绑定或平台不活跃返回 None。"""
        from .plugin_conf import get_bindings
        pid = None
        for item in get_bindings():
            if item.get("token") == token and item.get("platform_id"):
                pid = item["platform_id"]
                break
        if not pid:
            return None
        active = getattr(self, "_active_platforms", None)
        if active is not None and active:
            return pid if pid in active else None
        try:
            for p in astrbot_config.get("platform", []):
                if p.get("id") == pid:
                    return pid if p.get("enable") else None
        except Exception:
            pass
        return None

    def unbind_token(self, token: str) -> None:
        """从插件配置 bindings 移除该 token 条目；有变更才落盘。"""
        from .plugin_conf import get_bindings, set_bindings
        binds = [b for b in get_bindings() if b.get("token") != token]
        if len(binds) != len(get_bindings()):
            set_bindings(binds)
            self._save_plugin_conf()

    def bind_token(self, token: str, platform_id: str) -> None:
        """绑定 token → 目标平台：先移除旧条目（一对一），再追加。botapi 类型目标忽略。"""
        from .plugin_conf import get_bindings, set_bindings
        if platform_id == self.config.get("id"):
            return
        for p in (astrbot_config.get("platform") or []):
            if p.get("id") == platform_id and p.get("type") == "botapi":
                return
        binds = [b for b in get_bindings() if b.get("token") != token]
        binds.append({"token": token, "platform_id": platform_id})
        set_bindings(binds)
        self._save_plugin_conf()

    def _save_plugin_conf(self):
        """落盘插件配置单例（绑定/账户变更后调用）。"""
        from .plugin_conf import save
        save()
