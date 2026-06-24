import time


class MessageSerializer:
    def __init__(self, _media_enabled: bool = False):
        self._media_enabled = _media_enabled

    async def serialize_chain(self, message, event) -> dict:
        text_parts = []
        for comp in (message.chain or []):
            ct = comp.type.value.lower() if hasattr(comp.type, "value") else str(comp.type).lower()
            if ct == "plain":   # ComponentType 仅 'Plain'（'text' 不命中，防御性）
                text_parts.append(getattr(comp, "text", ""))
            # image/record/file 由 adapter._push_media 推送
        return {
            "message_id": event.message_obj.message_id if event else None,
            "role": "assistant", "type": "text",
            "content": "".join(text_parts),
            "timestamp": int(time.time()),
        }

    async def _media_url(self, comp):
        if not self._media_enabled:
            return None
        try:
            return await comp.register_to_file_service()
        except Exception:
            try:
                if hasattr(comp, "get_file"):                      # 仅 File
                    return await comp.get_file(allow_return_url=True)
                if hasattr(comp, "convert_to_file_path"):          # Image/Record（返回本地路径非 URL）
                    p = await comp.convert_to_file_path()
                    return p or None
            except Exception:
                pass
            return None
