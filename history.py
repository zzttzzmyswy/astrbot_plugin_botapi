from .runtime import runtime
from .models import SSEEvent


def row_to_sse(row):
    c = row.content or {}
    kind = c.get("kind")
    return {
        "message_id": str(row.id),
        "role": c.get("role", "assistant"),
        "type": {"final": "text", "thinking": "thinking", "tool_status": "tool_status"}.get(kind, "text"),
        "content": c.get("text", ""),
        "timestamp": int(row.created_at.timestamp()),
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
