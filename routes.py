# routes.py
import time
import uuid

from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
from astrbot.api.message_components import Image, Record, File, Plain
from quart import jsonify, request

from .event import BotApiMessageEvent
from .history import persist_inbound_text


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
