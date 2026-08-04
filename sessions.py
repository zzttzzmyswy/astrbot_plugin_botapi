# sessions.py — 会话元数据纯逻辑 + 持久化（服务端权威源）
import time

from astrbot.core import astrbot_config, logger

from .runtime import runtime

DEFAULT_SESSION_ID = "default"
DEFAULT_SESSION_NAME = "默认会话"
MAX_SESSIONS = 25


def sessions_list(adapter, token: str) -> list:
    """某 token 的会话列表（含默认在最前）。只读派生，不改存储。"""
    raw = (adapter.config.get("sessions") or {}).get(token, [])
    lst = list(raw)
    if not lst or lst[0].get("id") != DEFAULT_SESSION_ID:
        lst = [{
            "id": DEFAULT_SESSION_ID,
            "name": DEFAULT_SESSION_NAME,
            "created_at": 0,
        }] + [x for x in lst if x.get("id") != DEFAULT_SESSION_ID]
    return lst


def save_sessions(adapter, token: str, sessions: list) -> None:
    """写会话列表：adapter.config + adapter.cfg + astrbot_config 平台子树 + save_config。"""
    all_s = dict(adapter.config.get("sessions") or {})
    all_s[token] = list(sessions)
    adapter.config["sessions"] = all_s
    try:
        adapter.cfg.sessions = all_s
    except Exception:
        pass
    for p in astrbot_config.get("platform", []):
        if p.get("id") == adapter.config.get("id"):
            p["sessions"] = all_s
            break
    try:
        astrbot_config.save_config()
    except Exception:
        logger.warning("[BotAPI] save_config 失败: 会话变更可能未持久化")


def umo_for(adapter, token: str, sid: str) -> str:
    base = f"{adapter.platform_id}:FriendMessage:{token}"
    if sid in ("", DEFAULT_SESSION_ID):
        return base
    return f"{base}:{sid}"


def scoped_key_for(adapter, token: str, sid: str) -> str:
    if sid in ("", DEFAULT_SESSION_ID):
        return token
    return f"{token}:{sid}"


def resolve_sid(adapter, token: str, sid_str) -> str:
    """解析请求里的 session_id：缺省/空/default → 'default'；校验属于该 token。"""
    sid = (sid_str or "").strip() or DEFAULT_SESSION_ID
    known = {x["id"] for x in sessions_list(adapter, token)}
    if sid not in known:
        raise LookupError(f"unknown session: {sid}")
    return sid


def sse_queues_for(adapter, token: str) -> list:
    """聚合某 token 全部会话的 SSE 队列（token 及 token:*）。"""
    out = []
    prefix = f"{token}:"
    for key, queues in list(getattr(adapter, "_sse_clients", {}).items()):
        if key == token or key.startswith(prefix):
            out.extend(queues)
    return out


async def delete_session(adapter, token: str, sid: str) -> None:
    """删除会话：删 conversation（含会话 umo）+ 断 SSE + 从存储移除。"""
    if sid == DEFAULT_SESSION_ID:
        raise LookupError(f"cannot delete default session: {sid}")
    scoped_umo = umo_for(adapter, token, sid)
    cm = runtime().conversation_manager
    if cm is not None:
        try:
            await cm.delete_conversations_by_user_id(scoped_umo)
        except Exception:
            pass
    scoped = scoped_key_for(adapter, token, sid)
    for q in list(getattr(adapter, "_sse_clients", {}).get(scoped, [])):
        adapter._put(q, None)
    remaining = [x for x in sessions_list(adapter, token)
                 if x["id"] != sid and x["id"] != DEFAULT_SESSION_ID]
    save_sessions(adapter, token, remaining)
