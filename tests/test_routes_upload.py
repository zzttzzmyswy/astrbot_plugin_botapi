import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi import routes as routes_mod
from quart.datastructures import FileStorage


def _make_adapter(tmp_path, monkeypatch):
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.cfg = SimpleNamespace(host="127.0.0.1", port=9000, tokens=["tok"])
    adapter.config = {"id": "botapi", "tokens": ["tok"]}
    adapter.platform_id = "botapi"
    adapter._disabled_tokens = set(); adapter._last_active = {}
    adapter._uploaded_files = {}
    adapter._upload_dir = Path(tmp_path); adapter._media_enabled = True
    adapter._serializer = SimpleNamespace(); adapter.client_self_id = "selfid"
    adapter._sse_clients = {}; adapter._token_to_origin = {}
    adapter.commit_event = lambda e: None
    from quart import Quart
    adapter.app = Quart("t")
    routes_mod._setup_routes(adapter)
    return adapter


@pytest.mark.asyncio
async def test_upload_returns_file_info(tmp_path, monkeypatch):
    adapter = _make_adapter(tmp_path, monkeypatch)
    client = adapter.app.test_client()
    stream = io.BytesIO(b"hello bytes")
    file_storage = FileStorage(stream=stream, filename="photo.jpg",
                               content_type="image/jpeg")
    r = await client.post("/api/v1/botapi/upload",
                          files={"file": file_storage},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    body = await r.get_json()
    assert body["file_id"].startswith("f_")
    assert body["name"] == "photo.jpg"
    assert body["size"] == len(b"hello bytes")
    assert "path" not in body   # 不泄露服务器路径
    assert adapter._uploaded_files[body["file_id"]]["path"].endswith("photo.jpg")


@pytest.mark.asyncio
async def test_upload_no_file(tmp_path, monkeypatch):
    adapter = _make_adapter(tmp_path, monkeypatch)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/upload",
                          files={},
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400


def _chunk(adapter, data: bytes):
    return [FileStorage(stream=io.BytesIO(data), filename="chunk",
                        content_type="application/octet-stream")]


@pytest.mark.asyncio
async def test_chunk_sequential_append(tmp_path, monkeypatch):
    # 顺序分块(offset 递增)拼接后 complete:内容与单次上传一致。
    adapter = _make_adapter(tmp_path, monkeypatch)
    client = adapter.app.test_client()
    hdrs = {"Authorization": "Bearer tok"}
    for offset, data in [(0, b"hel"), (3, b"lo ") , (6, b"world")]:
        r = await client.post("/api/v1/botapi/upload/chunk",
                              form={"upload_id": "u1", "offset": str(offset)},
                              files={"file": _chunk(adapter, data)[0]},
                              headers=hdrs)
        assert r.status_code == 200
        body = await r.get_json()
        assert body["offset"] == offset + len(data)
    r = await client.post("/api/v1/botapi/upload/complete",
                          json={"upload_id": "u1", "filename": "a.bin",
                                "mime_type": "application/octet-stream"},
                          headers=hdrs)
    assert r.status_code == 200
    body = await r.get_json()
    assert body["size"] == 11
    saved = adapter._uploaded_files[body["file_id"]]["path"]
    with open(saved, "rb") as f:
        assert f.read() == b"hello world"


@pytest.mark.asyncio
async def test_chunk_retry_same_offset_idempotent(tmp_path, monkeypatch):
    # 超时重试的块重复发到同一 offset:覆盖而非重复追加,不会写坏 .part。
    adapter = _make_adapter(tmp_path, monkeypatch)
    client = adapter.app.test_client()
    hdrs = {"Authorization": "Bearer tok"}
    url = "/api/v1/botapi/upload/chunk"
    for _ in range(2):
        r = await client.post(url,
                              form={"upload_id": "u1", "offset": "0"},
                              files={"file": _chunk(adapter, b"abcdef")[0]},
                              headers=hdrs)
        assert r.status_code == 200
        assert (await r.get_json())["offset"] == 6
    r = await client.post("/api/v1/botapi/upload/complete",
                          json={"upload_id": "u1", "filename": "a.bin",
                                "mime_type": "application/octet-stream"},
                          headers=hdrs)
    body = await r.get_json()
    assert body["size"] == 6
    with open(adapter._uploaded_files[body["file_id"]]["path"], "rb") as f:
        assert f.read() == b"abcdef"


@pytest.mark.asyncio
async def test_chunk_offset_rewrite_truncates_tail(tmp_path, monkeypatch):
    # 覆盖旧 offset 重写时,truncate 会去掉该块之后残留的半块字节。
    adapter = _make_adapter(tmp_path, monkeypatch)
    client = adapter.app.test_client()
    hdrs = {"Authorization": "Bearer tok"}
    url = "/api/v1/botapi/upload/chunk"
    await client.post(url, form={"upload_id": "u1", "offset": "0"},
                      files={"file": _chunk(adapter, b"abcdef")[0]},
                      headers=hdrs)
    await client.post(url, form={"upload_id": "u1", "offset": "0"},
                      files={"file": _chunk(adapter, b"xyz")[0]},
                      headers=hdrs)
    r = await client.post("/api/v1/botapi/upload/complete",
                          json={"upload_id": "u1", "filename": "a.bin",
                                "mime_type": "application/octet-stream"},
                          headers=hdrs)
    body = await r.get_json()
    assert body["size"] == 3
    with open(adapter._uploaded_files[body["file_id"]]["path"], "rb") as f:
        assert f.read() == b"xyz"


@pytest.mark.asyncio
async def test_chunk_empty_probe_returns_size_without_write(tmp_path, monkeypatch):
    # 客户端离线重试前发 0 字节探针(offset=-1):返回当前 .part 大小,不做任何写。
    adapter = _make_adapter(tmp_path, monkeypatch)
    client = adapter.app.test_client()
    hdrs = {"Authorization": "Bearer tok"}
    url = "/api/v1/botapi/upload/chunk"
    r = await client.post(url, form={"upload_id": "u1", "offset": "0"},
                          files={"file": _chunk(adapter, b"hello")[0]},
                          headers=hdrs)
    assert r.status_code == 200
    probe = FileStorage(stream=io.BytesIO(b""), filename="probe",
                        content_type="application/octet-stream")
    r = await client.post(url, form={"upload_id": "u1", "offset": "-1"},
                          files={"file": probe}, headers=hdrs)
    assert r.status_code == 200
    assert (await r.get_json())["offset"] == 5
    assert (adapter._upload_dir / ".u1.part").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_chunk_invalid_offset_rejected(tmp_path, monkeypatch):
    # 写入位置超过当前进度:拒绝(防止稀疏洞/乱序写坏文件)。
    adapter = _make_adapter(tmp_path, monkeypatch)
    client = adapter.app.test_client()
    hdrs = {"Authorization": "Bearer tok"}
    url = "/api/v1/botapi/upload/chunk"
    r = await client.post(url, form={"upload_id": "u1", "offset": "100"},
                          files={"file": _chunk(adapter, b"abc")[0]},
                          headers=hdrs)
    assert r.status_code == 400
    r = await client.post(url, form={"upload_id": "u1", "offset": "-1"},
                          files={"file": _chunk(adapter, b"abc")[0]},
                          headers=hdrs)
    assert r.status_code == 400
