# tests/test_binding_proactive.py
import asyncio
from types import SimpleNamespace
import pytest


def _adapter(monkeypatch):
    from astrbot_plugin_botapi.adapter import BotApiAdapter
    _abs = BotApiAdapter.__abstractmethods__
    BotApiAdapter.__abstractmethods__ = frozenset()
    try:
        a = object.__new__(BotApiAdapter)
    finally:
        BotApiAdapter.__abstractmethods__ = _abs
    a.platform_id = "botapi"
    a.config = {"id": "botapi", "tokens": [], "nicknames": {}, "sessions": {}}
    a.cfg = SimpleNamespace(tokens=[], nicknames={}, sessions={})
    a._sse_clients = {}
    a._token_to_origin = {}
    a._serializer = SimpleNamespace()
    a._media_enabled = True
    a._put = lambda q, evt: q.put_nowait(evt)
    return a


@pytest.mark.asyncio
async def test_send_by_session_reverse_botapi_prefix(monkeypatch):
    """绑定平台 UMO 的主动消息：session_id=botapi_tok → 投到 tok 分区。"""
    a = _adapter(monkeypatch)
    q = asyncio.Queue(maxsize=10)
    a._sse_clients["tok"] = [q]
    async def _fake_serialize_chain(chain, evt):
        return {"type": "text", "content": "proactive", "timestamp": 0}
    a._serializer.serialize_chain = _fake_serialize_chain
    # 模拟绑定平台 UMO 的 MessageSession：第三段 = botapi_tok
    session = SimpleNamespace(session_id="botapi_tok")
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Plain
    # 需 patch Metric.upload（super().send_by_session 会 create_task，须返回协程）
    import astrbot.core.platform.platform as _pmod
    async def _noop(**kwargs):
        return None
    monkeypatch.setattr(_pmod.Metric, "upload", staticmethod(_noop))
    await a.send_by_session(session, MessageChain([Plain("hi")]))
    ev = await q.get()
    assert ev.data["content"] == "proactive"


@pytest.mark.asyncio
async def test_send_by_session_scoped_botapi_prefix(monkeypatch):
    a = _adapter(monkeypatch)
    q = asyncio.Queue(maxsize=10)
    a._sse_clients["tok:abc"] = [q]
    async def _fake_serialize_chain(chain, evt):
        return {"type": "text", "content": "p", "timestamp": 0}
    a._serializer.serialize_chain = _fake_serialize_chain
    session = SimpleNamespace(session_id="botapi_tok:abc")
    import astrbot.core.platform.platform as _pmod
    async def _noop(**kwargs):
        return None
    monkeypatch.setattr(_pmod.Metric, "upload", staticmethod(_noop))
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Plain
    await a.send_by_session(session, MessageChain([Plain("hi")]))
    ev = await q.get()
    assert ev.data["session_id"] == "abc"
