"""Deterministic stand-in for an LLM: replays canned responses per agent.

Used by the test suite and by `briefkasten demo`, so the whole pipeline
(redaction, routing, deadline maths, verification, approval) runs without an API key.
"""

from __future__ import annotations

import json
import threading

from .base import Image, LLMError, LLMResult


class ScriptedLLM:
    def __init__(self, responses: dict[str, list[str | dict]], model: str = "scripted-replay"):
        self.model = model
        self._queues = {agent: list(items) for agent, items in responses.items()}
        self._lock = threading.Lock()
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        agent: str,
        max_tokens: int = 2048,
        images: list[Image] | None = None,
    ) -> LLMResult:
        with self._lock:
            self.calls.append({"agent": agent, "system": system, "prompt": prompt})
            queue = self._queues.get(agent)
            if not queue:
                raise LLMError(f"ScriptedLLM has no response for agent '{agent}'")
            item = queue.pop(0) if len(queue) > 1 else queue[0]  # the last response repeats
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return LLMResult(text=text, model=self.model, input_tokens=len(system + prompt) // 4, output_tokens=len(text) // 4)
