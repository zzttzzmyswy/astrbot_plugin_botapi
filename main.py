# main.py
import asyncio
import hashlib
import json
import uuid
from datetime import datetime

from astrbot.api.star import Star, Context
from quart import request

# AstrBot 4.26 把 Response 类（astrbot.dashboard.routes.route）改为
# astrbot.dashboard.responses 的模块级 ok()/error() 函数。这里做兼容 shim，
# 4.26+ 走新函数、4.25.x 回退旧类，调用点仍沿用 Response().ok(...).__dict__ 形式。
try:
    from astrbot.dashboard.responses import ok as _ok_fn, error as _error_fn
    _RESP_NEW = True
except ImportError:  # 4.25.x
    from astrbot.dashboard.routes.route import Response as _OldResponse
    _RESP_NEW = False


class _RespObj:
    """把 dict 包成对象，使其 __dict__ == 该 dict（模拟旧 Response 实例的 .__dict__）。"""
    def __init__(self, d):
        self.__dict__.update(d)


class Response:
    if _RESP_NEW:
        def ok(self, data=None, message=None):
            return _RespObj(_ok_fn(data, message))

        def error(self, message, data=None):
            return _RespObj(_error_fn(message, data))
    else:
        def ok(self, data=None, message=None):
            return _OldResponse().ok(data, message)

        def error(self, message, data=None):
            return _OldResponse().error(message)


from .adapter import BotApiAdapter  # noqa: F401  (Star 生命周期自管 adapter，Task 3 起用)
from .runtime import runtime
from . import routes as _routes  # noqa: F401  保证模块加载


