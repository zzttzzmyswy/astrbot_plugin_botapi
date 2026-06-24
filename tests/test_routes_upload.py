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
