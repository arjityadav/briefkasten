"""Provider-agnostic LLM interface."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal, Protocol


class LLMError(RuntimeError):
    """Raised when a provider call fails or is misconfigured."""


@dataclass
class Image:
    media_type: str  # image/png, image/jpeg, image/webp
    data: bytes


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolSpec:
    """A tool the model may call. input_schema is a JSON Schema object."""

    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """Provider-neutral conversation turn.

    user: text from the caller; assistant: model text plus tool calls; tool: results for those calls.
    """

    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning: str = ""  # separate reasoning channel (e.g. gpt-oss on Groq); shown in traces, never sent back
    rejected: str = ""  # the provider refused the model's tool call (server-side schema check); why


class LLM(Protocol):
    model: str

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        agent: str,
        max_tokens: int = 2048,
        images: list[Image] | None = None,
    ) -> LLMResult: ...

    def chat(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        agent: str,
        max_tokens: int = 2048,
    ) -> ChatResult: ...


Tier = Literal["fast", "smart", "vision"]


@dataclass
class LLMRouter:
    """Model routing: cheap model for classification and critique, stronger model for reasoning."""

    smart: LLM
    fast: LLM
    vision: LLM | None = None  # for scans and photos; defaults to the smart model

    def for_tier(self, tier: Tier) -> LLM:
        if tier == "vision":
            return self.vision or self.smart
        return self.fast if tier == "fast" else self.smart


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (tolerates code fences and stray prose)."""
    t = text.strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(t[start : end + 1])
