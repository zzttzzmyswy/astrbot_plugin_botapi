import time

from astrbot.api.platform import AstrMessageEvent
from astrbot.api.event import MessageChain

from .models import SSEEvent
from .history import persist_assistant_text, persist_assistant_thinking
from . import sessions as _sessions

TOOL_STATUS_TYPE = "tool_call"


class BotApiMessageEvent(AstrMessageEvent):
    def __init__(self, message_str, message_obj, platform_meta, session_id, adapter):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.adapter = adapter
        self.token = message_obj.sender.user_id
        # session_id 形如 {pid}:FriendMessage:{token}[:{sid}]
        parts = (session_id or "").split(":")
        self.sid = parts[3] if len(parts) > 3 else "default"
        self._text_buf: list = []

    async def _broadcast(self, evt: SSEEvent):
        scoped = _sessions.scoped_key_for(self.adapter, self.token, self.sid)
        evt.data = dict(evt.data or {})
        evt.data["session_id"] = "" if self.sid == "default" else self.sid
        await self.adapter._broadcast_to(scoped, evt)

    async def send(self, message: MessageChain) -> None:
        if message is None:
            return
        await super().send(message)
        mtype = getattr(message, "type", None) or ""
        mid = self.message_obj.message_id

        if mtype == TOOL_STATUS_TYPE:
            txt = message.get_plain_text() if hasattr(message, "get_plain_text") else ""
            if txt:
                await self._broadcast(SSEEvent("message", {
                    "message_id": mid, "type": "text", "subtype": "tool_status", "content": txt,
                    "streaming": False, "final": False, "timestamp": int(time.time())}))
                await persist_assistant_text(self.token, mid, txt, kind="tool_status")
            return

        # 普通回复（含 tool_direct_result 工具直答，可带媒体）
        payload = await self.adapter._serializer.serialize_chain(message, self)
        await self._broadcast(SSEEvent("message", {**payload, "streaming": False, "final": True}))
        await self.adapter._push_media(message, self.token, mid, self.sid)
        await persist_assistant_text(self.token, mid, payload.get("content", ""), kind="final")

    async def send_streaming(self, generator, use_fallback=False) -> None:
        await super().send_streaming(generator, use_fallback)
        mid = self.message_obj.message_id
        full_text: list = []
        thinking: list = []
        async for chain in generator:
            if chain is None:
                continue
            ctype = getattr(chain, "type", None) or ""
            if ctype == "break":
                if self._text_buf:
                    seg = "".join(self._text_buf)
                    await self._broadcast(SSEEvent("message", {
                        "message_id": mid, "type": "text", "content": seg,
                        "streaming": True, "segment_end": True, "timestamp": int(time.time())}))
                    full_text.extend(self._text_buf); self._text_buf.clear()
                continue
            if ctype == "reasoning":
                t = chain.get_plain_text() if hasattr(chain, "get_plain_text") else ""
                if t:
                    thinking.append(t)
                    await self._broadcast(SSEEvent("thinking", {
                        "message_id": mid, "content": t, "streaming": True, "timestamp": int(time.time())}))
                continue
            if ctype in ("audio_chunk", "aborted"):
                continue
            # plain 增量
            t = chain.get_plain_text() if hasattr(chain, "get_plain_text") else ""
            if t:
                self._text_buf.append(t)
                await self._broadcast(SSEEvent("message", {
                    "message_id": mid, "type": "text", "content": t,
                    "streaming": True, "timestamp": int(time.time())}))
            await self.adapter._push_media(chain, self.token, mid, self.sid)
        if self._text_buf:
            full_text.extend(self._text_buf); self._text_buf.clear()
        final_text = "".join(full_text)
        await self._broadcast(SSEEvent("message", {
            "message_id": mid, "type": "text", "content": final_text,
            "streaming": False, "final": True, "timestamp": int(time.time())}))
        if thinking:
            await persist_assistant_thinking(self.token, mid, "".join(thinking))
        if final_text:
            await persist_assistant_text(self.token, mid, final_text, kind="final")
