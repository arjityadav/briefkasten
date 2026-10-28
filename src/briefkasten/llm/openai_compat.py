"""OpenAI-compatible chat completions provider.

Works with Ollama (default, fully local), vLLM, LM Studio, OpenAI, and Gemini's
OpenAI-compatible endpoint (https://generativelanguage.googleapis.com/v1beta/openai).
"""

from __future__ import annotations

import base64

from .base import Image, LLMResult
from .http import post_json


class OpenAICompatLLM:
    def __init__(self, model: str, base_url: str, api_key: str | None = None):
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        agent: str,
        max_tokens: int = 2048,
        images: list[Image] | None = None,
    ) -> LLMResult:
        if images:
            user_content: list[dict] | str = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{img.media_type};base64,{base64.b64encode(img.data).decode('ascii')}"},
                }
                for img in images
            ] + [{"type": "text", "text": prompt}]
        else:
            user_content = prompt
        headers = {"authorization": f"Bearer {self._key}"} if self._key else {}
        resp = post_json(
            self._url,
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
            },
            headers,
        )
        choice = (resp.get("choices") or [{}])[0]
        usage = resp.get("usage") or {}
        return LLMResult(
            text=(choice.get("message") or {}).get("content") or "",
            model=resp.get("model", self.model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )
