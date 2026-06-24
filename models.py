import json
from dataclasses import dataclass, field


@dataclass
class BotApiConfig:
    host: str = "0.0.0.0"
    port: int = 9000
    tokens: list = field(default_factory=list)
    nicknames: dict = field(default_factory=dict)   # {token: 昵称}，仅管理展示用，不注入对话


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
