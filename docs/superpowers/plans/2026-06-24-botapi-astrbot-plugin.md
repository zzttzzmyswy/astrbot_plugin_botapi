# BotAPI AstrBot 插件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个 AstrBot 自定义平台适配器插件，通过 REST+SSE HTTP API 让手机 App 收发消息（纯 SSE 回复、逐 token 流式、断连补消息、文本历史镜像、管理页）。

**Architecture:** `BotApiAdapter(Platform)` 跑手机 API（Quart 9000）+ SSE 回流（子类化 `AstrMessageEvent` 重写 `send/send_streaming`）；`BotApiStar(Star)` 持 `context` 注册管理 API + 注入 managers 到 `RuntimeState` 单例。详见 `docs/superpowers/specs/2026-06-24-botapi-astrbot-plugin-design.md`。

**Tech Stack:** Python 3.10+、AstrBot 4.25.5（本地源码 `/home/zzt/workspace/AstrBot`）、Quart（AstrBot 自带）、pytest、原生 HTML/CSS/JS（管理页）。

**关键约束（spec §1）：** Platform 无 `self.context`；`@register_platform_adapter` 装饰器（无 `support_proactive_message`）；3 参 `__init__`；回复回流靠子类化事件重写 `send/send_streaming`（无 `_on_reply`）；`session_id=token`（umo=`{pid}:FriendMessage:{token}` 驼峰）；流式需 `set_extra("enable_streaming", True)`；SSE 队列非阻塞 `put_nowait`；Star 须定义在 `main.py`；管理 API 全 GET/POST（无 DELETE/PATCH）；bridge 用 `apiPost`（无 apiDelete/apiPatch）。

---

## File Structure

| 文件 | 职责 |
|:--|:--|
| `metadata.yaml` | 插件元数据（name/desc/version/author/repo/astrbot_version） |
| `conftest.py` | 测试 sys.path（AstrBot 源码 + 插件父目录） |
| `pyproject.toml` | pytest 配置 |
| `models.py` | `SSEEvent`（to_sse/ping）、`BotApiConfig`（host/port/tokens） |
| `runtime.py` | `RuntimeState` 单例（adapter/conversation_manager/message_history_manager/context） |
| `history.py` | `platform_message_history` 文本镜像：`persist_*`/`get_history`/`catchup_events`/`row_to_sse` |
| `serializer.py` | `MessageSerializer`：`serialize_chain`（文本 payload）、`_media_url`（铸 token） |
| `adapter.py` | `BotApiAdapter(Platform)`：`__init__`/`meta`/`run`/`terminate`/`send_by_session`/`_put`/`_broadcast_to`/`_push_media` + `_setup_routes` |
| `event.py` | `BotApiMessageEvent(AstrMessageEvent)`：`send`/`send_streaming` |
| `routes.py` | 手机 API 路由（auth/message/upload/stream/history），由 adapter `_setup_routes` 注册 |
| `main.py` | 入口：`from .adapter import BotApiAdapter`（触发 `@register_platform_adapter`）+ 定义 `BotApiStar(Star)` |
| `pages/dashboard/index.html` | 管理页结构 |
| `pages/dashboard/app.js` | bridge 调用（apiGet/apiPost）+ 渲染 |
| `pages/dashboard/style.css` | 亮暗双主题 |
| `tests/test_*.py` | 单测/集成测试 |

---

## Task 1: 项目骨架与测试环境

**Files:**
- Create: `metadata.yaml`
- Create: `conftest.py`
- Create: `pyproject.toml`
- Create: `tests/test_setup_canary.py`

- [ ] **Step 1: 写 metadata.yaml**

```yaml
name: astrbot_plugin_botapi
desc: BotAPI 自定义移动端适配器 — 一人一 Bot 极简移动端接入，支持弱网断连恢复。
version: 1.0.0
author: ZZT
repo: https://github.com/ZZT/astrbot_plugin_botapi
astrbot_version: ">=4.25.0"
```

- [ ] **Step 2: 写 conftest.py（sys.path）**

```python
import sys
from pathlib import Path

# 让 `import astrbot` 可用（AstrBot 源码）
_ASTRBOT = Path("/home/zzt/workspace/AstrBot")
if _ASTRBOT.is_dir() and str(_ASTRBOT) not in sys.path:
    sys.path.insert(0, str(_ASTRBOT))

# 让 `import astrbot_plugin_botapi` 可用（插件父目录）
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))
```

- [ ] **Step 3: 写 pyproject.toml（pytest 配置）**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["."]
```

- [ ] **Step 4: 写 canary 测试验证 astrbot 可导入**

```python
# tests/test_setup_canary.py
def test_astrbot_importable():
    import astrbot
    from astrbot.api.platform import Platform, register_platform_adapter
    from astrbot.api.event import MessageChain
    from astrbot.core.platform.platform import PlatformStatus
    assert Platform is not None
```

- [ ] **Step 5: 运行 canary，确认通过（若失败先 `pip install -e /home/zzt/workspace/AstrBot`）**

Run: `pytest tests/test_setup_canary.py -v`
Expected: PASS。若 FAIL（ModuleNotFoundError），执行 `pip install -e /home/zzt/workspace/AstrBot` 后重跑。

- [ ] **Step 6: 提交**

```bash
git add metadata.yaml conftest.py pyproject.toml tests/test_setup_canary.py
git commit -m "chore: 项目骨架与测试环境"
```

---

## Task 2: models.py（SSEEvent + BotApiConfig）

**Files:**
- Create: `models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models.py
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_models.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'astrbot_plugin_botapi.models'`）

- [ ] **Step 3: 写实现**

```python
# models.py
import json
from dataclasses import dataclass, field


@dataclass
class BotApiConfig:
    host: str = "0.0.0.0"
    port: int = 9000
    tokens: list = field(default_factory=list)


@dataclass
class SSEEvent:
    event_type: str  # message | thinking | error | ping
    data: dict | None = None

    def to_sse(self) -> str:
        lines = [f"event: {self.event_type}"]
        if self.data is not None:
            lines.append(f"data: {json.dumps(self.data, ensure_ascii=False)}")
        lines.extend(["", ""])
        return "\n".join(lines)

    @classmethod
    def ping(cls) -> "SSEEvent":
        return cls("ping", {})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_models.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add models.py tests/test_models.py
git commit -m "feat(models): SSEEvent 与 BotApiConfig"
```

---

## Task 3: runtime.py（RuntimeState 单例）

**Files:**
- Create: `runtime.py`
- Create: `tests/test_runtime.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_runtime.py
from astrbot_plugin_botapi.runtime import runtime, RuntimeState


def test_runtime_singleton():
    rt1 = runtime()
    rt2 = runtime()
    assert rt1 is rt2


def test_runtime_initial_state_none():
    rt = runtime()
    assert rt.adapter is None
    assert rt.conversation_manager is None
    assert rt.message_history_manager is None


def test_runtime_set_get():
    rt = runtime()
    rt.adapter = "fake_adapter"
    assert runtime().adapter == "fake_adapter"
    rt.adapter = None  # 清理
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_runtime.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 写实现**

```python
# runtime.py
class RuntimeState:
    adapter: "object | None" = None
    conversation_manager: "object | None" = None
    message_history_manager: "object | None" = None
    context: "object | None" = None


_runtime = RuntimeState()


def runtime() -> RuntimeState:
    return _runtime
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_runtime.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add runtime.py tests/test_runtime.py
git commit -m "feat(runtime): RuntimeState 单例"
```

---

## Task 4: history.py 纯逻辑（row_to_sse + get_history + catchup int 过滤）

**Files:**
- Create: `history.py`
- Create: `tests/test_history_pure.py`

> 本任务只测不依赖 DB 的纯逻辑：`row_to_sse`、`get_history` 的 int 过滤、`catchup_events` 的 int 过滤。`persist_*` 在 Task 8。

- [ ] **Step 1: 写失败测试（用 fake row + fake message_history_manager）**

