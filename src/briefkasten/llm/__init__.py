from __future__ import annotations

from ..config import Settings
from .base import (
    LLM,
    ChatResult,
    Image,
    LLMError,
    LLMResult,
    LLMRouter,
    Message,
    ToolCall,
    ToolResult,
    ToolSpec,
    extract_json,
)
from .scripted import ScriptedLLM

__all__ = [
    "LLM",
    "ChatResult",
    "Image",
    "LLMError",
    "LLMResult",
    "LLMRouter",
    "Message",
    "ScriptedLLM",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "build_router",
    "extract_json",
]


def build_router(settings: Settings) -> LLMRouter:
    if settings.provider == "anthropic":
        from .anthropic import AnthropicLLM

        return LLMRouter(
            smart=AnthropicLLM(settings.model, settings.anthropic_api_key),
            fast=AnthropicLLM(settings.model_fast, settings.anthropic_api_key),
            vision=AnthropicLLM(settings.model_vision, settings.anthropic_api_key) if settings.model_vision else None,
        )
    if settings.provider == "openai":
        from .openai_compat import OpenAICompatLLM

        def make(model: str) -> OpenAICompatLLM:
            return OpenAICompatLLM(model, settings.openai_base_url, settings.openai_api_key)

    elif settings.provider == "groq":
        from .openai_compat import GROQ_BASE_URL, OpenAICompatLLM, groq_options

        if not settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not set (create one at https://console.groq.com/keys)")

        def make(model: str) -> OpenAICompatLLM:
            return OpenAICompatLLM(model, GROQ_BASE_URL, settings.groq_api_key, **groq_options(model))

    else:
        raise LLMError(f"Unknown provider '{settings.provider}' (use 'anthropic', 'groq' or 'openai')")
    return LLMRouter(
        smart=make(settings.model),
        fast=make(settings.model_fast),
        vision=make(settings.model_vision) if settings.model_vision else None,
    )
