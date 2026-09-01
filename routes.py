# routes.py
import asyncio
import time
import uuid

from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
from astrbot.api.message_components import Image, Record, File, Plain
from quart import jsonify, request
from werkzeug.utils import secure_filename

from .event import BotApiMessageEvent
from .history import persist_inbound_text, catchup_events
from .models import SSEEvent
from . import sessions as _sessions


async def submit_inbound(adapter, token, text, file_ids=None, session_id="") -> str:
    """构造入站 AstrBotMessage + BotApiMessageEvent，persist + commit。
    手机 /message 与管理页 /chat 共用。session_id 缺省/空 → 默认会话。
    返回 message_id。"""
    sid = _sessions.resolve_sid(adapter, token, session_id)
    scoped_key = _sessions.scoped_key_for(adapter, token, sid)
    _get_or_create_origin(adapter, token)   # 建立 token→origin 映射
    msg = AstrBotMessage()
    msg.type = MessageType.FRIEND_MESSAGE
    msg.self_id = adapter.client_self_id
    # AstrMessageEvent 会把 session_id 当作「裸第三段」再拼 {pid}:FriendMessage: 前缀，
    # 故这里必须传 scoped_key(默认="{token}"，分会话="{token}:{sid}")而非完整 umo，
    # 否则 unified_msg_origin 会双重前缀(如 botapi:FriendMessage:botapi:FriendMessage:...)，
    # 导致会话上下文路由错乱、管理页会话 id 显示错误。
    msg.session_id = scoped_key
    msg.message_id = f"botapi_{uuid.uuid4().hex[:12]}"
    msg.sender = MessageMember(user_id=token, nickname="User")
    msg.timestamp = int(time.time())
    components = []
    if text:
        components.append(Plain(text))
    if file_ids:
        for fid in file_ids:
            info = adapter._uploaded_files.get(fid)
            if info:
                components.append(_file_info_to_component(info))
    msg.message = components
    msg.message_str = text or "[消息]"
    msg.raw_message = {"text": text, "file_ids": file_ids or []}

    event = BotApiMessageEvent(message_str=msg.message_str, message_obj=msg,
                               platform_meta=adapter.meta(), session_id=scoped_key,
                               adapter=adapter)
    # 多机器人绑定：覆写 UMO 使 AstrBot 路由到绑定平台配置文件。
    # msg.session_id 仍是裸 scoped key（AstrMessageEvent 会拼 {pid}:FriendMessage: 前缀），
    # 这里覆写的是构造后的完整 UMO，前缀段换成绑定平台（若绑定且平台活跃）。
    # getattr 防御：旧测试 fake 无 binding_platform_for；真实 BotApiAdapter 均有。
    _bind_lookup = getattr(adapter, "binding_platform_for", None)
    bound = _bind_lookup(token) if _bind_lookup else None
    if bound:
        event.unified_msg_origin = f"{bound}:FriendMessage:botapi_{scoped_key}"
    event.set_extra("enable_streaming", True)
    await persist_inbound_text(scoped_key, msg.message_id, text)
    adapter.commit_event(event)
    return msg.message_id


