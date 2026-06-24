# routes.py
import asyncio
import time
import uuid

from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
from astrbot.api.message_components import Image, Record, File, Plain
from quart import jsonify, request

from .event import BotApiMessageEvent
from .history import persist_inbound_text, catchup_events
from .models import SSEEvent


def _setup_routes(adapter):
    app = adapter.app

    @app.before_request
    async def _check_auth():
        if request.endpoint == "auth":
            return
        token = _extract_token(adapter)
        if not _is_valid_token(adapter, token) or token in adapter._disabled_tokens:
            return jsonify({"error": "unauthorized", "code": "INVALID_TOKEN"}), 401
        adapter._last_active[token] = time.time()

    @app.post("/api/v1/botapi/auth")
    async def auth():
        data = await request.get_json()
        token = (data or {}).get("token", "")
        if not _is_valid_token(adapter, token) or token in adapter._disabled_tokens:
            return jsonify({"error": "invalid_token"}), 401
        origin = _get_or_create_origin(adapter, token)
        return jsonify({"user_id": token, "session_id": origin})

    @app.post("/api/v1/botapi/message")
    async def send_message():
        token = _extract_token(adapter)
        data = await request.get_json()
        text = (data or {}).get("text", "")
        file_ids = (data or {}).get("file_ids", [])

        origin = _get_or_create_origin(adapter, token)
        msg = AstrBotMessage()
        msg.type = MessageType.FRIEND_MESSAGE
        msg.self_id = adapter.client_self_id
        msg.session_id = token   # 只传 token
        msg.message_id = f"botapi_{uuid.uuid4().hex[:12]}"
        msg.sender = MessageMember(user_id=token, nickname="User")
        msg.timestamp = int(time.time())
        components = []
        if text:
            components.append(Plain(text))
        for fid in file_ids:
            info = adapter._uploaded_files.get(fid)
            if info:
                components.append(_file_info_to_component(info))
        msg.message = components
        msg.message_str = text or "[消息]"
        msg.raw_message = data

        event = BotApiMessageEvent(message_str=msg.message_str, message_obj=msg,
                                   platform_meta=adapter.meta(), session_id=token, adapter=adapter)
        event.set_extra("enable_streaming", True)
        await persist_inbound_text(token, msg.message_id, text)
        adapter.commit_event(event)
        return jsonify({"message_id": msg.message_id})

    @app.post("/api/v1/botapi/upload")
    async def upload_file():
        files = await request.files
        file = files.get("file")
        if not file:
            return jsonify({"error": "no_file"}), 400
        from werkzeug.utils import secure_filename
        filename = secure_filename(file.filename or "untitled")
        file_id = f"f_{uuid.uuid4().hex[:10]}"
        save_path = adapter._upload_dir / f"{file_id}_{filename}"
        await file.save(save_path)
        info = {"file_id": file_id, "name": filename,
                "mime_type": file.content_type or "application/octet-stream",
                "size": save_path.stat().st_size}
        adapter._uploaded_files[file_id] = {**info, "path": str(save_path)}
        return jsonify(info)

    @app.get("/api/v1/botapi/stream")
    async def stream():
        from quart import make_response
        token = _extract_token(adapter)
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        adapter._sse_clients[token].append(q)
        since = request.args.get("since")

        resp = await make_response(_stream_gen(adapter, token, q, since), {
            "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
            "Connection": "keep-alive", "Transfer-Encoding": "chunked",
            "X-Accel-Buffering": "no",
        })
        resp.timeout = None
        return resp

    @app.get("/api/v1/botapi/history")
    async def get_history():
        from . import history as hist_mod
        token = _extract_token(adapter)
        since = request.args.get("since")
        before = request.args.get("before")
        limit = min(int(request.args.get("limit", 50)), 200)
        msgs, has_more = await hist_mod.get_history(adapter.platform_id, token, since, before, limit)
        return jsonify({"messages": msgs, "has_more": has_more})


async def _stream_gen(adapter, token, q, since):
    try:
        if since:
            for evt in await catchup_events(adapter.platform_id, token, since):
                yield evt.to_sse()
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=30)
            except asyncio.TimeoutError:
                yield SSEEvent.ping().to_sse()
                continue
            if item is None:
                break
            yield item.to_sse()
    except asyncio.CancelledError:
        pass
    finally:
        if q in adapter._sse_clients.get(token, []):
            adapter._sse_clients[token].remove(q)


def _extract_token(adapter):
    return request.headers.get("Authorization", "").removeprefix("Bearer ").strip()


def _is_valid_token(adapter, token):
    tokens = adapter.cfg.tokens or []
    return token in tokens if tokens else bool(token)


def _get_or_create_origin(adapter, token):
    origin = f"{adapter.platform_id}:FriendMessage:{token}"
    adapter._token_to_origin.setdefault(token, origin)
    return origin


def _file_info_to_component(info):
    mime = info.get("mime_type", "")
    path = info["path"]
    if mime.startswith("image/"):
        return Image.fromFileSystem(path)
    if mime.startswith("audio/") or "ogg" in mime:
        return Record.fromFileSystem(path)
    return File(name=info["name"], file=path)
