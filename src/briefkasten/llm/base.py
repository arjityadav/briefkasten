"""Provider-agnostic LLM interface."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
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


Tier = Literal["fast", "smart"]


@dataclass
class LLMRouter:
    """Model routing: cheap model for classification and critique, stronger model for reasoning."""

    smart: LLM
    fast: LLM

    def for_tier(self, tier: Tier) -> LLM:
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
