from datetime import datetime, timezone

from astrbot.core import logger

from .runtime import runtime
from .models import SSEEvent


def row_to_sse(row):
    c = row.content or {}
    kind = c.get("kind")
    # created_at 在 model 里按 datetime.now(timezone.utc) 存(TZ-aware UTC),但 SQLite
    # 存成 naive 字符串(丢了 +00:00),读回为 naive。若直接 .timestamp() 会按服务器
    # 本地时区解释 → 比真实 UTC 偏一个时区(北京服务器早 8h)。故 naive 时显式补 UTC。
    ca = row.created_at
    if ca.tzinfo is None:
        ca = ca.replace(tzinfo=timezone.utc)
    return {
        "message_id": str(row.id),
        "role": c.get("role", "assistant"),
        "type": {"final": "text", "thinking": "thinking", "tool_status": "tool_status"}.get(kind, "text"),
        "content": c.get("text", ""),
        "timestamp": int(ca.timestamp()),
    }


async def get_history(platform_id, token, since=None, before=None, limit=50):
    rt = runtime()
    rows = await rt.message_history_manager.get(
        platform_id=platform_id, user_id=token, page=1, page_size=200)  # 升序
    msgs = [row_to_sse(r) for r in rows]
    if since:
        msgs = [m for m in msgs if int(m["message_id"]) > int(since)]   # int 比较
    if before:
        msgs = [m for m in msgs if int(m["message_id"]) < int(before)]
    return msgs[-limit:], len(msgs) > limit


async def catchup_events(platform_id, token, since):
    rt = runtime()
    rows = await rt.message_history_manager.get(
        platform_id=platform_id, user_id=token, page=1, page_size=200)
    out = []
    for r in rows:
        if int(r.id) <= int(since):   # int 比较（防字典序 bug）
            continue
        c = r.content or {}
        etype = "thinking" if c.get("kind") == "thinking" else "message"
        out.append(SSEEvent(etype, row_to_sse(r)))
    return out


async def _insert(content, user_id, sender_id, sender_name):
    rt = runtime()
    if not (rt.message_history_manager and rt.adapter):
        logger.warning(
            "[BotAPI] _insert NO-OP: mgr=%r adapter=%r",
            rt.message_history_manager, rt.adapter,
        )
        return
    logger.info(
        "[BotAPI] _insert platform_id=%s user_id=%s kind=%s",
        rt.adapter.platform_id, user_id, (content or {}).get("kind"),
    )
    await rt.message_history_manager.insert(
        platform_id=rt.adapter.platform_id, user_id=user_id, content=content,
        sender_id=sender_id, sender_name=sender_name)


async def persist_inbound_text(token, message_id, text):
    if not text:
        return
    await _insert({"role": "user", "kind": "user", "text": text, "message_id": message_id},
                  token, token, "User")


async def persist_assistant_text(token, message_id, text, kind: str):  # kind: final/tool_status
    if not text:
        return
    await _insert({"role": "assistant", "kind": kind, "text": text, "message_id": message_id},
                  token, "bot", "BotAPI")


async def persist_assistant_thinking(token, message_id, text):
    if not text:
        return
    await _insert({"role": "assistant", "kind": "thinking", "text": text, "message_id": message_id},
                  token, "bot", "BotAPI")


# ── 导出（无上限：分页累加 + 按 id 升序）──

async def get_export_rows(platform_id, token, page_size=500):
    """取某账户全部历史行（row_to_sse 形态），分页累加直至某页 < page_size，
    按 row.id 升序排列。无条数上限。"""
    rt = runtime()
    mgr = rt.message_history_manager
    if not mgr:
        return []
    all_rows = []
    page = 1
    while True:
        rows = await mgr.get(platform_id=platform_id, user_id=token,
                             page=page, page_size=page_size)
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        page += 1
    all_rows.sort(key=lambda r: r.id)
    return [row_to_sse(r) for r in all_rows]


def _fmt_ts(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def to_markdown(rows: list, meta: dict) -> str:
    """把 row_to_sse 结果渲染为 Markdown。纯函数（不读时间，meta 由调用方注入）。"""
    nickname = meta.get("nickname") or ""
    token_preview = meta.get("token_preview") or ""
    exported_at = meta.get("exported_at", "")
    title = nickname or token_preview or "未知账户"

    lines = [f"# BotAPI 对话记录 — {title}", ""]
    if token_preview:
        lines.append(f"> 账户：{nickname or '（无昵称）'} (`{token_preview}`)")
    else:
        lines.append(f"> 账户：{nickname or '（无昵称）'}")
    lines.append(f"> 导出时间：{exported_at}")
    lines.append(f"> 消息数：{len(rows)}")
    lines += ["", "---", ""]

    for r in rows:
        ts = _fmt_ts(r.get("timestamp"))
        role = r.get("role")
        typ = r.get("type")
        content = r.get("content", "") or ""
        if role == "user":
            lines += [f"## 👤 用户  · {ts}", "", content]
        elif typ == "thinking":
            lines += [f"<details><summary>💭 思考  · {ts}</summary>", "", content, "", "</details>"]
        elif typ == "tool_status":
            quoted = content.replace("\n", "\n> ")
            lines += [f"> 🔨 工具状态  · {ts}", f"> {quoted}"]
        else:   # assistant text
            lines += [f"## 🤖 助手  · {ts}", "", content]
        lines += ["", "---", ""]

    return "\n".join(lines).rstrip() + "\n"
