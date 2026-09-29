# tests/test_review_hardening.py
#
# v3.0.8 review 修复的回归用例：
# - /upload/chunk 与 /upload/complete 的 upload_id 直接拼进文件名，"../" 可写出
#   上传目录（路径穿越）→ 必须拒绝非法 upload_id。
# - /history?limit=非整数 旧实现 int() 直接抛 → 500；应 400，且 limit 夹到 1..200。
# - 主动消息（send_by_session，如定时提醒）只推 SSE 不落 platform_message_history，
#   断线期间错过 / 其它设备都无法从 /history 补回 → 需落库。
import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from quart.datastructures import FileStorage

from astrbot_plugin_botapi import history as hist
from astrbot_plugin_botapi import routes as routes_mod
from astrbot_plugin_botapi.adapter import BotApiAdapter

HDRS = {"Authorization": "Bearer tok"}


def _make_adapter(tmp_path):
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.cfg = SimpleNamespace(host="127.0.0.1", port=9000, tokens=["tok"])
    adapter.config = {"id": "botapi", "tokens": ["tok"]}
    adapter.platform_id = "botapi"
    adapter._disabled_tokens = set()
    adapter._last_active = {}
    adapter._uploaded_files = {}
    adapter._upload_dir = Path(tmp_path) / "uploads"
    adapter._upload_dir.mkdir()
    adapter._media_enabled = True
    adapter._serializer = SimpleNamespace()
    adapter.client_self_id = "selfid"
    adapter._sse_clients = {}
    adapter._token_to_origin = {}
    adapter.commit_event = lambda e: None
    from quart import Quart
    adapter.app = Quart("t")
    routes_mod._setup_routes(adapter)
    return adapter


def _chunk(data: bytes):
    return {"file": FileStorage(stream=io.BytesIO(data), filename="chunk",
                                content_type="application/octet-stream")}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["/../evil", "../evil", "a/b", "..", ".hidden", "x" * 200, "a b"])
async def test_chunk_rejects_bad_upload_id(tmp_path, bad_id):
    adapter = _make_adapter(tmp_path)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/upload/chunk",
                          form={"upload_id": bad_id, "offset": "0"},
                          files=_chunk(b"pwned"), headers=HDRS)
    assert r.status_code == 400
    # 上传目录之外不得出现任何文件
    assert sorted(p.name for p in Path(tmp_path).iterdir()) == ["uploads"]


@pytest.mark.asyncio
async def test_complete_rejects_bad_upload_id(tmp_path):
    adapter = _make_adapter(tmp_path)
    # 在上传目录外预置一个「.part」，旧实现会把它 rename 进上传目录
    # upload_id="/../x" → 拼出 "./../x.part" → 上传目录的上一级
    outside = Path(tmp_path) / "x.part"
    outside.write_bytes(b"x")
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/upload/complete",
                          json={"upload_id": "/../x", "filename": "a.bin"},
                          headers=HDRS)
    assert r.status_code == 400
    assert outside.exists()


@pytest.mark.asyncio
async def test_client_style_upload_id_still_accepted(tmp_path):
    # App 生成的 upload_id 形如 "<毫秒时间戳>_<哈希>"，必须继续可用。
    adapter = _make_adapter(tmp_path)
    client = adapter.app.test_client()
    uid = "1759119600123_123456789"
    r = await client.post("/api/v1/botapi/upload/chunk",
                          form={"upload_id": uid, "offset": "0"},
                          files=_chunk(b"hello"), headers=HDRS)
    assert r.status_code == 200
    r = await client.post("/api/v1/botapi/upload/complete",
                          json={"upload_id": uid, "filename": "a.bin"},
                          headers=HDRS)
    assert r.status_code == 200
    assert (await r.get_json())["size"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [("abc", None), ("0", 1), ("-5", 1),
                                          ("999", 200), ("20", 20)])
async def test_history_limit_validation(tmp_path, monkeypatch, raw, expected):
    adapter = _make_adapter(tmp_path)
    seen = {}

    async def fake_get_history(pid, token, since=None, before=None, limit=50):
        seen["limit"] = limit
        return ([], False)

    monkeypatch.setattr(hist, "get_history", fake_get_history)
    client = adapter.app.test_client()
    r = await client.get(f"/api/v1/botapi/history?limit={raw}", headers=HDRS)
    if expected is None:
        assert r.status_code == 400
    else:
        assert r.status_code == 200
        assert seen["limit"] == expected


@pytest.mark.asyncio
async def test_proactive_message_is_persisted(monkeypatch):
    a = BotApiAdapter.__new__(BotApiAdapter)
    a.platform_id = "botapi"
    a._sse_clients = {}
    a._put = lambda q, evt: q.put_nowait(evt)
    q = asyncio.Queue(maxsize=10)
    a._sse_clients["tok:abc"] = [q]

    async def _fake_serialize_chain(chain, evt):
        return {"type": "text", "content": "该喝水了", "timestamp": 0}
    a._serializer = SimpleNamespace(serialize_chain=_fake_serialize_chain)

    async def _no_media(*args, **kwargs):
        return None
    a._push_media = _no_media

    import astrbot_plugin_botapi.adapter as adapter_mod

    async def _noop(**kwargs):
        return None
    monkeypatch.setattr(adapter_mod.Metric, "upload", staticmethod(_noop))

    persisted = []

    async def fake_persist(scoped, mid, text, kind):
        persisted.append((scoped, text, kind))
    monkeypatch.setattr(hist, "persist_assistant_text", fake_persist)

    await a.send_by_session(SimpleNamespace(session_id="tok:abc"), SimpleNamespace(chain=[]))
    ev = await q.get()
    assert ev.data["content"] == "该喝水了"
    assert persisted == [("tok:abc", "该喝水了", "final")]
