import json
from astrbot_plugin_botapi.models import SSEEvent, BotApiConfig


def test_sse_event_to_sse_has_event_and_data():
    evt = SSEEvent("message", {"message_id": "m1", "content": "hi"})
    s = evt.to_sse()
    assert s.startswith("event: message\n")
    assert "data: " in s
    payload = s.split("data: ", 1)[1].strip()
    assert json.loads(payload)["message_id"] == "m1"


def test_sse_event_no_data():
    evt = SSEEvent("ping", None)
    s = evt.to_sse()
    assert s.startswith("event: ping\n")
    assert "data:" not in s


def test_sse_ping_factory():
    evt = SSEEvent.ping()
    assert evt.event_type == "ping"
    assert evt.data == {}


def test_botapi_config_defaults():
    cfg = BotApiConfig()
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9000
    assert cfg.tokens == []


def test_botapi_config_from_dict():
    cfg = BotApiConfig(host="127.0.0.1", port=8080, tokens=["t1"])
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8080
    assert cfg.tokens == ["t1"]