```python
# tests/test_history_pure.py
import asyncio
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from astrbot_plugin_botapi import history
from astrbot_plugin_botapi.models import SSEEvent


def _row(rid, kind, text, role="assistant"):
    return SimpleNamespace(
        id=rid,
        content={"role": role, "kind": kind, "text": text, "message_id": f"m{rid}"},
        created_at=datetime(2026, 6, 24, 12, 0, rid, tzinfo=timezone.utc),
    )


class FakePMH:
    def __init__(self, rows):
        self._rows = rows  # 已是升序

    async def get(self, platform_id, user_id, page=1, page_size=200):
        return list(self._rows)


def test_row_to_sse_final():
    r = _row(3, "final", "hello")
    m = history.row_to_sse(r)
    assert m["message_id"] == "3"
    assert m["role"] == "assistant"
    assert m["type"] == "text"
    assert m["content"] == "hello"
    assert isinstance(m["timestamp"], int)


def test_row_to_sse_thinking():
    r = _row(5, "thinking", "reasoning...")
    assert history.row_to_sse(r)["type"] == "thinking"


def test_row_to_sse_tool_status():
    r = _row(7, "tool_status", "🔧 tool")
    assert history.row_to_sse(r)["type"] == "tool_status"


def test_row_to_sse_user():
    r = _row(1, "user", "hi", role="user")
    assert history.row_to_sse(r)["role"] == "user"


@pytest.mark.asyncio
async def test_get_history_since_int_filter(monkeypatch):
    rows = [_row(1, "user", "u1", "user"), _row(2, "final", "a1"),
            _row(3, "final", "a2"), _row(4, "final", "a3")]
    fake_rt = SimpleNamespace(message_history_manager=FakePMH(rows), adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    msgs, has_more = await history.get_history("botapi", "tok", since="2", limit=50)
    assert [m["message_id"] for m in msgs] == ["3", "4"]


@pytest.mark.asyncio
async def test_get_history_before_int_filter(monkeypatch):
    rows = [_row(1, "final", "a1"), _row(2, "final", "a2"), _row(3, "final", "a3")]
    fake_rt = SimpleNamespace(message_history_manager=FakePMH(rows), adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    msgs, _ = await history.get_history("botapi", "tok", before="3", limit=50)
    assert [m["message_id"] for m in msgs] == ["1", "2"]


@pytest.mark.asyncio
async def test_get_history_limit(monkeypatch):
    rows = [_row(i, "final", f"a{i}") for i in range(1, 6)]
    fake_rt = SimpleNamespace(message_history_manager=FakePMH(rows), adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    msgs, has_more = await history.get_history("botapi", "tok", limit=2)
    assert [m["message_id"] for m in msgs] == ["4", "5"]
    assert has_more is True


@pytest.mark.asyncio
async def test_catchup_events_int_filter(monkeypatch):
    rows = [_row(1, "user", "u1", "user"), _row(2, "final", "a1"),
            _row(3, "thinking", "th"), _row(4, "final", "a2")]
    fake_rt = SimpleNamespace(message_history_manager=FakePMH(rows), adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    evts = await history.catchup_events("botapi", "tok", since="2")
    assert len(evts) == 2
    assert isinstance(evts[0], SSEEvent)
    # thinking 记录 → thinking 事件；final → message 事件
    assert evts[0].event_type == "thinking"
    assert evts[1].event_type == "message"


@pytest.mark.asyncio
async def test_catchup_int_not_lexicographic(monkeypatch):
    # 防字典序 bug：id 9 vs 10（字典序 "10" < "9"，但 int 10 > 9）
    rows = [_row(9, "final", "a9"), _row(10, "final", "a10")]
    fake_rt = SimpleNamespace(message_history_manager=FakePMH(rows), adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    evts = await history.catchup_events("botapi", "tok", since="9")
    assert len(evts) == 1
    assert evts[0].data["message_id"] == "10"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_history_pure.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 写实现**

```python
# history.py
from .runtime import runtime
from .models import SSEEvent


def row_to_sse(row):
    c = row.content or {}
    kind = c.get("kind")
    return {
        "message_id": str(row.id),
        "role": c.get("role", "assistant"),
        "type": {"final": "text", "thinking": "thinking", "tool_status": "tool_status"}.get(kind, "text"),
        "content": c.get("text", ""),
        "timestamp": int(row.created_at.timestamp()),
    }


async def get_history(platform_id, token, since=None, before=None, limit=50):
    rt = runtime()
    rows = await rt.message_history_manager.get(
        platform_id=platform_id, user_id=token, page=1, page_size=200)  # 升序
    msgs = [row_to_sse(r) for r in rows]
    if since:
        msgs = [m for m in msgs if int(m["message_id"]) > int(since)]   # int 比较
    if before:
        msgs = [m for m in msgs if int(m["message_id"]) < int(before)]
    return msgs[-limit:], len(msgs) == limit


async def catchup_events(platform_id, token, since):
    rt = runtime()
    rows = await rt.message_history_manager.get(
        platform_id=platform_id, user_id=token, page=1, page_size=200)
    out = []
    for r in rows:
        if int(r.id) <= int(since):   # int 比较（防字典序 bug）
            continue
        c = r.content or {}
        etype = "thinking" if c.get("kind") == "thinking" else "message"
        out.append(SSEEvent(etype, row_to_sse(r)))
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_history_pure.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**

```bash
git add history.py tests/test_history_pure.py
git commit -m "feat(history): row_to_sse + int since/before 过滤 + catchup"
```

---

## Task 5: serializer.py（serialize_chain + _media_url）

**Files:**
- Create: `serializer.py`
- Create: `tests/test_serializer.py`

- [ ] **Step 1: 写失败测试（用真实 astrbot 组件类型）**

```python
# tests/test_serializer.py
import asyncio
from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image
from astrbot_plugin_botapi.serializer import MessageSerializer


def _event(mid="m1", token="tok"):
    return SimpleNamespace(message_obj=SimpleNamespace(message_id=mid), token=token,
                           adapter=SimpleNamespace(_media_enabled=True, _sse_clients={}))


def test_serialize_chain_text():
    ser = MessageSerializer(_media_enabled=True)
    chain = MessageChain([Plain("hello "), Plain("world")])
    payload = asyncio.get_event_loop().run_until_complete(ser.serialize_chain(chain, _event()))
    assert payload["type"] == "text"
    assert payload["content"] == "hello world"
    assert payload["message_id"] == "m1"
    assert payload["role"] == "assistant"


@pytest.mark.asyncio
async def test_media_disabled_returns_none():
    ser = MessageSerializer(_media_enabled=False)
    comp = Image.fromFileSystem("/nonexistent.png")
    url = await ser._media_url(comp)
    assert url is None


@pytest.mark.asyncio
async def test_media_url_calls_register(monkeypatch):
    ser = MessageSerializer(_media_enabled=True)
    comp = Image.fromFileSystem("/nonexistent.png")
    called = {}

    async def fake_register(self):
        called["yes"] = True
        return "https://dash/api/file/tok123"

    monkeypatch.setattr(Image, "register_to_file_service", fake_register, raising=True)
    # Image.fromFileSystem 的实例方法绑定：用 monkeypatch 替换类方法
    url = await ser._media_url(comp)
    assert url == "https://dash/api/file/tok123"
    assert called.get("yes")
```

> 注：`MessageSerializer` 构造收 `_media_enabled` 参数（adapter 注入），避免直接依赖全局 config，便于测试（spec §7.2 备注）。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_serializer.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 写实现**

```python
# serializer.py
import time

from .models import SSEEvent


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
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_serializer.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add serializer.py tests/test_serializer.py
git commit -m "feat(serializer): serialize_chain 文本 payload + _media_url 铸 token"
```

---

## Task 6: adapter.py 核心（__init__/meta/_put/_broadcast_to/_push_media）

**Files:**
- Create: `adapter.py`
- Create: `tests/test_adapter_core.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_adapter_core.py
import asyncio
from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image
from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi.models import SSEEvent


def _make_adapter():
    # 绕过 PlatformManager 实例化，直接构造测试用 adapter
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter._sse_clients = {}
    adapter._media_enabled = True
    adapter._serializer = type("S", (), {"_media_url": classmethod(
        lambda cls, comp: _async_return("https://dash/api/file/" + str(id(comp) % 1000)))})()
    return adapter


async def _async_return(v):
    return v


def test_put_nonblocking_when_full():
    adapter = _make_adapter()
    q = asyncio.Queue(maxsize=2)
    adapter._put(q, SSEEvent("message", {"i": 1}))
    adapter._put(q, SSEEvent("message", {"i": 2}))
    # 第 3 个：队列满，丢最旧后放入，不抛异常
    adapter._put(q, SSEEvent("message", {"i": 3}))
    items = [asyncio.get_event_loop().run_until_complete(q.get()) for _ in range(2)]
    assert items[0].data["i"] == 2   # 最旧(1)被丢
    assert items[1].data["i"] == 3


@pytest.mark.asyncio
async def test_broadcast_to_all_queues():
    adapter = _make_adapter()
    adapter._sse_clients["tok"] = [asyncio.Queue(maxsize=10), asyncio.Queue(maxsize=10)]
    await adapter._broadcast_to("tok", SSEEvent("message", {"x": 1}))
    for q in adapter._sse_clients["tok"]:
        assert (await q.get()).data["x"] == 1


@pytest.mark.asyncio
async def test_push_media_per_queue_independent_token(monkeypatch):
    """每队列铸独立 token：N 队列 → 每队列恰好 1 条媒体事件。"""
    adapter = _make_adapter()
    q1, q2 = asyncio.Queue(maxsize=10), asyncio.Queue(maxsize=10)
    adapter._sse_clients["tok"] = [q1, q2]
    mint_count = {"n": 0}

    async def fake_media_url(comp):
        mint_count["n"] += 1
        return f"https://dash/api/file/t{mint_count['n']}"

    adapter._serializer._media_url = fake_media_url

    chain = MessageChain([Image.fromFileSystem("/x.png")])
    await adapter._push_media(chain, "tok", "mid1")

    e1 = await q1.get()
    e2 = await q2.get()
    assert e1.data["type"] == "image"
    assert e2.data["type"] == "image"
    # 两队列拿到不同 token URL
    assert e1.data["content"] != e2.data["content"]
    assert mint_count["n"] == 2   # 每队列铸一次


@pytest.mark.asyncio
async def test_push_media_skips_when_no_url(monkeypatch):
    adapter = _make_adapter()
    q = asyncio.Queue(maxsize=10)
    adapter._sse_clients["tok"] = [q]

    async def no_url(comp):
        return None

    adapter._serializer._media_url = no_url
    chain = MessageChain([Image.fromFileSystem("/x.png")])
    await adapter._push_media(chain, "tok", "mid1")
    assert q.empty()   # 无 URL 则不投递


@pytest.mark.asyncio
async def test_push_media_ignores_plain():
    adapter = _make_adapter()
    q = asyncio.Queue(maxsize=10)
    adapter._sse_clients["tok"] = [q]
    chain = MessageChain([Plain("text")])
    await adapter._push_media(chain, "tok", "mid1")
    assert q.empty()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_adapter_core.py -v`
Expected: FAIL（ModuleNotFoundError / AttributeError）

- [ ] **Step 3: 写实现（adapter.py 核心部分；run/terminate/send_by_session 在后续任务补）**

