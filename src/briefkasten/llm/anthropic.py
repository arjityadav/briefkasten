"""Anthropic Messages API provider."""

from __future__ import annotations

import base64

from .base import Image, LLMError, LLMResult
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
