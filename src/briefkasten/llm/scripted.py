"""Deterministic stand-in for an LLM: replays canned turns per agent.

Used by the test suite and by `briefkasten demo`, so the whole system (supervisor decisions,
agent tool loops, redaction, deadline maths, verification, approval) runs without an API key.

Each agent has a queue of turns. A turn is either a string (the model answers in prose,
without calling a tool) or {"text": "...", "tool_calls": [{"name": ..., "input": {...}}]}.
The tools themselves always run live; only the model's choices are replayed.
"""

from __future__ import annotations

import json
import threading
import time

from .base import ChatResult, Image, LLMError, LLMResult, Message, ToolCall, ToolSpec


class ScriptedLLM:
    def __init__(self, responses: dict[str, list[str | dict]], model: str = "scripted-replay", delay: float = 0.0):
        self.model = model
        self.delay = delay  # seconds per call, so a replay can be watched live in the web UI
        self._queues = {agent: list(items) for agent, items in responses.items()}
        self._lock = threading.Lock()
        self._ids = 0
        self.calls: list[dict] = []

    def _next(self, agent: str, system: str, prompt: str) -> str | dict:
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.calls.append({"agent": agent, "system": system, "prompt": prompt})
            queue = self._queues.get(agent)
            if not queue:
                raise LLMError(f"ScriptedLLM has no response for agent '{agent}'")
            return queue.pop(0) if len(queue) > 1 else queue[0]  # the last response repeats

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        agent: str,
        max_tokens: int = 2048,
        images: list[Image] | None = None,
    ) -> LLMResult:
        item = self._next(agent, system, prompt)
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return LLMResult(text=text, model=self.model, input_tokens=len(system + prompt) // 4, output_tokens=len(text) // 4)

    def chat(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        agent: str,
        max_tokens: int = 2048,
    ) -> ChatResult:
        # Everything the model would see, flattened, so tests can assert on it.
        prompt = "\n".join([m.text for m in messages if m.text] + [r.content for m in messages for r in m.results])
        item = self._next(agent, system, prompt)
        if isinstance(item, str):
            text, raw_calls = item, []
        else:
            text, raw_calls = item.get("text", ""), item.get("tool_calls", [])
        offered = {t.name for t in tools}
        calls = []
        with self._lock:
            for c in raw_calls:
                if c["name"] not in offered:
                    raise LLMError(f"ScriptedLLM: agent '{agent}' was not offered tool '{c['name']}'")
                self._ids += 1
                calls.append(ToolCall(f"call_{self._ids}", c["name"], c.get("input", {})))
        out = text + json.dumps([c.input for c in calls], ensure_ascii=False)
        return ChatResult(
            text=text,
            tool_calls=calls,
            model=self.model,
            input_tokens=len(system + prompt) // 4,
            output_tokens=len(out) // 4,
        )