```python
# adapter.py
import asyncio
import time
import uuid
from collections import defaultdict
from pathlib import Path

from astrbot.api.platform import (register_platform_adapter, Platform, PlatformMetadata,
    AstrBotMessage, MessageMember, MessageType)
from astrbot.api.event import MessageChain
from astrbot.core import astrbot_config

from .models import BotApiConfig, SSEEvent
from .serializer import MessageSerializer
from .runtime import runtime


@register_platform_adapter(
    "botapi",
    "BotAPI 自定义移动端适配器 — 一人一 Bot 极简移动端接入，支持弱网断连恢复",
    default_config_tmpl={"host": "0.0.0.0", "port": 9000, "tokens": []},
    config_metadata={
        "host":   {"description": "监听地址", "type": "string", "hint": "0.0.0.0"},
        "port":   {"description": "监听端口", "type": "int", "hint": "9000"},
        "tokens": {"description": "允许的 Token 列表（空则允许所有非空 token）",
                   "type": "list", "items": {"type": "string"}},
    },
    adapter_display_name="BotAPI 移动端",
    support_streaming_message=True,
)
class BotApiAdapter(Platform):
    def __init__(self, platform_config: dict, platform_settings: dict, event_queue: asyncio.Queue) -> None:
        super().__init__(platform_config, event_queue)
        self.settings = platform_settings
        self.cfg = BotApiConfig(**platform_config)
        self.platform_id = self.meta().id
        self._token_to_origin: dict = {}
        self._sse_clients: dict = defaultdict(list)
        self._disabled_tokens: set = set()
        self._last_active: dict = {}
        self._uploaded_files: dict = {}
        self._upload_dir = Path(astrbot_config.get("data_path", "./data")) / "botapi_uploads"
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._shutdown = asyncio.Event()
        self._media_enabled = bool(astrbot_config.get("callback_api_base"))
        self._serializer = MessageSerializer(_media_enabled=self._media_enabled)
        runtime().adapter = self
        # self._setup_routes()  # 在 Task 9+ 引入 routes 后启用

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            name="botapi",
            description="BotAPI 自定义移动端适配器",
            id=self.config.get("id", "botapi"),
            adapter_display_name="BotAPI 移动端",
            support_streaming_message=True,
            support_proactive_message=True,
        )

    # ── 非阻塞 SSE 投递（spec §4.2）──
    def _put(self, q: asyncio.Queue, evt):
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(evt)
            except Exception:
                pass

    async def _broadcast_to(self, token: str, evt: SSEEvent):
        for q in list(self._sse_clients.get(token, [])):
            self._put(q, evt)

    async def _push_media(self, chain, token: str, message_id: str):
        if chain is None:
            return
        queues = list(self._sse_clients.get(token, []))
        for comp in (chain.chain or []):
            ct = comp.type.value.lower() if hasattr(comp.type, "value") else str(comp.type).lower()
            if ct not in ("image", "record", "file"):
                continue
            mtype = {"image": "image", "record": "audio", "file": "file"}[ct]
            for q in queues:   # 每队列铸独立 token
                url = await self._serializer._media_url(comp)
                if not url:
                    continue
                data = {"message_id": message_id, "type": mtype,
                        "content": ({"name": getattr(comp, "name", "file"), "url": url}
                                    if mtype == "file" else url),
                        "streaming": False, "final": False, "timestamp": int(time.time())}
                self._put(q, SSEEvent("message", data))
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_adapter_core.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add adapter.py tests/test_adapter_core.py
git commit -m "feat(adapter): __init__/meta + 非阻塞 _put/_broadcast_to/_push_media"
```

---

## Task 7: event.py（send + send_streaming）

**Files:**
- Create: `event.py`
- Create: `tests/test_event.py`

- [ ] **Step 1: 写失败测试（fake adapter + fake persist）**

```python
# tests/test_event.py
import asyncio
from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot_plugin_botapi.event import BotApiMessageEvent
from astrbot_plugin_botapi.models import SSEEvent


def _setup(monkeypatch, mid="m1", token="tok"):
    received = []

    class FakeAdapter:
        platform_id = "botapi"
        _sse_clients = {token: []}

        async def _broadcast_to(self, t, evt):
            received.append(evt)

        async def _push_media(self, chain, t, m):
            received.append(SSEEvent("message", {"_push_media": m}))

    class FakeSerializer:
        async def serialize_chain(self, message, event):
            return {"message_id": mid, "role": "assistant", "type": "text",
                    "content": message.get_plain_text(), "timestamp": 0}

    persisted = []

    async def fake_persist_text(token, mid, text, kind):
        persisted.append((kind, text))

    async def fake_persist_thinking(token, mid, text):
        persisted.append(("thinking", text))

    import astrbot_plugin_botapi.event as evmod
    monkeypatch.setattr(evmod, "persist_assistant_text", fake_persist_text)
    monkeypatch.setattr(evmod, "persist_assistant_thinking", fake_persist_thinking)

    adapter = FakeAdapter()
    adapter._serializer = FakeSerializer()

    msg_obj = SimpleNamespace(message_id=mid, sender=SimpleNamespace(user_id=token))
    event = BotApiMessageEvent(
        message_str="hi", message_obj=msg_obj,
        platform_meta=SimpleNamespace(id="botapi"), session_id=token, adapter=adapter)
    return event, received, persisted


@pytest.mark.asyncio
async def test_send_tool_call_status(monkeypatch):
    event, received, persisted = _setup(monkeypatch)
    chain = MessageChain()
    chain.type = "tool_call"
    chain.message("🔨 调用工具: web_search")
    await event.send(chain)
    assert received[0].event_type == "message"
    assert received[0].data["subtype"] == "tool_status"
    assert "web_search" in received[0].data["content"]
    assert received[0].data["final"] is False
    assert ("tool_status", received[0].data["content"]) in persisted


@pytest.mark.asyncio
async def test_send_normal_reply(monkeypatch):
    event, received, persisted = _setup(monkeypatch)
    chain = MessageChain([Plain("hello answer")])
    await event.send(chain)
    # 文本 final + push_media + persist final
    finals = [e for e in received if e.event_type == "message" and e.data.get("final")]
    assert len(finals) == 1
    assert finals[0].data["content"] == "hello answer"
    assert ("final", "hello answer") in persisted


@pytest.mark.asyncio
async def test_send_none_guard(monkeypatch):
    event, received, persisted = _setup(monkeypatch)
    await event.send(None)   # 不应崩
    assert received == []
    assert persisted == []


@pytest.mark.asyncio
async def test_send_streaming_sequence(monkeypatch):
    event, received, persisted = _setup(monkeypatch)

    async def gen():
        r = MessageChain([Plain("思考中")])
        r.type = "reasoning"
        yield r
        yield MessageChain([Plain("答案")])   # type 默认 None → plain 增量

    await event.send_streaming(gen())
    types = [e.event_type for e in received]
    assert "thinking" in types
    finals = [e for e in received if e.event_type == "message" and e.data.get("final")]
    assert len(finals) == 1
    assert finals[0].data["content"] == "答案"
    assert ("final", "答案") in persisted
    assert ("thinking", "思考中") in persisted


@pytest.mark.asyncio
async def test_send_streaming_none_and_break(monkeypatch):
    event, received, persisted = _setup(monkeypatch)

    async def gen():
        yield None
        b = MessageChain([]); b.type = "break"; yield b
        t = MessageChain([Plain("x")]); yield t

    await event.send_streaming(gen())   # 不崩；break 切段；最终 final "x"
    finals = [e for e in received if e.event_type == "message" and e.data.get("final")]
    assert finals and finals[0].data["content"] == "x"
```

> 注：测试里 `MessageChain([Plain("答案")])` 的写法以 astrbot 真实构造为准；若构造签名不同，调整为 `MessageChain(chain=[...])`。`gen2` 中 `t.type` 默认 None 即 plain。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_event.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 写实现**

```python
# event.py
import time

from astrbot.api.platform import AstrMessageEvent
from astrbot.api.event import MessageChain

from .models import SSEEvent
from .history import persist_assistant_text, persist_assistant_thinking

TOOL_STATUS_TYPE = "tool_call"   # 仅"🔨 调用工具"状态文本；tool_direct_result 走普通回复


class BotApiMessageEvent(AstrMessageEvent):
    def __init__(self, message_str, message_obj, platform_meta, session_id, adapter):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.adapter = adapter
        self.token = message_obj.sender.user_id
        self._text_buf: list = []

    async def _broadcast(self, evt: SSEEvent):
        await self.adapter._broadcast_to(self.token, evt)

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
        await self.adapter._push_media(message, self.token, mid)
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
            await self.adapter._push_media(chain, self.token, mid)
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
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_event.py -v`
Expected: PASS（5 passed）。若 `MessageChain([...])` 构造签名与预期不同（如需关键字 `chain=`），按报错调整测试构造。

- [ ] **Step 5: 提交**

```bash
git add event.py tests/test_event.py
git commit -m "feat(event): BotApiMessageEvent send/send_streaming（tool_status/流式/None 守卫）"
```

---

## Task 8: history.py persist（_insert + persist_*）

**Files:**
- Modify: `history.py`
- Create: `tests/test_history_persist.py`

- [ ] **Step 1: 写失败测试（fake message_history_manager）**