class BotApiStar(Star):
    # 类级服务器 task：插件激活时由 AstrBot 以类级静态调用 Star.initialize() 拉起，
    # 禁用时 Star.terminate() 停服。放类级（非实例）是因为 initialize/terminate 是类方法。
    _server_task = None

    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        rt = runtime()
        rt.context = context
        rt.conversation_manager = context.conversation_manager
        rt.message_history_manager = context.message_history_manager
        # 插件配置（插件配置页可编辑 host/port），AstrBotConfig 是 dict 子类
        self._plugin_conf = config if isinstance(config, dict) else {}
        P = "astrbot_plugin_botapi"
        context.register_web_api(f"/{P}/stats", self._stats, ["GET"], "统计")
        context.register_web_api(f"/{P}/accounts", self._accounts, ["GET"], "账户列表")
        context.register_web_api(f"/{P}/accounts", self._create, ["POST"], "新增账户")
        context.register_web_api(
            f"/{P}/accounts/<token_hash>/delete", self._delete, ["POST"], "删除账户"
        )
        context.register_web_api(
            f"/{P}/accounts/<token_hash>/bind", self._bind, ["POST"], "绑定机器人"
        )
        context.register_web_api(
            f"/{P}/accounts/<token_hash>/unbind", self._unbind, ["POST"], "解绑机器人"
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
        context.register_web_api(
            f"/{P}/accounts/<token_hash>/export", self._export, ["POST"], "导出历史"
        )
        context.register_web_api(
            f"/{P}/sessions/<token_hash>/chat", self._chat, ["POST"], "会话对话"
        )
        context.register_web_api(
            f"/{P}/sessions/<token_hash>/history", self._history, ["POST"], "会话历史"
        )
        context.register_web_api(
            f"/{P}/sessions/<token_hash>", self._sessions_web, ["GET"], "会话列表"
        )
        context.register_web_api(
            f"/{P}/sessions/<token_hash>", self._create_session_web, ["POST"], "新建会话"
        )
        context.register_web_api(
            f"/{P}/sessions/<token_hash>/<sid>/rename",
            self._rename_session_web,
            ["POST"],
            "重命名会话",
        )
        context.register_web_api(
            f"/{P}/sessions/<token_hash>/<sid>/delete",
            self._delete_session_web,
            ["POST"],
            "删除会话",
        )
        context.register_web_api(
            f"/{P}/platforms", self._platforms, ["GET"], "平台列表"
        )

    # ── 生命周期：AstrBot 以类级静态调用 Star.initialize()/terminate()（纯插件自管）──

    @classmethod
    async def initialize(cls):
        """插件激活时由 AstrBot 调用（star_cls.initialize()）。自建 adapter + 自起服务器。"""
        if runtime().adapter is not None:
            return                          # 已起过（重载等）
        rt = runtime()
        from .plugin_conf import get_host, get_port
        from .adapter import BotApiAdapter
        adapter = BotApiAdapter(get_host() or "0.0.0.0", get_port() or 9000,
                                rt.context.get_event_queue())
        rt.adapter = adapter
        cls._server_task = asyncio.create_task(adapter.run())

    @classmethod
    async def terminate(cls):
        """插件禁用/重载时由 AstrBot 调用。停服务器 + 复位 runtime。"""
        adapter = runtime().adapter
        if adapter is not None:
            await adapter.shutdown()
        task = getattr(cls, "_server_task", None)
        if task is not None:
            task.cancel()
            try:
                await asyncio.gather(task, return_exceptions=True)
            except Exception:
                pass
            cls._server_task = None

    # ── helpers ──

    def sync_active_platforms(self, platform_ids):
        """注入活跃平台集合到 adapter（供 binding_platform_for 校验目标平台是否在运行）。

        启动时序：plugin_manager.reload() 先于 platform_manager.initialize()，
        故 Star 初始化时 adapter 尚为 None；此方法由外部在平台就绪后延迟调用，
        为空时静默跳过（不抛异常）。
        """
        adapter = runtime().adapter
        if adapter is not None:
            adapter._active_platforms = set(platform_ids)

    def _refresh_active_platforms(self):
        """从 PlatformManager 拉取活跃平台并注入 adapter._active_platforms。

        生产路径：binding_platform_for 依赖 adapter._active_platforms 判定绑定是否生效，
        而该集合在 adapter __init__ 为空、又没有调用方填充时，会导致每次绑定都静默回退
        （pid 不在空集 → 返回 None）。此方法在需要准确活跃平台的入口（platforms/stats/bind）
        惰性调用：platform_manager 未就绪或 adapter 未就绪时静默跳过。
        """
        adapter = runtime().adapter
        if adapter is None:
            return
        pm = getattr(self.context, "platform_manager", None)
        if pm is None:
            return
        insts = getattr(pm, "platform_insts", None) or []
        # 排除 botapi 自身（避免自我绑定）。adapter 已无平台 config，
        # botapi 平台 id 是常量 "botapi"。
        ids = set()
        for inst in insts:
            try:
                pid = inst.meta().id if hasattr(inst, "meta") else inst.config.get("id")
            except Exception:
                pid = None
            if pid and pid != "botapi":
                ids.add(pid)
        adapter._active_platforms = ids

    @staticmethod
    def _hash_tok(t):
        return hashlib.sha256(t.encode()).hexdigest()[:16]

    @staticmethod
    def _preview(t):
        return f"{t[:8]}...{t[-4:]}" if len(t) > 16 else t

    def _persist_tokens(self, adapter, new_tokens):
        """写插件配置 tokens + 更新运行时缓存 + 落盘。"""
        from .plugin_conf import set_tokens, save
        set_tokens(new_tokens)
        adapter.cfg.tokens = list(new_tokens)
        save()

    # ── _do_* helpers（纯逻辑，可直接测试）──

    async def _do_stats(self):
        self._refresh_active_platforms()
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        from . import sessions as _sessions

        per = []
        for token in adapter.cfg.tokens or []:
            platform_id, tok = _sessions.bound_conversation_umo(adapter, token,
                                                                _sessions.DEFAULT_SESSION_ID)
            umo = f"{platform_id}:FriendMessage:{tok}"
            msg_count = 0
            try:
                cid = await rt.conversation_manager.get_curr_conversation_id(umo)
                if cid:
                    conv = await rt.conversation_manager.get_conversation(umo, cid)
                    if conv and conv.history:
                        msg_count = len(json.loads(conv.history))
            except Exception:
                pass
            sse = _sessions.sse_queues_for(adapter, token)
            per.append({
                "token_preview": self._preview(token),
                "token_hash": self._hash_tok(token),
                "bound_platform": adapter.binding_platform_for(token),   # 仅有效绑定（平台活跃/启用）才显示
                "online": bool(sse),
                "sse_connections": len(sse),
                "message_count": msg_count,
                "last_active": adapter._last_active.get(token),
            })
        return Response().ok({
            "total_accounts": len(per),
            "total_online": sum(1 for a in per if a["online"]),
            "total_messages": sum(a["message_count"] for a in per),
            "per_account": per,
        }).__dict__

    async def _do_create(self, token=None):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        from .plugin_conf import get_tokens
        token = token or uuid.uuid4().hex[:16]
        toks = list(get_tokens())
        if token not in toks:
            toks.append(token)
            self._persist_tokens(adapter, toks)
        return Response().ok({"token": token, "message": "账户创建成功"}).__dict__

    async def _do_delete(self, token_hash):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        from .plugin_conf import get_tokens, get_sessions_map, set_sessions_map, save
        target = next(
            (t for t in get_tokens() if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        toks = [t for t in get_tokens() if t != target]
        self._persist_tokens(adapter, toks)
        adapter.unbind_token(target)          # 从所有平台 tokens 移除绑定
        all_s = get_sessions_map()
        all_s.pop(target, None)               # 清 sessions
        set_sessions_map(all_s)
        save()
        from . import sessions as _sessions

        for q in _sessions.sse_queues_for(adapter, target):
            adapter._put(q, None)
        # 逐个 scoped key 清理（token 及 token:*），保留旧默认行为
        for key in list(adapter._sse_clients):
            if key == target or key.startswith(f"{target}:"):
                adapter._sse_clients.pop(key, None)
        if hasattr(adapter, "_token_to_origin"):
            adapter._token_to_origin.pop(target, None)
        return Response().ok({"message": "账户已删除"}).__dict__

    async def _do_toggle(self, token_hash, disabled):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        if disabled:
            adapter._disabled_tokens.add(target)
            from . import sessions as _sessions

            for q in _sessions.sse_queues_for(adapter, target):
                adapter._put(q, None)
            for key in list(adapter._sse_clients):
                if key == target or key.startswith(f"{target}:"):
                    adapter._sse_clients.pop(key, None)
        else:
            adapter._disabled_tokens.discard(target)
        return Response().ok({"message": "状态已更新"}).__dict__

    async def _do_disconnect(self, token_hash):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到会话").__dict__
        from .models import SSEEvent
        from . import sessions as _sessions

        for q in _sessions.sse_queues_for(adapter, target):
            adapter._put(
                q,
                SSEEvent(
                    "error",
                    {"code": "SESSION_KICKED", "message": "管理员已断开此会话"},
                ),
            )
        for key in list(adapter._sse_clients):
            if key == target or key.startswith(f"{target}:"):
                adapter._sse_clients.pop(key, None)
        return Response().ok({"message": "会话已断开"}).__dict__

    async def _do_clear(self, token_hash, session_id=""):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到会话").__dict__
        from . import sessions as _sessions

        try:
            sid = _sessions.resolve_sid(adapter, target, session_id)
        except LookupError:
            return Response().error("未找到会话").__dict__
        platform_id, tok = _sessions.bound_conversation_umo(adapter, target, sid)
        await rt.conversation_manager.new_conversation(f"{platform_id}:FriendMessage:{tok}")
        return Response().ok({"message": "历史已清除"}).__dict__

    async def _do_export(self, token_hash, fmt):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        from .history import get_export_rows, to_markdown
        rows = await get_export_rows(adapter.platform_id, target)
        meta = {
            "token_preview": self._preview(target),
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(rows),
        }
        safe_title = meta["token_preview"] or target[:8]
        if fmt == "json":
            content = json.dumps(rows, ensure_ascii=False, indent=2)
            return Response().ok({
                "content": content,
                "filename": f"botapi-history-{safe_title}.json",
                "mime": "application/json",
            }).__dict__
        if fmt == "md":
            content = to_markdown(rows, meta)
            return Response().ok({
                "content": content,
                "filename": f"botapi-history-{safe_title}.md",
                "mime": "text/markdown",
            }).__dict__
        return Response().error("不支持的格式，可选 md 或 json").__dict__

    async def _do_chat(self, token_hash, text, session_id=""):
        """管理页直接对话：以该 token 身份注入同一会话（与手机端 /message 共享）。
        消息经 commit_event 进 LLM pipeline，更新 conversation_manager；回复轮询
        sessions/<hash>/history 取（读 conversation_manager）。session_id 指定
        分会话（默认会话为空）。"""
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        if not (text and text.strip()):
            return Response().error("消息不能为空").__dict__
        from .routes import submit_inbound

        try:
            message_id = await submit_inbound(adapter, target, text, session_id=session_id)
        except LookupError:
            return Response().error("未找到会话").__dict__
        return Response().ok({"message_id": message_id}).__dict__

    async def _do_history(self, token_hash, since=None, limit=50, session_id=""):
        """管理页拉某账户/会话历史。读 conversation_manager（LLM 真实对话上下文，
        稳定可用），不读 platform_message_history（部分 4.26 环境 insert 不落表）。
        前端按 (role,content) 去重，since 仅作兼容占位。session_id 指定分会话，
        缺省取默认会话。"""
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        from .history import get_conversation_messages
        from . import sessions as _sessions

        try:
            sid = _sessions.resolve_sid(adapter, target, session_id)
        except LookupError:
            return Response().error("未找到会话").__dict__
        limit = min(int(limit), 200) if limit else 50
        platform_id, tok = _sessions.bound_conversation_umo(adapter, target, sid)
        msgs = await get_conversation_messages(rt, platform_id, tok, limit)
        return Response().ok({"messages": msgs, "has_more": False}).__dict__

    async def _do_sessions(self, token_hash):
        """某账户（token_hash）的会话列表，默认会话在最前。"""
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        from . import sessions as _sessions

        return Response().ok({
            "sessions": _sessions.sessions_list(adapter, target),
            "default_id": _sessions.DEFAULT_SESSION_ID,
        }).__dict__

    async def _do_create_session(self, token_hash, name):
        """为某账户新建会话。"""
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        from . import sessions as _sessions
        import time as _time

        raw_name = (name or "").strip() if isinstance(name, str) else ""
        if not raw_name:
            return Response().error("会话名称不能为空").__dict__
        cur = _sessions.sessions_list(adapter, target)
        if len(cur) >= _sessions.MAX_SESSIONS:
            return Response().error("会话数量已达上限").__dict__
        new = {"id": uuid.uuid4().hex[:12], "name": raw_name,
               "created_at": int(_time.time())}
        cur.append(new)
        _sessions.save_sessions(adapter, target, cur)
        return Response().ok({"session": new}).__dict__

    async def _do_rename_session(self, token_hash, sid, name):
        """重命名某账户的某会话。"""
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        from . import sessions as _sessions

        raw_name = (name or "").strip() if isinstance(name, str) else ""
        if not raw_name:
            return Response().error("会话名称不能为空").__dict__
        cur = _sessions.sessions_list(adapter, target)
        for x in cur:
            if x["id"] == sid:
                x["name"] = raw_name
                _sessions.save_sessions(adapter, target, cur)
                return Response().ok({"message": "会话已重命名"}).__dict__
        return Response().error("未找到会话").__dict__

    async def _do_delete_session(self, token_hash, sid):
        """删除某账户的某会话（默认会话不可删）。"""
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in (adapter.cfg.tokens or []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        from . import sessions as _sessions

        if sid == _sessions.DEFAULT_SESSION_ID:
            return Response().error("默认会话不可删除").__dict__
        if not any(x["id"] == sid for x in _sessions.sessions_list(adapter, target)):
            return Response().error("未找到会话").__dict__
        await _sessions.delete_session(adapter, target, sid)
        return Response().ok({"message": "会话已删除"}).__dict__

    async def _do_platforms(self):
        """返回当前可绑定的活跃平台 id 列表（绑定下拉用）。"""
        self._refresh_active_platforms()
        rt = runtime()
        adapter = rt.adapter
        self_id = adapter.platform_id if adapter is not None else None
        platforms = []
        if adapter is not None:
            active = getattr(adapter, "_active_platforms", None)
            if active:
                platforms = [p for p in sorted(active) if p != self_id]
        return Response().ok({"platforms": platforms}).__dict__

    async def _do_bind(self, token_hash, platform_id):
        """绑定 token → 目标平台（多机器人路由）。校验目标在活跃集合。"""
        self._refresh_active_platforms()
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        from .plugin_conf import get_tokens
        target = next(
            (t for t in get_tokens() if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        if not platform_id:
            return Response().error("platform_id 不能为空").__dict__
        active = getattr(adapter, "_active_platforms", None)
        if active and platform_id not in active:
            return Response().error("目标平台不在活跃集合").__dict__
        adapter.bind_token(target, platform_id)
        return Response().ok({"message": "绑定成功"}).__dict__

    async def _do_unbind(self, token_hash):
        """解绑 token（恢复默认单机路由）。"""
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        from .plugin_conf import get_tokens
        target = next(
            (t for t in get_tokens() if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        adapter.unbind_token(target)
        return Response().ok({"message": "已解绑"}).__dict__

    # ── register_web_api handlers（薄封装：取参→调 _do_*）──

    async def _stats(self):
        return await self._do_stats()

    async def _accounts(self):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        from . import sessions as _sessions

        accs = [
            {
                "token_preview": self._preview(t),
                "token_hash": self._hash_tok(t),
                "enabled": t not in adapter._disabled_tokens,
                "bound_platform": adapter.binding_platform_for(t),
                "online": bool(_sessions.sse_queues_for(adapter, t)),
                "sse_connections": len(_sessions.sse_queues_for(adapter, t)),
                "last_active": adapter._last_active.get(t),
            }
            for t in (adapter.cfg.tokens or [])
        ]
        return Response().ok({"accounts": accs, "total": len(accs)}).__dict__

    async def _create(self):
        data = await request.get_json()
        token = (data or {}).get("token")
        return await self._do_create(token)

    async def _delete(self, token_hash):
        return await self._do_delete(token_hash)

    async def _toggle(self, token_hash):
        data = await request.get_json()
        return await self._do_toggle(token_hash, disabled=bool((data or {}).get("disabled")))

    async def _disconnect(self, token_hash):
        return await self._do_disconnect(token_hash)

    async def _clear(self, token_hash):
        data = await request.get_json()
        session_id = (data or {}).get("session_id", "")
        return await self._do_clear(token_hash, session_id)

    async def _export(self, token_hash):
        data = await request.get_json()
        fmt = (data or {}).get("format", "md")
        return await self._do_export(token_hash, fmt)

    async def _chat(self, token_hash):
        data = await request.get_json()
        text = (data or {}).get("text", "")
        session_id = (data or {}).get("session_id", "")
        return await self._do_chat(token_hash, text, session_id)

    async def _history(self, token_hash):
        # 用 POST+body（与 export/chat 同构），避开 bridge apiGet 的 query/params 路径
        # （sandbox iframe null-origin 下 apiGet+query 会让父外壳 postMessage 失败）。
        data = await request.get_json()
        since = (data or {}).get("since")
        limit = (data or {}).get("limit", 50)
        session_id = (data or {}).get("session_id", "")
        return await self._do_history(token_hash, since, limit, session_id)

    async def _sessions_web(self, token_hash):
        return await self._do_sessions(token_hash)

    async def _create_session_web(self, token_hash):
        data = await request.get_json()
        name = (data or {}).get("name", "")
        return await self._do_create_session(token_hash, name)

    async def _rename_session_web(self, token_hash, sid):
        data = await request.get_json()
        name = (data or {}).get("name", "")
        return await self._do_rename_session(token_hash, sid, name)

    async def _delete_session_web(self, token_hash, sid):
        return await self._do_delete_session(token_hash, sid)

    async def _platforms(self):
        return await self._do_platforms()

    async def _bind(self, token_hash):
        data = await request.get_json()
        platform_id = (data or {}).get("platform_id", "")
        return await self._do_bind(token_hash, platform_id)

    async def _unbind(self, token_hash):
        return await self._do_unbind(token_hash)
