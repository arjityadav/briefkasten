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


def _split_terms(value: str) -> list[str]:
    return [t.strip() for t in value.split(";") if t.strip()]


@dataclass
class Settings:
    provider: str = "anthropic"  # anthropic | openai
    model: str = "claude-sonnet-5-5"  # used by reasoning-heavy agents
    model_fast: str = "claude-haiku-5-5"  # used by triage and the critic
    anthropic_api_key: str | None = None
    openai_base_url: str = "http://localhost:11434/v1"  # Ollama by default
    openai_api_key: str | None = None
    state: str = "BW"  # Bundesland, decides which public holidays apply
    data_dir: Path = Path(".briefkasten")
    redact_terms: list[str] = field(default_factory=list)
    sender_name: str = ""
    sender_address: str = ""
    max_extraction_retries: int = 1

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        load_dotenv()
        env = os.environ
        provider = env.get("BRIEFKASTEN_PROVIDER", "anthropic")
        default_model = "claude-sonnet-5-5" if provider == "anthropic" else "llama3.1"
        default_fast = "claude-haiku-5-5" if provider == "anthropic" else default_model
        s = cls(
            provider=provider,
            model=env.get("BRIEFKASTEN_MODEL", default_model),
            model_fast=env.get("BRIEFKASTEN_MODEL_FAST", env.get("BRIEFKASTEN_MODEL", default_fast)),
            anthropic_api_key=env.get("ANTHROPIC_API_KEY"),
            openai_base_url=env.get("OPENAI_BASE_URL", "http://localhost:11434/v1"),
            openai_api_key=env.get("OPENAI_API_KEY"),
            state=env.get("BRIEFKASTEN_STATE", "BW").upper(),
            data_dir=Path(env.get("BRIEFKASTEN_DATA_DIR", ".briefkasten")),
            redact_terms=_split_terms(env.get("BRIEFKASTEN_REDACT_TERMS", "")),
            sender_name=env.get("BRIEFKASTEN_SENDER_NAME", ""),
            sender_address=env.get("BRIEFKASTEN_SENDER_ADDRESS", "").replace("\\n", "\n"),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(s, key, value)
        return s