```python
# tests/test_history_persist.py
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi import history


class FakePMH:
    def __init__(self):
        self.inserted = []

    async def insert(self, platform_id, user_id, content, sender_id=None,
                     sender_name=None, llm_checkpoint_id=None):
        self.inserted.append({"platform_id": platform_id, "user_id": user_id,
                              "content": content, "sender_id": sender_id})
        return SimpleNamespace(id=len(self.inserted), content=content)


@pytest.mark.asyncio
async def test_persist_inbound_text(monkeypatch):
    fake_pmh = FakePMH()
    fake_rt = SimpleNamespace(message_history_manager=fake_pmh,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    await history.persist_inbound_text("tok", "m1", "hello")
    assert len(fake_pmh.inserted) == 1
    c = fake_pmh.inserted[0]["content"]
    assert c["role"] == "user" and c["text"] == "hello"
    assert fake_pmh.inserted[0]["platform_id"] == "botapi"


@pytest.mark.asyncio
async def test_persist_assistant_final(monkeypatch):
    fake_pmh = FakePMH()
    fake_rt = SimpleNamespace(message_history_manager=fake_pmh,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    await history.persist_assistant_text("tok", "m2", "answer", kind="final")
    c = fake_pmh.inserted[0]["content"]
    assert c["kind"] == "final" and c["text"] == "answer"


@pytest.mark.asyncio
async def test_persist_skips_empty(monkeypatch):
    fake_pmh = FakePMH()
    fake_rt = SimpleNamespace(message_history_manager=fake_pmh,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    await history.persist_assistant_text("tok", "m3", "", kind="final")
    await history.persist_inbound_text("tok", "m3", "")
    assert fake_pmh.inserted == []


@pytest.mark.asyncio
async def test_persist_thinking(monkeypatch):
    fake_pmh = FakePMH()
    fake_rt = SimpleNamespace(message_history_manager=fake_pmh,
                              adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)

    await history.persist_assistant_thinking("tok", "m4", "reasoning")
    assert fake_pmh.inserted[0]["content"]["kind"] == "thinking"


@pytest.mark.asyncio
async def test_persist_noop_when_no_manager(monkeypatch):
    fake_rt = SimpleNamespace(message_history_manager=None, adapter=SimpleNamespace(platform_id="botapi"))
    monkeypatch.setattr(history, "runtime", lambda: fake_rt)
    await history.persist_assistant_text("tok", "m5", "x", kind="final")   # 不崩
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_history_persist.py -v`
Expected: FAIL（`persist_inbound_text` 未定义）

- [ ] **Step 3: 扩展 history.py（追加 persist 部分）**

在 `history.py` 末尾追加：

```python
async def _insert(content, user_id, sender_id, sender_name):
    rt = runtime()
    if not (rt.message_history_manager and rt.adapter):
        return
    await rt.message_history_manager.insert(
        platform_id=rt.adapter.platform_id, user_id=user_id, content=content,
        sender_id=sender_id, sender_name=sender_name)


async def persist_inbound_text(token, message_id, text):
    if not text:
        return
    await _insert({"role": "user", "kind": "user", "text": text, "message_id": message_id},
                  token, token, "User")


async def persist_assistant_text(token, message_id, text, kind: str):  # kind: final/tool_status
    if not text:
        return
    await _insert({"role": "assistant", "kind": kind, "text": text, "message_id": message_id},
                  token, "bot", "BotAPI")


async def persist_assistant_thinking(token, message_id, text):
    if not text:
        return
    await _insert({"role": "assistant", "kind": "thinking", "text": text, "message_id": message_id},
                  token, "bot", "BotAPI")
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_history_persist.py tests/test_history_pure.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add history.py tests/test_history_persist.py
git commit -m "feat(history): persist_inbound_text/assistant_text/thinking 镜像写入"
```

---

## Task 9: routes.py /message + auth 中间件 + _file_info_to_component

**Files:**
- Create: `routes.py`
- Modify: `adapter.py`（`__init__` 调 `self._setup_routes()`）
- Create: `tests/test_routes_message.py`

> routes 用 Quart test app 测。adapter 持 `self.app = Quart(...)`，`_setup_routes` 在 adapter 上定义（从 routes.py 导入辅助）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_routes_message.py
import asyncio
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter


def _make_adapter_with_app(monkeypatch):
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.cfg = SimpleNamespace(host="127.0.0.1", port=9000, tokens=["secret-tok"])
    adapter._disabled_tokens = set()
    adapter._last_active = {}
    adapter._uploaded_files = {}
    adapter._sse_clients = {}
    adapter._media_enabled = True
    adapter._serializer = SimpleNamespace()
    adapter.platform_id = "botapi"
    adapter.client_self_id = "selfid"
    # 不真正 commit_event / set_extra：用桩
    committed = []
    adapter.commit_event = lambda e: committed.append(e)
    extras = {}
    # _setup_routes 会注册路由
    from astrbot_plugin_botapi import routes as routes_mod
    routes_mod._setup_routes(adapter)
    adapter._committed = committed
    return adapter


@pytest.mark.asyncio
async def test_message_requires_auth(monkeypatch):
    adapter = _make_adapter_with_app(monkeypatch)
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/message", json={"text": "hi"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_message_returns_message_id_only(monkeypatch):
    adapter = _make_adapter_with_app(monkeypatch)
    import astrbot_plugin_botapi.history as hist
    monkeypatch.setattr(hist, "persist_inbound_text", lambda *a, **k: _async_none())
    client = adapter.app.test_client()
    r = await client.post("/api/v1/botapi/message",
                          json={"text": "hi"},
                          headers={"Authorization": "Bearer secret-tok"})
    assert r.status_code == 200
    body = await r.get_json()
    assert "message_id" in body
    assert "reply" not in body   # 纯 SSE：无同步 reply
    assert len(adapter._committed) == 1   # commit_event 被调
    # set_extra enable_streaming 被设
    evt = adapter._committed[0]
    assert evt.get_extra("enable_streaming") is True


async def _async_none():
    return None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes_message.py -v`
Expected: FAIL（routes 模块/`_setup_routes` 未定义）

- [ ] **Step 3: 写 routes.py**

```python
# routes.py
import time
import uuid

from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
from astrbot.api.message_components import Image, Record, File
from quart import jsonify, request

from .event import BotApiMessageEvent
from .history import persist_inbound_text
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
            from astrbot.api.message_components import Plain
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

    # /upload /stream /history 在后续任务追加


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
```

并在 `adapter.py` 的 `__init__` 末尾把注释 `# self._setup_routes()` 改为：

```python
        from .routes import _setup_routes
        self._setup_routes = lambda: _setup_routes(self)
        self._setup_routes()
```

并在 `__init__` 里加 `self.app`（若 Task 6 未加）：

```python
        from quart import Quart
        self.app = Quart("astrbot_plugin_botapi")
```

> 注：Task 6 的 `__init__` 暂未建 `self.app`；本任务补上。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_routes_message.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add routes.py adapter.py tests/test_routes_message.py
git commit -m "feat(routes): /auth + /message（纯 SSE）+ auth 中间件 + 文件组件"
```

---

## Task 10: routes.py /upload

**Files:**
- Modify: `routes.py`
- Create: `tests/test_routes_upload.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_routes_upload.py
import io
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi import routes as routes_mod


def _make_adapter(tmp_path, monkeypatch):
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.cfg = SimpleNamespace(host="127.0.0.1", port=9000, tokens=["tok"])
    adapter._disabled_tokens = set(); adapter._last_active = {}
    adapter._uploaded_files = {}
    adapter._upload_dir = tmp_path; adapter._media_enabled = True
    adapter._serializer = SimpleNamespace(); adapter.platform_id = "botapi"
    adapter._sse_clients = {}; adapter.client_self_id = "selfid"
    adapter._token_to_origin = {}
    adapter.commit_event = lambda e: None
    from quart import Quart
    adapter.app = Quart("t")
    routes_mod._setup_routes(adapter)
    return adapter


@pytest.mark.asyncio
async def test_upload_returns_file_info(tmp_path, monkeypatch):
    adapter = _make_adapter(tmp_path, monkeypatch)
    client = adapter.app.test_client()
    data = {"file": (io.BytesIO(b"hello bytes"), "photo.jpg")}
    r = await client.post("/api/v1/botapi/upload", data=data,
                          headers={"Authorization": "Bearer tok", "Content-Type": "multipart/form-data"})
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
    r = await client.post("/api/v1/botapi/upload", data={},
                          headers={"Authorization": "Bearer tok", "Content-Type": "multipart/form-data"})
    assert r.status_code == 400
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes_upload.py -v`
Expected: FAIL（/upload 路由未定义）

- [ ] **Step 3: 在 routes.py `_setup_routes` 内追加 /upload**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_routes_upload.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add routes.py tests/test_routes_upload.py
git commit -m "feat(routes): /upload 文件上传"
```

---

## Task 11: routes.py /stream（SSE handler）

**Files:**
- Modify: `routes.py`
- Create: `tests/test_routes_stream.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_routes_stream.py
import asyncio
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi import routes as routes_mod
from astrbot_plugin_botapi.models import SSEEvent


def _make_adapter(monkeypatch):
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.cfg = SimpleNamespace(host="127.0.0.1", port=9000, tokens=["tok"])
    adapter._disabled_tokens = set(); adapter._last_active = {}
    adapter._uploaded_files = {}; adapter._sse_clients = {}
    adapter._media_enabled = True; adapter._serializer = SimpleNamespace()
    adapter.platform_id = "botapi"; adapter.client_self_id = "selfid"
    adapter._token_to_origin = {}; adapter.commit_event = lambda e: None
    from quart import Quart
    adapter.app = Quart("t")
    routes_mod._setup_routes(adapter)
    return adapter


@pytest.mark.asyncio
async def test_stream_registers_queue_and_sends_ping(monkeypatch):
    adapter = _make_adapter(monkeypatch)
    client = adapter.app.test_client()
    # 不带 since，连上后无消息→30s 内应 ping（测试里用 monkeypatch 缩短 timeout 困难；
    # 改测：连接建立后队列被注册；推送一个事件能被收到；断开后队列被注销）
    import astrbot_plugin_botapi.routes as r
    # 直接测队列注册逻辑：用内部辅助更稳——这里改为测 _broadcast_to 经 /stream 通路（集成）
    # 简化为：触发 /stream 后立即 _broadcast_to，再取消连接
    gen_task = asyncio.create_task(_drain(client))
    await asyncio.sleep(0.2)   # 让 /stream 建立并注册队列
    assert len(adapter._sse_clients.get("tok", [])) == 1
    q = adapter._sse_clients["tok"][0]
    await q.put(SSEEvent("message", {"message_id": "m1", "content": "hi", "final": True}))
    await asyncio.sleep(0.2)
    gen_task.cancel()
    try:
        await gen_task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.1)
    # finally 注销队列
    assert adapter._sse_clients.get("tok", []) == []


async def _drain(client):
    async with client.request("GET", "/api/v1/botapi/stream",
                              headers={"Authorization": "Bearer tok"}) as resp:
        async for _ in resp.body:
            pass   # 消费直到取消
```