def _setup_routes(adapter):
    app = adapter.app
    # 允许大文件上传:Quart/Werkzeug 默认/框架可能设了较小的 MAX_CONTENT_LENGTH,
    # 实测 50MB 文件在 nginx 放过后应用层仍 413(RequestEntityTooLarge)。显式调到
    # 200MB 覆盖大多数场景;nginx 侧也需相应 client_max_body_size。request.files
    # 解析 multipart 时走临时文件,不会把整文件读入内存。
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

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
        session_id = (data or {}).get("session_id", "")
        try:
            message_id = await submit_inbound(adapter, token, text, file_ids, session_id)
        except LookupError:
            return jsonify({"error": "session_not_found"}), 404
        return jsonify({"message_id": message_id})

    @app.post("/api/v1/botapi/upload")
    async def upload_file():
        files = await request.files
        file = files.get("file")
        if not file:
            return jsonify({"error": "no_file"}), 400
        filename = secure_filename(file.filename or "untitled")
        file_id = f"f_{uuid.uuid4().hex[:10]}"
        save_path = adapter._upload_dir / f"{file_id}_{filename}"
        await file.save(save_path)
        info = {"file_id": file_id, "name": filename,
                "mime_type": file.content_type or "application/octet-stream",
                "size": save_path.stat().st_size}
        adapter._uploaded_files[file_id] = {**info, "path": str(save_path)}
        return jsonify(info)

    # 分块上传:大文件切成多块逐个 POST(每块 ≪ 服务端请求超时),最后合并。
    # 绕过服务端 ASGI/反代 ~90s 的请求超时(50MB 单次上传会被 408)。
    @app.post("/api/v1/botapi/upload/chunk")
    async def upload_chunk():
        form = await request.form
        upload_id = (form.get("upload_id") or "").strip()
        if not upload_id:
            return jsonify({"error": "no_upload_id"}), 400
        files = await request.files
        chunk = files.get("file")
        if chunk is None:
            return jsonify({"error": "no_file"}), 400
        part_path = adapter._upload_dir / f".{upload_id}.part"
        try:
            offset = int((form.get("offset") or "0").strip())
        except (TypeError, ValueError):
            offset = 0
        with (open(part_path, "r+b") if part_path.exists()
                else open(part_path, "w+b")) as f:
            first = chunk.stream.read(65536)
            if not first:
                # 空块 = 进度探针(客户端离线重试前询问真实 offset):不做任何写。
                return jsonify({"upload_id": upload_id, "offset": f.seek(0, 2)})
            if offset < 0 or offset > f.seek(0, 2):
                # 写入位置不能超过当前进度:防止稀疏洞/乱序写坏 .part。
                return jsonify({"error": "invalid_offset"}), 400
            # 按客户端 offset 写入(seek + truncate):对落在同一 offset 的重复块做
            # 覆盖重写而非盲目追加,使超时重试幂等——重发的块不会重复拼入写坏文件。
            # 旧客户端(offset 顺序递增)行为不变,seek 覆盖等价于顺序拼接。
            f.seek(offset)
            f.truncate()  # 去掉该 offset 之后可能残留的旧字节(中断的半块)
            while True:
                f.write(first)
                buf = chunk.stream.read(65536)
                if not buf:
                    break
                f.write(buf)
            new_size = f.tell()
        return jsonify({"upload_id": upload_id, "offset": new_size})

    @app.post("/api/v1/botapi/upload/complete")
    async def upload_complete():
        data = await request.get_json() or {}
        upload_id = (data.get("upload_id") or "").strip()
        filename = secure_filename(data.get("filename") or "untitled")
        mime_type = data.get("mime_type") or "application/octet-stream"
        part_path = adapter._upload_dir / f".{upload_id}.part"
        if not part_path.exists():
            return jsonify({"error": "no_part"}), 400
        file_id = f"f_{uuid.uuid4().hex[:10]}"
        save_path = adapter._upload_dir / f"{file_id}_{filename}"
        part_path.rename(save_path)
        info = {"file_id": file_id, "name": filename,
                "mime_type": mime_type, "size": save_path.stat().st_size}
        adapter._uploaded_files[file_id] = {**info, "path": str(save_path)}
        return jsonify(info)

    @app.get("/api/v1/botapi/stream")
    async def stream():
        from quart import make_response
        token = _extract_token(adapter)
        try:
            sid = _sessions.resolve_sid(adapter, token, request.args.get("session_id"))
        except LookupError:
            return jsonify({"error": "session_not_found"}), 404
        scoped = _sessions.scoped_key_for(adapter, token, sid)
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        adapter._sse_clients[scoped].append(q)
        since = request.args.get("since")

        resp = await make_response(_stream_gen(adapter, scoped, q, since), {
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
        try:
            sid = _sessions.resolve_sid(adapter, token, request.args.get("session_id"))
        except LookupError:
            return jsonify({"error": "session_not_found"}), 404
        scoped = _sessions.scoped_key_for(adapter, token, sid)
        since = request.args.get("since")
        before = request.args.get("before")
        limit = min(int(request.args.get("limit", 50)), 200)
        msgs, has_more = await hist_mod.get_history(adapter.platform_id, scoped, since, before, limit)
        return jsonify({"messages": msgs, "has_more": has_more})

    @app.get("/api/v1/botapi/sessions")
    async def list_sessions():
        token = _extract_token(adapter)
        return jsonify({"sessions": _sessions.sessions_list(adapter, token),
                        "default_id": _sessions.DEFAULT_SESSION_ID})

    @app.post("/api/v1/botapi/sessions")
    async def create_session():
        token = _extract_token(adapter)
        data = await request.get_json() or {}
        raw_name = data.get("name")
        name = (raw_name.strip() if isinstance(raw_name, str) else "").strip()
        if not name:
            return jsonify({"error": "name_required"}), 400
        cur = _sessions.sessions_list(adapter, token)
        if len(cur) >= _sessions.MAX_SESSIONS:
            return jsonify({"error": "session_limit"}), 400
        new = {"id": uuid.uuid4().hex[:12], "name": name,
               "created_at": int(time.time())}
        cur.append(new)
        _sessions.save_sessions(adapter, token, cur)
        return jsonify({"session": new})

    @app.post("/api/v1/botapi/sessions/<sid>/rename")
    async def rename_session(sid):
        token = _extract_token(adapter)
        data = await request.get_json() or {}
        raw_name = data.get("name")
        name = (raw_name.strip() if isinstance(raw_name, str) else "").strip()
        if not name:
            return jsonify({"error": "name_required"}), 400
        cur = _sessions.sessions_list(adapter, token)
        for x in cur:
            if x["id"] == sid:
                x["name"] = name
                _sessions.save_sessions(adapter, token, cur)
                return jsonify({"message": "会话已重命名"})
        return jsonify({"error": "not_found"}), 404

    @app.post("/api/v1/botapi/sessions/<sid>/delete")
    async def delete_session(sid):
        token = _extract_token(adapter)
        if sid == _sessions.DEFAULT_SESSION_ID:
            return jsonify({"error": "default_not_deletable"}), 400
        if not any(x["id"] == sid for x in _sessions.sessions_list(adapter, token)):
            return jsonify({"error": "not_found"}), 404
        await _sessions.delete_session(adapter, token, sid)
        return jsonify({"message": "会话已删除"})


async def _stream_gen(adapter, scoped, q, since):
    # 不经 SSE 回放历史：catchup 会把历史行（含较早消息）重发给 client，client
    # 用本地 now() 存 created_at、丢弃事件自带的 timestamp，导致较早的历史被
    # 盖上最新时间、排到真正最新记录下方。历史补漏改由 client 调 /history 端点
    # （row_to_sse 带真实 timestamp+role，mergeHistory 正确落库）。since 参数
    # 保留以兼容 client 的 /stream?since=<cursor> URL，但不再回放。
    try:
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
        if q in adapter._sse_clients.get(scoped, []):
            adapter._sse_clients[scoped].remove(q)


def _extract_token(adapter):
    return request.headers.get("Authorization", "").removeprefix("Bearer ").strip()


def _is_valid_token(adapter, token):
    return token in (adapter.cfg.tokens or [])


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
