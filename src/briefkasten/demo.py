"""Offline demo: replay recorded agent replies through the real pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from .llm import LLMRouter, ScriptedLLM

SAMPLES = Path(__file__).parent / "samples"
DEMO_REDACT_TERMS = ["Max Mustermann", "Erika Mustermann", "Mustermann", "Musterstraße 12"]


def available_replays() -> list[str]:
    return sorted(p.stem for p in (SAMPLES / "replays").glob("*.json"))


def load_replay(name: str) -> tuple[str, str | None, LLMRouter]:
    """Returns (letter text, reply intent, router) for a recorded sample."""
    path = SAMPLES / "replays" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"No replay '{name}'. Available: {', '.join(available_replays())}")
    spec = json.loads(path.read_text(encoding="utf-8"))
    letter = (SAMPLES / "letters" / spec["letter"]).read_text(encoding="utf-8")
    llm = ScriptedLLM(spec["responses"])
    return letter, spec.get("intent"), LLMRouter(smart=llm, fast=llm)