> 注：SSE 集成测试对时序敏感；若 flaky，改为直接测 `_setup_routes` 注册的 handler 生成器（提取生成器为可单测函数）。本任务保留集成测，重点断言"队列注册 + finally 注销"。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes_stream.py -v`
Expected: FAIL（/stream 路由未定义）

- [ ] **Step 3: 在 routes.py `_setup_routes` 内追加 /stream**

```python
    @app.get("/api/v1/botapi/stream")
    async def stream():
        from quart import make_response
        from . import history as hist_mod
        token = _extract_token(adapter)
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        adapter._sse_clients[token].append(q)
        since = request.args.get("since")

        async def gen():
            try:
                if since:
                    for evt in await hist_mod.catchup_events(adapter.platform_id, token, since):
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

        resp = await make_response(gen(), {
            "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
            "Connection": "keep-alive", "Transfer-Encoding": "chunked",
            "X-Accel-Buffering": "no",
        })
        resp.timeout = None
        return resp
```

并在 routes.py 顶部 `import asyncio`（若未导入）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_routes_stream.py -v`
Expected: PASS。若 flaky，提高 sleep 或改单测生成器。

- [ ] **Step 5: 提交**

```bash
git add routes.py tests/test_routes_stream.py
git commit -m "feat(routes): /stream SSE（catchup + ping + finally 注销 + 非阻塞队列）"
```

---

## Task 12: routes.py /history

**Files:**
- Modify: `routes.py`
- Create: `tests/test_routes_history.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_routes_history.py
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi import routes as routes_mod
from astrbot_plugin_botapi import history as hist


@pytest.mark.asyncio
async def test_history_endpoint(monkeypatch):
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter.cfg = SimpleNamespace(host="127.0.0.1", port=9000, tokens=["tok"])
    adapter._disabled_tokens = set(); adapter._last_active = {}; adapter._uploaded_files = {}
    adapter._sse_clients = {}; adapter._media_enabled = True; adapter._serializer = SimpleNamespace()
    adapter.platform_id = "botapi"; adapter.client_self_id = "selfid"
    adapter._token_to_origin = {}; adapter.commit_event = lambda e: None
    from quart import Quart
    adapter.app = Quart("t")
    routes_mod._setup_routes(adapter)

    async def fake_get_history(pid, token, since=None, before=None, limit=50):
        return ([{"message_id": "1", "type": "text", "content": "a"}], False)
    monkeypatch.setattr(hist, "get_history", fake_get_history)

    client = adapter.app.test_client()
    r = await client.get("/api/v1/botapi/history?since=0&limit=50",
                         headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    body = await r.get_json()
    assert body["messages"][0]["content"] == "a"
    assert body["has_more"] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes_history.py -v`
Expected: FAIL（/history 路由未定义）

- [ ] **Step 3: 在 routes.py `_setup_routes` 内追加 /history**

```python
    @app.get("/api/v1/botapi/history")
    async def get_history():
        from . import history as hist_mod
        token = _extract_token(adapter)
        since = request.args.get("since")
        before = request.args.get("before")
        limit = min(int(request.args.get("limit", 50)), 200)
        msgs, has_more = await hist_mod.get_history(adapter.platform_id, token, since, before, limit)
        return jsonify({"messages": msgs, "has_more": has_more})
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_routes_history.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add routes.py tests/test_routes_history.py
git commit -m "feat(routes): /history 端点"
```

---

## Task 13: adapter.py run/terminate + send_by_session

**Files:**
- Modify: `adapter.py`
- Create: `tests/test_adapter_lifecycle.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_adapter_lifecycle.py
import asyncio
from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot_plugin_botapi.adapter import BotApiAdapter
from astrbot_plugin_botapi.models import SSEEvent


def _make_adapter():
    adapter = BotApiAdapter.__new__(BotApiAdapter)
    adapter._sse_clients = {"tok": [asyncio.Queue(maxsize=10)]}
    adapter._shutdown = asyncio.Event()
    adapter._media_enabled = True
    adapter._serializer = SimpleNamespace()
    adapter.platform_id = "botapi"
    return adapter


@pytest.mark.asyncio
async def test_terminate_sends_none_sentinel():
    adapter = _make_adapter()
    await adapter.terminate()
    q = adapter._sse_clients["tok"][0]
    item = await q.get()
    assert item is None   # 哨兵


@pytest.mark.asyncio
async def test_send_by_session_pushes_to_queue(monkeypatch):
    adapter = _make_adapter()
    # send_by_session 依赖 _broadcast_to + _push_media + serialize_chain
    pushed = []

    async def bcast(token, evt):
        adapter._put(adapter._sse_clients[token][0], evt)

    async def push_media(chain, token, mid):
        pushed.append(mid)

    adapter._broadcast_to = bcast
    adapter._push_media = push_media

    class FakeSer:
        async def serialize_chain(self, mc, event):
            return {"message_id": None, "role": "assistant", "type": "text",
                    "content": mc.get_plain_text(), "timestamp": 0}
    adapter._serializer = FakeSer()

    session = SimpleNamespace(session_id="tok")
    mc = MessageChain([Plain("主动通知")])
    await adapter.send_by_session(session, mc)
    q = adapter._sse_clients["tok"][0]
    evt = await q.get()
    assert evt.data["content"] == "主动通知"
    assert evt.data["final"] is True
    assert pushed  # _push_media 被调
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_adapter_lifecycle.py -v`
Expected: FAIL（terminate/send_by_session 未定义）

- [ ] **Step 3: 在 adapter.py BotApiAdapter 内追加**

```python
    def run(self):
        return self.app.run_task(host=self.cfg.host, port=self.cfg.port,
                                 shutdown_trigger=self._shutdown.wait)

    async def terminate(self) -> None:
        self._shutdown.set()
        for token, queues in list(self._sse_clients.items()):
            for q in queues:
                self._put(q, None)

    async def send_by_session(self, session, message_chain) -> None:
        await super().send_by_session(session, message_chain)
        token = session.session_id
        mid = f"botapi_proactive_{uuid.uuid4().hex[:12]}"
        payload = await self._serializer.serialize_chain(message_chain, None)
        await self._broadcast_to(token, SSEEvent("message", {**payload, "streaming": False, "final": True}))
        await self._push_media(message_chain, token, mid)
```

> `send_by_session` 调 `super().send_by_session(...)`（基类 metrics）。基类签名 `(self, session, message_chain)`（`platform.py:133-144`）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_adapter_lifecycle.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add adapter.py tests/test_adapter_lifecycle.py
git commit -m "feat(adapter): run/terminate/send_by_session"
```

---

## Task 14: main.py BotApiStar（注册管理 API + 注入 runtime）

**Files:**
- Create: `main.py`
- Create: `tests/test_star.py`

> `main.py` 既 `from .adapter import BotApiAdapter`（触发装饰器注册），又定义 `BotApiStar`。

- [ ] **Step 1: 写失败测试（用 fake Context）**

```python
# tests/test_star.py
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar


def test_star_registers_web_apis_and_injects_runtime():
    registered = []

    class FakeContext:
        conversation_manager = "CM"
        message_history_manager = "PMH"
        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, tuple(methods), desc))

    # BotApiStar.__init__(context, config=None)
    star = BotApiStar(FakeContext(), None)
    from astrbot_plugin_botapi.runtime import runtime
    rt = runtime()
    assert rt.conversation_manager == "CM"
    assert rt.message_history_manager == "PMH"
    # 注册的路由
    routes = {r for r, _, _ in registered}
    assert "/astrbot_plugin_botapi/stats" in routes
    assert "/astrbot_plugin_botapi/accounts" in routes
    assert "/astrbot_plugin_botapi/accounts/<token_hash>/delete" in routes
    assert "/astrbot_plugin_botapi/sessions/<token_hash>/disconnect" in routes
    # 全 GET/POST，无 DELETE/PATCH
    for _, methods, _ in registered:
        assert set(methods) <= {"GET", "POST"}
    # 清理 runtime
    rt.conversation_manager = None
    rt.message_history_manager = None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_star.py -v`
Expected: FAIL（main.py 未定义 BotApiStar）

- [ ] **Step 3: 写 main.py（Star 部分；handler 实现在 Task 15）**

```python
# main.py
from astrbot.api.star import Star, Context

from .adapter import BotApiAdapter   # 触发 @register_platform_adapter 注册到 platform_cls_map
from .runtime import runtime
from . import routes as _routes  # noqa: 保证模块加载


