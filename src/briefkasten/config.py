"""Settings, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


# provider -> (smart, fast, vision) defaults; vision None means the smart model reads images
DEFAULT_MODELS: dict[str, tuple[str, str, str | None]] = {
    "anthropic": ("claude-sonnet-5-5", "claude-haiku-5-5", None),
    # Qwen for every role: parallel tool calls, reliable tool-call format, and vision. gpt-oss on Groq can't make
    # parallel tool calls and often calls a non-existent "json" tool, which stalls the agent loops.
    "groq": ("qwen/qwen3.8-27b", "qwen/qwen3.8-27b", "qwen/qwen3.8-27b"),
    "openai": ("llama3.1", "llama3.1", None),
}


def _infer_provider(env) -> str:
    """Pick a provider from whichever credentials are present when BRIEFKASTEN_PROVIDER is unset."""
    if env.get("GROQ_API_KEY"):
        return "groq"
    if env.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if env.get("OPENAI_API_KEY") or env.get("OPENAI_BASE_URL"):
        return "openai"
    return "anthropic"


def _split_terms(value: str) -> list[str]:
    return [t.strip() for t in value.split(";") if t.strip()]


@dataclass
class Settings:
    provider: str = "anthropic"  # anthropic | groq | openai
    model: str = "claude-sonnet-5-5"  # used by reasoning-heavy agents
    model_fast: str = "claude-haiku-5-5"  # used by triage and the critic
    model_vision: str | None = None  # cloud model for scans and photos (image_reader=cloud); None means `model`
    image_reader: str = "ocr"  # ocr | local_vision | cloud: who reads scans and photos (see readers.py)
    vision_base_url: str = "http://localhost:11434/v1"  # local OpenAI-compatible server for image_reader=local_vision
    model_vision_local: str = "qwen2.5vl:7b"
    anthropic_api_key: str | None = None
    groq_api_key: str | None = None
    openai_base_url: str = "http://localhost:11434/v1"  # Ollama by default
    openai_api_key: str | None = None
    state: str = "BW"  # Bundesland, decides which public holidays apply
    data_dir: Path = Path(".briefkasten")
    redact_terms: list[str] = field(default_factory=list)
    auto_redact: bool = True  # also detect unlisted names, street addresses and birthdates
    sender_name: str = ""
    sender_address: str = ""
    max_extraction_retries: int = 1
    demo_only: bool = False  # public demo: sample letters only, no uploads, nothing stored on disk
    imprint_url: str = ""  # Impressum, linked in the web app footer
    privacy_url: str = ""  # privacy policy (Datenschutzerklärung), linked in the footer

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        load_dotenv()
        env = os.environ
        provider = env.get("BRIEFKASTEN_PROVIDER") or _infer_provider(env)
        groq_key = env.get("GROQ_API_KEY")
        if provider == "openai" and "api.groq.com" in env.get("OPENAI_BASE_URL", ""):
            # Groq configured through the generic OPENAI_* variables: use the Groq provider and its tuning.
            provider, groq_key = "groq", groq_key or env.get("OPENAI_API_KEY")
        default_model, default_fast, default_vision = DEFAULT_MODELS.get(provider, DEFAULT_MODELS["openai"])
        model = env.get("BRIEFKASTEN_MODEL") or env.get("OPENAI_MODEL")
        # Without an explicit fast model, Groq keeps its small model; elsewhere the chosen model does both jobs.
        fast_fallback = default_fast if provider == "groq" else (model or default_fast)
        s = cls(
            provider=provider,
            model=model or default_model,
            model_fast=env.get("BRIEFKASTEN_MODEL_FAST") or fast_fallback,
            model_vision=env.get("BRIEFKASTEN_MODEL_VISION", default_vision),
            anthropic_api_key=env.get("ANTHROPIC_API_KEY"),
            groq_api_key=groq_key,
            openai_base_url=env.get("OPENAI_BASE_URL", "http://localhost:11434/v1"),
            openai_api_key=env.get("OPENAI_API_KEY"),
            state=env.get("BRIEFKASTEN_STATE", "BW").upper(),
            data_dir=Path(env.get("BRIEFKASTEN_DATA_DIR", ".briefkasten")),
            redact_terms=_split_terms(env.get("BRIEFKASTEN_REDACT_TERMS", "")),
            auto_redact=env.get("BRIEFKASTEN_AUTO_REDACT", "1").strip().lower() not in ("0", "false", "no", "off"),
            sender_name=env.get("BRIEFKASTEN_SENDER_NAME", ""),
            sender_address=env.get("BRIEFKASTEN_SENDER_ADDRESS", "").replace("\\n", "\n"),
            image_reader=env.get("BRIEFKASTEN_IMAGE_READER", "ocr").strip().lower(),
            vision_base_url=env.get("BRIEFKASTEN_VISION_BASE_URL", "http://localhost:11434/v1"),
            model_vision_local=env.get("BRIEFKASTEN_LOCAL_VISION_MODEL", "qwen2.5vl:7b"),
            demo_only=env.get("BRIEFKASTEN_DEMO_ONLY", "").strip().lower() in ("1", "true", "yes", "on"),
            imprint_url=env.get("BRIEFKASTEN_IMPRINT_URL", ""),
            privacy_url=env.get("BRIEFKASTEN_PRIVACY_URL", ""),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(s, key, value)
        return s
