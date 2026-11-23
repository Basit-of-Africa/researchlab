from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol


class LLMError(RuntimeError):
    """Base class for inference errors."""


class LLMUnavailable(LLMError):
    """No provider configured or reachable."""


class LLMQuotaExceeded(LLMError):
    """Provider quota / rate limit exhausted (free tiers)."""


class LLMBadOutput(LLMError):
    """Provider returned output that could not be parsed."""


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    raw: dict = field(default_factory=dict)

    def json(self) -> dict | list:
        return parse_json_loose(self.text)


class LLMProvider(Protocol):
    name: str
    model: str
    external: bool  # True if data leaves this machine

    def available(self) -> bool: ...

    def generate(
        self,
        messages: list[Message],
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResult: ...


def parse_json_loose(text: str) -> dict | list:
    """Parse JSON from model output, tolerating code fences and leading prose."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = t.find(open_c), t.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(t[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMBadOutput("Model output was not valid JSON")
