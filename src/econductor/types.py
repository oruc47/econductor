from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Protocol


class Permission(StrEnum):
    APPROVE = "approve"
    READ_ONLY = "read-only"
    AUTONOMOUS = "autonomous"


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = ""


@dataclass
class ToolResult:
    ok: bool
    summary: str
    artifacts: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def json(self) -> dict:
        return asdict(self)


@dataclass
class Completion:
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0
    peak_memory_bytes: int = 0
    truncated: bool = False


class Inference(Protocol):
    def count(self, messages: list[dict], tools: list[dict]) -> int: ...
    def complete(self, messages: list[dict], tools: list[dict], on_text) -> Completion: ...
    def unload(self) -> None: ...