class BotApiStar(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        rt = runtime()
        rt.context = context
        rt.conversation_manager = context.conversation_manager
        rt.message_history_manager = context.message_history_manager
        P = "astrbot_plugin_botapi"
        context.register_web_api(f"/{P}/stats",    self._stats,    ["GET"],  "统计")
        context.register_web_api(f"/{P}/accounts", self._accounts, ["GET"],  "账户列表")
        context.register_web_api(f"/{P}/accounts", self._create,   ["POST"], "新增账户")
        context.register_web_api(f"/{P}/accounts/<token_hash>/delete", self._delete, ["POST"], "删除账户")
        context.register_web_api(f"/{P}/accounts/<token_hash>/status", self._toggle, ["POST"], "启停账户")
        context.register_web_api(f"/{P}/sessions/<token_hash>/disconnect", self._disconnect, ["POST"], "断开会话")
        context.register_web_api(f"/{P}/sessions/<token_hash>/clear", self._clear, ["POST"], "清空历史")

    # handler 实现在 Task 15（同文件追加）
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_star.py -v`
Expected: PASS。若 `super().__init__(context, config)` 因 Star 基类签名差异报错，按报错调整（基类 `Star.__init__(self, context, config=None)`）。

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_star.py
git commit -m "feat(star): BotApiStar 注册管理 API + 注入 runtime"
```

---

## Task 15: admin handlers + 账户持久化（核实开放点 4）

**Files:**
- Modify: `main.py`
- Create: `tests/test_admin_handlers.py`

> 含 `_stats/_accounts/_create/_delete/_toggle/_disconnect/_clear`。账户持久化用全局 `astrbot_config`（开放点 4：先核实 `adapter.config` 是否为全局子树共享引用）。

- [ ] **Step 1: 先核实开放点 4（adapter.config 与全局 astrbot_config 关系）**

Run:
```bash
cd /home/zzt/workspace/AstrBot && python -c "
from astrbot.core import astrbot_config
print(type(astrbot_config))
print(hasattr(astrbot_config, 'save_config'))
print('platform' in astrbot_config)
"
```
记录：`astrbot_config` 类型、是否有 `save_config`、`platform` 子树结构。据此确定 `_create`/`_delete` 写法（直接 mutate `adapter.config['tokens']` 若为共享引用，否则改全局 `astrbot_config['platform']` 子树）。

- [ ] **Step 2: 写失败测试（fake adapter + fake astrbot_config）**

```python
# tests/test_admin_handlers.py
import json
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar


def _fake_context():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()
        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, handler, methods, desc))
    return FakeContext(), registered


def _make_star(monkeypatch, tokens=None, platforms=None):
    ctx, registered = _fake_context()
    star = BotApiStar(ctx, None)
    # 注入 fake adapter
    adapter = SimpleNamespace(
        cfg=SimpleNamespace(tokens=list(tokens or [])),
        config={"id": "botapi", "tokens": list(tokens or [])},
        platform_id="botapi",
        _sse_clients={}, _disabled_tokens=set(), _last_active={})
    from astrbot_plugin_botapi import runtime as rt_mod
    rt = rt_mod.runtime()
    rt.adapter = adapter
    # fake astrbot_config
    fake_cfg = {"platform": list(platforms or [{"id": "botapi", "tokens": list(tokens or [])}])}

    class FakeAstrbotConfig:
        def __getitem__(self, k): return fake_cfg[k]
        def get(self, k, d=None): return fake_cfg.get(k, d)
        def save_config(self): fake_cfg["_saved"] = True
    import astrbot_plugin_botapi.main as main_mod
    monkeypatch.setattr(main_mod, "astrbot_config", FakeAstrbotConfig())
    return star, adapter, fake_cfg, registered


@pytest.mark.asyncio
async def test_create_account_persists(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=[])
    # 模拟 handler 调用（star._create 是 async，需 quart request 上下文）
    # 直接测内部逻辑：用 helper 拆出 _do_create(token)
    token = await star._do_create("newtok")
    assert token == "newtok"
    assert "newtok" in adapter.config["tokens"]
    assert "newtok" in adapter.cfg.tokens
    assert fake_cfg.get("_saved") is True   # save_config 被调
    # 全局子树也更新
    assert "newtok" in fake_cfg["platform"][0]["tokens"]


@pytest.mark.asyncio
async def test_delete_account(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a", "b"])
    await star._do_delete("a")
    assert "a" not in adapter.config["tokens"]
    assert "a" not in adapter.cfg.tokens
    assert fake_cfg.get("_saved") is True


@pytest.mark.asyncio
async def test_toggle_disable(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a"])
    await star._do_toggle("a", disabled=True)
    assert "a" in adapter._disabled_tokens


def test_stats_envelope(monkeypatch):
    star, adapter, fake_cfg, _ = _make_star(monkeypatch, tokens=["a", "b"])
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(star._do_stats())
    assert result["status"] == "ok"
    assert result["data"]["total_accounts"] == 2
```

> 测试用 `_do_create/_do_delete/_do_toggle/_do_stats` 等"纯逻辑"helper（handler 内调用它们），避免依赖 quart request 上下文。handler（`_create` 等）在 §Step 3 里写成"取参→调 helper→返回 Response"。

- [ ] **Step 3: 在 main.py BotApiStar 内追加 handler + helper**

```python
import hashlib, json, uuid
from astrbot.dashboard.routes.route import Response
from astrbot.core import astrbot_config as _cfg_singleton   # 全局单例（spec §9.3）
from quart import request


def _hash(t): return hashlib.sha256(t.encode()).hexdigest()[:16]
def _preview(t): return f"{t[:8]}...{t[-4:]}" if len(t) > 16 else t


class BotApiStar(Star):
    # ... __init__ 见 Task 14 ...

    async def _do_stats(self):
        from .runtime import runtime
        adapter = runtime().adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        pid = adapter.platform_id
        per = []
        for token in adapter.cfg.tokens or []:
            umo = f"{pid}:FriendMessage:{token}"
            msg_count = 0
            try:
                cid = await runtime().conversation_manager.get_curr_conversation_id(umo)
                if cid:
                    conv = await runtime().conversation_manager.get_conversation(umo, cid)
                    if conv and conv.history:
                        msg_count = len(json.loads(conv.history))
            except Exception:
                pass
            per.append({"token_preview": _preview(token), "token_hash": _hash(token),
                        "online": bool(adapter._sse_clients.get(token)),
                        "sse_connections": len(adapter._sse_clients.get(token, [])),
                        "message_count": msg_count,
                        "last_active": adapter._last_active.get(token)})
        return Response().ok({"total_accounts": len(per),
            "total_online": sum(1 for a in per if a["online"]),
            "total_messages": sum(a["message_count"] for a in per),
            "per_account": per}).__dict__

    def _persist_tokens(self, adapter, new_tokens):
        """改全局 astrbot_config 子树 + 同步运行时副本 + 落盘（开放点 4 核实后可简化）。"""
        # 1. 改全局 astrbot_config['platform'] 中本平台子树
        for p in _cfg_singleton.get("platform", []):
            if p.get("id") == adapter.config.get("id"):
                p["tokens"] = list(new_tokens)
                break
        # 2. 同步运行时副本
        adapter.config["tokens"] = list(new_tokens)
        adapter.cfg.tokens = list(new_tokens)
        # 3. 落盘
        _cfg_singleton.save_config()

    async def _do_create(self, token=None):
        from .runtime import runtime
        adapter = runtime().adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        token = token or uuid.uuid4().hex[:16]
        toks = list(adapter.config.get("tokens", []))
        if token not in toks:
            toks.append(token)
            self._persist_tokens(adapter, toks)
        return Response().ok({"token": token, "message": "账户创建成功"}).__dict__

    async def _do_delete(self, token_hash):
        from .runtime import runtime
        adapter = runtime().adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next((t for t in adapter.config.get("tokens", []) if _hash(t) == token_hash), None)
        if not target:
            return Response().error("未找到账户").__dict__
        toks = [t for t in adapter.config.get("tokens", []) if t != target]
        self._persist_tokens(adapter, toks)
        for q in adapter._sse_clients.pop(target, []):
            adapter._put(q, None)
        adapter._token_to_origin.pop(target, None) if hasattr(adapter, "_token_to_origin") else None
        return Response().ok({"message": "账户已删除"}).__dict__

    async def _do_toggle(self, token_hash, disabled):
        from .runtime import runtime
        adapter = runtime().adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next((t for t in (adapter.cfg.tokens or []) if _hash(t) == token_hash), None)
        if not target:
            return Response().error("未找到账户").__dict__
        if disabled:
            adapter._disabled_tokens.add(target)
            for q in adapter._sse_clients.pop(target, []):
                adapter._put(q, None)
        else:
            adapter._disabled_tokens.discard(target)
        return Response().ok({"message": "状态已更新"}).__dict__

    async def _do_disconnect(self, token_hash):
        from .runtime import runtime
        adapter = runtime().adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next((t for t in (adapter.cfg.tokens or []) if _hash(t) == token_hash), None)
        if not target:
            return Response().error("未找到会话").__dict__
        from .models import SSEEvent
        for q in adapter._sse_clients.pop(target, []):
            adapter._put(q, SSEEvent("error", {"code": "SESSION_KICKED", "message": "管理员已断开此会话"}))
        return Response().ok({"message": f"会话已断开"}).__dict__

    async def _do_clear(self, token_hash):
        from .runtime import runtime
        adapter = runtime().adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        target = next((t for t in (adapter.cfg.tokens or []) if _hash(t) == token_hash), None)
        if not target:
            return Response().error("未找到会话").__dict__
        umo = f"{adapter.platform_id}:FriendMessage:{target}"
        await runtime().conversation_manager.new_conversation(umo)
        return Response().ok({"message": "历史已清除"}).__dict__

    # ── register_web_api 注册的 handler（薄封装：取参→调 _do_*→返回）──
    async def _stats(self):
        return await self._do_stats()

    async def _accounts(self):
        from .runtime import runtime
        adapter = runtime().adapter
        if not adapter:
            return Response().error("适配器未就绪").__dict__
        accs = [{"token_preview": _preview(t), "token_hash": _hash(t),
                 "enabled": t not in adapter._disabled_tokens,
                 "online": bool(adapter._sse_clients.get(t)),
                 "sse_connections": len(adapter._sse_clients.get(t, [])),
                 "last_active": adapter._last_active.get(t)} for t in (adapter.cfg.tokens or [])]
        return Response().ok({"accounts": accs, "total": len(accs)}).__dict__

    async def _create(self):
        data = await request.get_json()
        token = (data or {}).get("token")
        return await self._do_create(token)

    async def _delete(self, token_hash):
        return await self._do_delete(token_hash)

    async def _toggle(self, token_hash):
        data = await request.get_json()
        return await self._do_toggle(token_hash, disabled=bool((data or {}).get("disabled")))

    async def _disconnect(self, token_hash):
        return await self._do_disconnect(token_hash)

    async def _clear(self, token_hash):
        return await self._do_clear(token_hash)
```

> 开放点 4 核实结论若表明 `adapter.config` 是全局子树共享引用，则 `_persist_tokens` 可简化为直接 `adapter.config["tokens"] = list(new_tokens)` + `adapter.cfg.tokens = list(new_tokens)` + `_cfg_singleton.save_config()`（删掉遍历 `astrbot_config['platform']` 那段）。按 Step 1 核实结果调整。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_admin_handlers.py tests/test_star.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_admin_handlers.py
git commit -m "feat(admin): stats/accounts/create/delete/toggle/disconnect/clear + 账户持久化"
```

---

## Task 16: 管理页前端（pages/dashboard）

**Files:**
- Create: `pages/dashboard/index.html`
- Create: `pages/dashboard/app.js`
- Create: `pages/dashboard/style.css`

> 前端无单测（纯静态）；用 spec §5.4 沿用 + 改 `apiPost` + envelope 解包。验收靠 Task 19 烟测。

- [ ] **Step 1: 写 index.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>BotAPI 管理面板</title>
  <link rel="stylesheet" href="./style.css" />
</head>
<body>
  <div id="app">
    <div class="stats-row">
      <div class="stat-card"><span class="stat-value" id="total-accounts">-</span><span class="stat-label">总账户</span></div>
      <div class="stat-card online"><span class="stat-value" id="online-count">-</span><span class="stat-label">在线</span></div>
      <div class="stat-card"><span class="stat-value" id="total-messages">-</span><span class="stat-label">总消息数</span></div>
    </div>
    <div class="toolbar">
      <button id="btn-add" class="btn btn-primary">+ 新增账户</button>
      <button id="btn-refresh" class="btn btn-secondary">刷新</button>
    </div>
    <div class="table-container">
      <table>
        <thead><tr><th>Token</th><th>ID(hash)</th><th>状态</th><th>消息</th><th>SSE</th><th>最后活跃</th><th>操作</th></tr></thead>
        <tbody id="account-list"><tr class="empty-row"><td colspan="7">加载中...</td></tr></tbody>
      </table>
    </div>
    <div id="modal-add" class="modal hidden">
      <div class="modal-content">
        <h3>新增 BotAPI 账户</h3>
        <label>Token（留空自动生成）:<input type="text" id="input-token" /></label>
        <div class="modal-actions">
          <button id="btn-create" class="btn btn-primary">创建</button>
          <button id="btn-cancel" class="btn btn-secondary">取消</button>
        </div>
      </div>
    </div>
  </div>
  <script type="module" src="./app.js"></script>
</body>
</html>
```

- [ ] **Step 2: 写 app.js（bridge.apiPost，envelope 解包由 bridge 自动完成）**

```js
const bridge = window.AstrBotPluginPage;
let accounts = [];

async function init() {
  await bridge.ready();
  await refresh();
  setupEventListeners();
}

async function refresh() {
  try {
    const stats = await bridge.apiGet("stats");   // bridge 解包 .data
    accounts = stats.per_account || [];
    document.getElementById("total-accounts").textContent = stats.total_accounts ?? "-";
    document.getElementById("online-count").textContent = stats.total_online ?? "-";
    document.getElementById("total-messages").textContent = stats.total_messages ?? "-";
    renderAccounts();
  } catch (err) { console.error("刷新失败:", err); }
}

function renderAccounts() {
  const tbody = document.getElementById("account-list");
  if (!accounts.length) { tbody.innerHTML = '<tr class="empty-row"><td colspan="7">暂无账户</td></tr>'; return; }
  tbody.innerHTML = accounts.map(a => `
    <tr>
      <td><code>${esc(a.token_preview)}</code></td>
      <td><code>${esc(a.token_hash)}</code></td>
      <td><span class="badge ${a.online ? 'badge-online' : 'badge-offline'}">${a.online ? '在线' : '离线'}</span></td>
      <td>${a.message_count ?? 0}</td>
      <td>${a.sse_connections || 0}</td>
      <td>${a.last_active ? new Date(a.last_active * 1000).toLocaleString('zh-CN') : '-'}</td>
      <td><button class="btn btn-sm btn-danger" onclick="deleteAccount('${esc(a.token_hash)}')">删除</button></td>
    </tr>`).join('');
}

function setupEventListeners() {
  document.getElementById("btn-add").addEventListener("click", () => document.getElementById("modal-add").classList.remove("hidden"));
  document.getElementById("btn-cancel").addEventListener("click", () => document.getElementById("modal-add").classList.add("hidden"));
  document.getElementById("btn-refresh").addEventListener("click", refresh);
  document.getElementById("btn-create").addEventListener("click", async () => {
    const token = document.getElementById("input-token").value.trim();
    try {
      await bridge.apiPost("accounts", { token: token || undefined });
      document.getElementById("modal-add").classList.add("hidden");
      document.getElementById("input-token").value = "";
      await refresh();
    } catch (err) { alert("创建失败: " + err.message); }
  });
}

async function deleteAccount(tokenHash) {
  if (!confirm(`确定删除 ${tokenHash}？`)) return;
  await bridge.apiPost(`accounts/${tokenHash}/delete`, {});   // 用 apiPost（无 apiDelete）
  await refresh();
}
window.deleteAccount = deleteAccount;

function esc(s) { const d = document.createElement("div"); d.textContent = String(s); return d.innerHTML; }

init();
```

- [ ] **Step 3: 写 style.css（亮暗双主题，沿用 spec §5.4）**

```css
:root { --bg:#f5f5f5; --card-bg:#fff; --text:#1a1a1a; --text-secondary:#666; --border:#e0e0e0;
  --primary:#3b82f6; --primary-hover:#2563eb; --danger:#ef4444; --danger-hover:#dc2626;
  --online:#22c55e; --offline:#9ca3af; --modal-overlay:rgba(0,0,0,0.5); }
[data-theme="dark"] { --bg:#1a1a2e; --card-bg:#252540; --text:#e0e0e0; --text-secondary:#a0a0b0;
  --border:#3a3a5c; --primary:#60a5fa; --primary-hover:#3b82f6; --danger:#f87171; --danger-hover:#ef4444;
  --online:#4ade80; --offline:#6b7280; --modal-overlay:rgba(0,0,0,0.7); }
* { box-sizing:border-box; margin:0; padding:0; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; padding:24px; line-height:1.5; }
.stats-row { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:16px; margin-bottom:24px; }
.stat-card { background:var(--card-bg); border:1px solid var(--border); border-radius:12px; padding:20px; text-align:center; }
.stat-value { font-size:28px; font-weight:700; display:block; }
.stat-label { font-size:13px; color:var(--text-secondary); margin-top:4px; }
.stat-card.online .stat-value { color:var(--online); }
.toolbar { display:flex; gap:8px; margin-bottom:16px; }
.table-container { background:var(--card-bg); border:1px solid var(--border); border-radius:12px; overflow:hidden; margin-bottom:24px; }
table { width:100%; border-collapse:collapse; }
th,td { padding:10px 14px; text-align:left; font-size:13px; }
th { background:var(--bg); font-weight:600; color:var(--text-secondary); border-bottom:2px solid var(--border); }
td { border-bottom:1px solid var(--border); } tr:last-child td { border-bottom:none; }
.empty-row td { text-align:center; color:var(--text-secondary); padding:32px; }
code { background:var(--bg); padding:2px 6px; border-radius:4px; font-size:12px; font-family:"SF Mono",monospace; }
.badge { display:inline-block; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:500; }
.badge-online { background:#dcfce7; color:#166534; } .badge-offline { background:#f3f4f6; color:#6b7280; }
[data-theme="dark"] .badge-online { background:#166534; color:#dcfce7; } [data-theme="dark"] .badge-offline { background:#374151; color:#9ca3af; }
.btn { padding:8px 16px; border:none; border-radius:8px; font-size:13px; font-weight:500; cursor:pointer; transition:background 0.2s; }
.btn-primary { background:var(--primary); color:#fff; } .btn-primary:hover { background:var(--primary-hover); }
.btn-secondary { background:var(--bg); color:var(--text); border:1px solid var(--border); } .btn-secondary:hover { background:var(--border); }
.btn-danger { background:var(--danger); color:#fff; } .btn-danger:hover { background:var(--danger-hover); }
.btn-sm { padding:4px 12px; font-size:12px; }
.modal { position:fixed; inset:0; background:var(--modal-overlay); display:flex; align-items:center; justify-content:center; z-index:1000; }
.modal.hidden { display:none; }
.modal-content { background:var(--card-bg); border-radius:16px; padding:24px; width:100%; max-width:400px; box-shadow:0 8px 30px rgba(0,0,0,0.2); }
.modal-content h3 { margin-bottom:16px; }
.modal-content label { display:block; margin-bottom:16px; font-size:13px; color:var(--text-secondary); }
.modal-content input { display:block; width:100%; margin-top:6px; padding:8px 12px; border:1px solid var(--border); border-radius:8px; font-size:13px; background:var(--bg); color:var(--text); }
.modal-actions { display:flex; gap:8px; justify-content:flex-end; margin-top:16px; }
```

- [ ] **Step 4: 提交**

```bash
git add pages/dashboard/index.html pages/dashboard/app.js pages/dashboard/style.css
git commit -m "feat(pages): dashboard 管理页（bridge apiPost + envelope 解包）"
```

---

## Task 17: main.py 入口 wiring + 集成测试（send_streaming 全链路）

**Files:**
- Verify: `main.py`（Task 14/15 已含 `from .adapter import BotApiAdapter` + `BotApiStar`）
- Create: `tests/test_integration_streaming.py`

- [ ] **Step 1: 写集成测试（commit_event→send_streaming→SSE 队列）**

```python
# tests/test_integration_streaming.py
import asyncio
from types import SimpleNamespace

import pytest

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot_plugin_botapi.event import BotApiMessageEvent
from astrbot_plugin_botapi import history as hist


@pytest.mark.asyncio
async def test_full_streaming_chain_to_sse(monkeypatch):
    received = []

    class FakeAdapter:
        platform_id = "botapi"
        _sse_clients = {"tok": []}

        async def _broadcast_to(self, t, evt):
            for q in self._sse_clients[t]:
                self._put(q, evt)

        def _put(self, q, evt):
            q.put_nowait(evt)

        async def _push_media(self, chain, t, mid):
            pass

        class _S:
            _media_enabled = False
            async def serialize_chain(self, mc, event):
                mid = event.message_obj.message_id if event else None
                return {"message_id": mid, "role": "assistant", "type": "text",
                        "content": mc.get_plain_text(), "timestamp": 0}

    adapter = FakeAdapter()
    adapter._serializer = FakeAdapter._S()
    q = asyncio.Queue(maxsize=100)
    adapter._sse_clients["tok"] = [q]

    persisted = []
    monkeypatch.setattr(hist, "persist_assistant_text",
                        lambda t, mid, text, kind: _ap(persisted.append, (kind, text)))
    monkeypatch.setattr(hist, "persist_assistant_thinking",
                        lambda t, mid, text: _ap(persisted.append, ("thinking", text)))

    msg_obj = SimpleNamespace(message_id="m1", sender=SimpleNamespace(user_id="tok"))
    event = BotApiMessageEvent(message_str="问", message_obj=msg_obj,
                               platform_meta=SimpleNamespace(id="botapi"),
                               session_id="tok", adapter=adapter)

    async def gen():
        r = MessageChain([Plain("思考中")]); r.type = "reasoning"; yield r
        yield MessageChain([Plain("答案")])

    await event.send_streaming(gen())

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    types = [e.event_type for e in events]
    assert "thinking" in types
    finals = [e for e in events if e.event_type == "message" and e.data.get("final")]
    assert len(finals) == 1
    assert finals[0].data["content"] == "答案"
    assert ("thinking", "思考中") in persisted
    assert ("final", "答案") in persisted


async def _ap(fn, args):
    fn(*args)
```

> 注：`persist_assistant_text`/`persist_assistant_thinking` 被 monkeypatch 为返回协程的 lambda（`await` 时执行 `persisted.append`）。

- [ ] **Step 2: 运行确认通过**

Run: `pytest tests/test_integration_streaming.py -v`
Expected: PASS

- [ ] **Step 3: 全量回归**

Run: `pytest -v`
Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add tests/test_integration_streaming.py
git commit -m "test: send_streaming 全链路集成测试"
```

---

## Task 18: 管理 API 集成测试（dashboard 路由匹配）

**Files:**
- Create: `tests/test_admin_routing.py`

> 用 AstrBot 的 dashboard 测试客户端模式（对照 `tests/test_dashboard.py`）。若该 fixture 不可用，降级为单测 `register_web_api` 注册元组。

- [ ] **Step 1: 写测试**

```python
# tests/test_admin_routing.py
from types import SimpleNamespace

import pytest

from astrbot_plugin_botapi.main import BotApiStar


def test_all_admin_routes_are_get_or_post():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()
        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, methods))

    BotApiStar(FakeContext(), None)
    assert len(registered) >= 7
    for route, methods in registered:
        assert set(methods) <= {"GET", "POST"}, f"{route} 含非 GET/POST: {methods}"


