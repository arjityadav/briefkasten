from __future__ import annotations

from ..config import Settings
from .base import LLM, Image, LLMError, LLMResult, LLMRouter, extract_json
from .scripted import ScriptedLLM

__all__ = ["LLM", "Image", "LLMError", "LLMResult", "LLMRouter", "ScriptedLLM", "build_router", "extract_json"]


def build_router(settings: Settings) -> LLMRouter:
    if settings.provider == "anthropic":
        from .anthropic import AnthropicLLM

        return LLMRouter(
            smart=AnthropicLLM(settings.model, settings.anthropic_api_key),
            fast=AnthropicLLM(settings.model_fast, settings.anthropic_api_key),
        )
    if settings.provider == "openai":
        from .openai_compat import OpenAICompatLLM

        return LLMRouter(
            smart=OpenAICompatLLM(settings.model, settings.openai_base_url, settings.openai_api_key),
            fast=OpenAICompatLLM(settings.model_fast, settings.openai_base_url, settings.openai_api_key),
        )
    raise LLMError(f"Unknown provider '{settings.provider}' (use 'anthropic' or 'openai')")
