"""Anthropic Messages API provider."""

from __future__ import annotations

import base64

from .base import ChatResult, Image, LLMError, LLMResult, Message, ToolCall, ToolSpec
from .http import post_json


class AnthropicLLM:
    def __init__(self, model: str, api_key: str | None, base_url: str = "https://api.anthropic.com"):
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set (or use BRIEFKASTEN_PROVIDER=openai with a local model)")
        self.model = model
        self._key = api_key
        self._url = base_url.rstrip("/") + "/v1/messages"

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        agent: str,
        max_tokens: int = 2048,
        images: list[Image] | None = None,
    ) -> LLMResult:
        content: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.media_type,
                    "data": base64.b64encode(img.data).decode("ascii"),
                },
            }
            for img in images or []
        ]
        content.append({"type": "text", "text": prompt})
        resp = post_json(
            self._url,
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": content}],
                "metadata": {"user_id": f"briefkasten-{agent}"},
            },
            {"x-api-key": self._key, "anthropic-version": "2023-06-01"},
        )
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        usage = resp.get("usage", {})
        return LLMResult(
            text=text,
            model=resp.get("model", self.model),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    def chat(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        agent: str,
        max_tokens: int = 2048,
    ) -> ChatResult:
        resp = post_json(
            self._url,
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [_to_anthropic(m) for m in messages],
                "tools": [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools],
                "metadata": {"user_id": f"briefkasten-{agent}"},
            },
            {"x-api-key": self._key, "anthropic-version": "2023-06-01"},
        )
        blocks = resp.get("content", [])
        usage = resp.get("usage", {})
        return ChatResult(
            text="".join(b.get("text", "") for b in blocks if b.get("type") == "text"),
            tool_calls=[ToolCall(b["id"], b["name"], b.get("input") or {}) for b in blocks if b.get("type") == "tool_use"],
            model=resp.get("model", self.model),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )


def _to_anthropic(m: Message) -> dict:
    if m.role == "tool":
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
                for r in m.results
            ],
        }
    if m.role == "assistant":
        content: list[dict] = [{"type": "text", "text": m.text}] if m.text else []
        content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in m.tool_calls]
        return {"role": "assistant", "content": content or [{"type": "text", "text": "(no output)"}]}  # API rejects empty turns
    return {"role": "user", "content": [{"type": "text", "text": m.text}]}