def test_routes_prefixed_with_plugin_name():
    registered = []

    class FakeContext:
        conversation_manager = SimpleNamespace()
        message_history_manager = SimpleNamespace()
        def register_web_api(self, route, handler, methods, desc):
            registered.append(route)

    BotApiStar(FakeContext(), None)
    for route in registered:
        assert route.startswith("/astrbot_plugin_botapi/"), f"{route} 缺插件名前缀"
```

- [ ] **Step 2: 运行确认通过**

Run: `pytest tests/test_admin_routing.py -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add tests/test_admin_routing.py
git commit -m "test: 管理 API 路由全 GET/POST + 插件名前缀"
```

---

## Task 19: AstrBot 烟测（加载插件 + 发消息 + SSE）

**Files:**
- 无新文件（手动/scripted 烟测）

> 把插件挂到 AstrBot 实例，验证端到端。

- [ ] **Step 1: 链接插件到 AstrBot data/plugins**

```bash
ln -sf /home/zzt/workspace/astrbot_plugin_botapi /home/zzt/workspace/AstrBot/data/plugins/astrbot_plugin_botapi
```

- [ ] **Step 2: 启动 AstrBot，确认插件加载**

Run: `cd /home/zzt/workspace/AstrBot && python main.py`（或项目惯用启动命令）
Expected: 日志无 "未通过 Star 注册"；WebUI 插件管理出现 `astrbot_plugin_botapi`。

- [ ] **Step 3: WebUI 创建平台实例并启用**

WebUI → 机器人/平台 → 新增 → 选 type=`botapi` → host=0.0.0.0/port=9000/tokens=["test-token"] → 启用。
配置 `callback_api_base`（仪表盘可达地址，如 `http://localhost:6185`）。

- [ ] **Step 4: 发消息 + 收 SSE**

```bash
# 认证
curl -s -X POST http://localhost:9000/api/v1/botapi/auth -H "Content-Type: application/json" -d '{"token":"test-token"}'
# 发消息（纯 SSE：只返回 message_id）
curl -s -X POST http://localhost:9000/api/v1/botapi/message -H "Authorization: Bearer test-token" -H "Content-Type: application/json" -d '{"text":"你好"}'
# 收 SSE（流式 thinking→增量→final）
curl -N http://localhost:9000/api/v1/botapi/stream -H "Authorization: Bearer test-token"
```
Expected: `/auth` 返回 `{user_id, session_id}`；`/message` 返回 `{message_id}`（无 reply）；`/stream` 推送 `thinking`/`message(streaming)`/`message(final)` 事件。

- [ ] **Step 5: 验证断连补消息**

```bash
# 记下 final 的 message_id（或 /history 的最大 id），断开 /stream，再发一条消息，重连 ?since=<id>
curl -s "http://localhost:9000/api/v1/botapi/history?since=0" -H "Authorization: Bearer test-token"
curl -N "http://localhost:9000/api/v1/botapi/stream?since=<上次的id>" -H "Authorization: Bearer test-token"
```
Expected: 重连后补推漏掉的文本消息。

- [ ] **Step 6: 验证管理页**

WebUI → 插件管理 → astrbot_plugin_botapi → Dashboard 页面：显示账户/在线/消息数；新增/删除账户生效（刷新后持久化）。

- [ ] **Step 7: 提交烟测记录**

```bash
git commit --allow-empty -m "test: AstrBot 端到端烟测通过（加载/发消息/SSE/补消息/管理页）"
```

---

## Self-Review

**1. Spec coverage：** 逐条对照 spec 章节——
- §2 架构（Platform+Star+RuntimeState）：Task 3/6/14 ✓
- §4 适配器（装饰器/3参/__init__/meta/run/terminate/send_by_session/_put/_broadcast_to/_push_media）：Task 6/13 ✓
- §5 事件（send/send_streaming/set_extra）：Task 7/9 ✓
- §6 手机 API（auth/message/upload/stream/history）：Task 9/10/11/12 ✓
- §7 序列化器（serialize_chain/_media_url）：Task 5 ✓
- §8 历史（persist_*/get_history/catchup）：Task 4/8 ✓
- §9 管理页（Star/handler/persistence/pages）：Task 14/15/16 ✓
- §10 配置（metadata/callback_api_base/两端口）：Task 1/19 ✓
- §11 错误处理（非阻塞队列/None 守卫/媒体 per-queue）：Task 6/7/11 ✓
- §12 开放点：开放点 1（set_extra）Task 7 已用；开放点 4（adapter.config）Task 15 Step 1 核实；开放点 2/3/5 标注为后续 ✓

**2. Placeholder scan：** 无 TBD/TODO/"add error handling" 等占位符；每个代码步骤均含完整代码。✓

**3. Type consistency：** `SSEEvent`、`BotApiConfig`、`RuntimeState`、`row_to_sse`、`persist_assistant_text(token,mid,text,kind)`、`_push_media(chain,token,mid)`、`_broadcast_to(token,evt)`、`_put(q,evt)`、`_do_create/_do_delete/_do_toggle/_do_stats` 在各任务签名一致。`MessageSerializer(_media_enabled=)` 构造在 Task 5/6 一致。✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-24-botapi-astrbot-plugin.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
