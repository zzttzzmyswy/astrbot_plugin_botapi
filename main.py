# main.py
import hashlib
import json
import uuid
from datetime import datetime

from astrbot.api.star import Star, Context
from astrbot.core import astrbot_config as _cfg_singleton
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
            f"/{P}/accounts/<token_hash>/nickname", self._set_nickname, ["POST"], "设置昵称"
        )
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
        context.register_web_api(
            f"/{P}/accounts/<token_hash>/export", self._export, ["POST"], "导出历史"
        )
        context.register_web_api(
            f"/{P}/sessions/<token_hash>/chat", self._chat, ["POST"], "会话对话"
        )
        context.register_web_api(
            f"/{P}/sessions/<token_hash>/history", self._history, ["POST"], "会话历史"
        )

    # ── helpers ──

    @staticmethod
    def _hash_tok(t):
        return hashlib.sha256(t.encode()).hexdigest()[:16]

    @staticmethod
    def _preview(t):
        return f"{t[:8]}...{t[-4:]}" if len(t) > 16 else t

    def _persist_account_state(self, adapter, new_tokens, new_nicknames):
        """改全局 astrbot_config 子树（tokens + nicknames）+ 同步运行时副本 + 落盘。"""
        for p in _cfg_singleton.get("platform", []):
            if p.get("id") == adapter.config.get("id"):
                p["tokens"] = list(new_tokens)
                p["nicknames"] = dict(new_nicknames)
                break
        adapter.config["tokens"] = list(new_tokens)
        adapter.config["nicknames"] = dict(new_nicknames)
        adapter.cfg.tokens = list(new_tokens)
        adapter.cfg.nicknames = dict(new_nicknames)
        _cfg_singleton.save_config()

    # ── _do_* helpers（纯逻辑，可直接测试）──

    async def _do_stats(self):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        pid = adapter.platform_id
        per = []
        for token in adapter.cfg.tokens or []:
            umo = f"{pid}:FriendMessage:{token}"
            msg_count = 0
            try:
                cid = await rt.conversation_manager.get_curr_conversation_id(umo)
                if cid:
                    conv = await rt.conversation_manager.get_conversation(umo, cid)
                    if conv and conv.history:
                        msg_count = len(json.loads(conv.history))
            except Exception:
                pass
            per.append({
                "token_preview": self._preview(token),
                "token_hash": self._hash_tok(token),
                "nickname": adapter.cfg.nicknames.get(token, ""),
                "online": bool(adapter._sse_clients.get(token)),
                "sse_connections": len(adapter._sse_clients.get(token, [])),
                "message_count": msg_count,
                "last_active": adapter._last_active.get(token),
            })
        return Response().ok({
            "total_accounts": len(per),
            "total_online": sum(1 for a in per if a["online"]),
            "total_messages": sum(a["message_count"] for a in per),
            "per_account": per,
        }).__dict__

    async def _do_create(self, token=None, nickname=""):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        token = token or uuid.uuid4().hex[:16]
        toks = list(adapter.config.get("tokens", []))
        nicks = dict(adapter.config.get("nicknames", {}))
        changed = False
        if token not in toks:
            toks.append(token)
            changed = True
        if nickname:
            nicks[token] = nickname
            changed = True
        if changed:
            self._persist_account_state(adapter, toks, nicks)
        return Response().ok({"token": token, "message": "账户创建成功"}).__dict__

    async def _do_delete(self, token_hash):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next(
            (t for t in adapter.config.get("tokens", []) if self._hash_tok(t) == token_hash),
            None,
        )
        if not target:
            return Response().error("未找到账户").__dict__
        toks = [t for t in adapter.config.get("tokens", []) if t != target]
        nicks = {k: v for k, v in adapter.config.get("nicknames", {}).items() if k != target}
        self._persist_account_state(adapter, toks, nicks)
        for q in adapter._sse_clients.pop(target, []):
            adapter._put(q, None)
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
            for q in adapter._sse_clients.pop(target, []):
                adapter._put(q, None)
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

        for q in adapter._sse_clients.pop(target, []):
            adapter._put(
                q,
                SSEEvent(
                    "error",
                    {"code": "SESSION_KICKED", "message": "管理员已断开此会话"},
                ),
            )
        return Response().ok({"message": "会话已断开"}).__dict__

    async def _do_clear(self, token_hash):
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
        umo = f"{adapter.platform_id}:FriendMessage:{target}"
        await rt.conversation_manager.new_conversation(umo)
        return Response().ok({"message": "历史已清除"}).__dict__

    async def _do_set_nickname(self, token_hash, nickname):
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
        nicks = dict(adapter.config.get("nicknames", {}))
        if nickname:
            nicks[target] = nickname
        else:
            nicks.pop(target, None)   # 空昵称=清除
        self._persist_account_state(adapter, list(adapter.config.get("tokens", [])), nicks)
        return Response().ok({"message": "昵称已更新"}).__dict__

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
            "nickname": adapter.cfg.nicknames.get(target, ""),
            "token_preview": self._preview(target),
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(rows),
        }
        safe_title = meta["nickname"] or meta["token_preview"] or target[:8]
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

    async def _do_chat(self, token_hash, text):
        """管理页直接对话：以该 token 身份注入同一会话（与手机端 /message 共享）。
        回复经轮询 sessions/<hash>/history 获取，不碰 SSE。"""
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
        from .history import get_history as _gh

        message_id = await submit_inbound(adapter, target, text)
        _rows, _ = await _gh(adapter.platform_id, target, None, 200)

        # 诊断（v1.2.6）：绕过 submit_inbound，直接调 manager.insert 插一行测试数据，
        # 看能不能落表 / 有没有异常。隔离 persist 链路 vs DB 层。
        direct_err = None
        try:
            await rt.message_history_manager.insert(
                platform_id=adapter.platform_id, user_id=target,
                content={"role": "user", "kind": "user", "text": "DIAG", "message_id": "diag"},
                sender_id=target, sender_name="AdminDiag")
        except Exception as e:
            direct_err = f"{type(e).__name__}: {e}"
        _rows2, _ = await _gh(adapter.platform_id, target, None, 200)

        return Response().ok({
            "message_id": message_id,
            "_diag": {
                "platform_id": adapter.platform_id,
                "target": target,
                "mgr_set": bool(rt.message_history_manager),
                "rows_after_send": len(_rows),
                "rows_after_direct": len(_rows2),
                "direct_err": direct_err,
            },
        }).__dict__

    async def _do_history(self, token_hash, since=None, limit=50):
        """管理页拉某账户会话历史（轮询用 since=最大行 id 取增量）。复用 history.get_history。"""
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
        from .history import get_history

        limit = min(int(limit), 200) if limit else 50
        msgs, has_more = await get_history(adapter.platform_id, target, since, limit)
        return Response().ok({
            "messages": msgs, "has_more": has_more,
            "_diag": {
                "platform_id": adapter.platform_id,
                "target": target,
                "mgr_set": bool(rt.message_history_manager),
                "since": since, "limit": limit,
            },
        }).__dict__

    # ── register_web_api handlers（薄封装：取参→调 _do_*）──

    async def _stats(self):
        return await self._do_stats()

    async def _accounts(self):
        rt = runtime()
        adapter = rt.adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        accs = [
            {
                "token_preview": self._preview(t),
                "token_hash": self._hash_tok(t),
                "nickname": adapter.cfg.nicknames.get(t, ""),
                "enabled": t not in adapter._disabled_tokens,
                "online": bool(adapter._sse_clients.get(t)),
                "sse_connections": len(adapter._sse_clients.get(t, [])),
                "last_active": adapter._last_active.get(t),
            }
            for t in (adapter.cfg.tokens or [])
        ]
        return Response().ok({"accounts": accs, "total": len(accs)}).__dict__

    async def _create(self):
        data = await request.get_json()
        token = (data or {}).get("token")
        nickname = (data or {}).get("nickname", "")
        return await self._do_create(token, nickname)

    async def _set_nickname(self, token_hash):
        data = await request.get_json()
        nickname = (data or {}).get("nickname", "")
        return await self._do_set_nickname(token_hash, nickname)

    async def _delete(self, token_hash):
        return await self._do_delete(token_hash)

    async def _toggle(self, token_hash):
        data = await request.get_json()
        return await self._do_toggle(token_hash, disabled=bool((data or {}).get("disabled")))

    async def _disconnect(self, token_hash):
        return await self._do_disconnect(token_hash)

    async def _clear(self, token_hash):
        return await self._do_clear(token_hash)

    async def _export(self, token_hash):
        data = await request.get_json()
        fmt = (data or {}).get("format", "md")
        return await self._do_export(token_hash, fmt)

    async def _chat(self, token_hash):
        data = await request.get_json()
        text = (data or {}).get("text", "")
        return await self._do_chat(token_hash, text)

    async def _history(self, token_hash):
        # 用 POST+body（与 export/chat 同构），避开 bridge apiGet 的 query/params 路径
        # （sandbox iframe null-origin 下 apiGet+query 会让父外壳 postMessage 失败）。
        data = await request.get_json()
        since = (data or {}).get("since")
        limit = (data or {}).get("limit", 50)
        return await self._do_history(token_hash, since, limit)
