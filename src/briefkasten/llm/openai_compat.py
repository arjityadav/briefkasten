"""OpenAI-compatible chat completions provider.

Works with Groq (BRIEFKASTEN_PROVIDER=groq), Ollama (default, fully local), vLLM, LM Studio,
OpenAI, and Gemini's OpenAI-compatible endpoint (https://generativelanguage.googleapis.com/v1beta/openai).
"""

from __future__ import annotations

import base64
import json
import re

from .base import ChatResult, Image, LLMError, LLMResult, Message, ToolCall, ToolSpec
from .http import post_json

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def groq_options(model: str) -> dict:
    """Per-model request tuning for Groq.

    gpt-oss models always reason, and their reasoning tokens count against the completion limit,
    so they get low effort plus headroom on top of each agent's answer budget. Qwen 3.x reasons
    only on request; transcription and these structured tasks don't need it.
    """
    if model.startswith("openai/gpt-oss"):
        return {"extra": {"reasoning_effort": "low"}, "token_headroom": 2048}
    if model.startswith("qwen/qwen3"):
        return {"extra": {"reasoning_effort": "none"}}
    return {}


class OpenAICompatLLM:
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None = None,
        *,
        extra: dict | None = None,
        token_headroom: int = 0,
    ):
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._extra = extra or {}  # provider-specific body fields, e.g. reasoning_effort
        self._headroom = token_headroom

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
                "max_tokens": max_tokens + self._headroom,
                "temperature": 0,
                **self._extra,
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

    def chat(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        agent: str,
        max_tokens: int = 2048,
    ) -> ChatResult:
        headers = {"authorization": f"Bearer {self._key}"} if self._key else {}
        wire: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            wire += _to_openai(m)
        try:
            resp = post_json(
                self._url,
                {
                    "model": self.model,
                    "max_tokens": max_tokens + self._headroom,
                    "temperature": 0,
                    **self._extra,
                    "messages": wire,
                    "tools": [
                        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
                        for t in tools
                    ],
                },
                headers,
            )
        except LLMError as e:
            # Groq validates tool calls server-side and answers 400 (tool_use_failed) when the model
            # produces an invalid one. Hand the reason back so the agent can correct the call.
            reason = _tool_call_rejection(str(e))
            if reason is None:
                raise
            return ChatResult(text="", tool_calls=[], model=self.model, rejected=reason)
        msg = ((resp.get("choices") or [{}])[0]).get("message") or {}
        usage = resp.get("usage") or {}
        calls = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparseable_arguments": fn.get("arguments")}  # surfaced to the model as a validation error
            calls.append(ToolCall(c.get("id") or f"call_{i}", fn.get("name", ""), args if isinstance(args, dict) else {}))
        return ChatResult(
            text=msg.get("content") or "",
            tool_calls=calls,
            model=resp.get("model", self.model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            reasoning=msg.get("reasoning") or "",
        )


def _tool_call_rejection(error: str) -> str | None:
    """The provider's explanation if this error is a rejected tool call, else None."""
    if "tool_use_failed" not in error and "tool call validation failed" not in error.lower():
        return None
    try:
        body = json.loads(error[error.index("{") :])
        return str(body["error"]["message"])[:1000]
    except (ValueError, KeyError, TypeError):
        # The body may be cut off (Groq echoes the whole failed generation); take the message field alone.
        m = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', error)
        return (m.group(1).encode().decode("unicode_escape", errors="replace") if m else error)[:1000]


def _to_openai(m: Message) -> list[dict]:
    if m.role == "tool":
        return [
            {"role": "tool", "tool_call_id": r.call_id, "content": f"ERROR: {r.content}" if r.is_error else r.content}
            for r in m.results
        ]
    if m.role == "assistant":
        out: dict = {"role": "assistant", "content": m.text or None}
        if m.tool_calls:
            out["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.input, ensure_ascii=False)}}
                for c in m.tool_calls
            ]
        return [out]
    return [{"role": "user", "content": m.text}]
