"""Offline demo: replay recorded agent replies through the real pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from .llm import LLMRouter, ScriptedLLM

SAMPLES = Path(__file__).parent / "samples"
DEMO_REDACT_TERMS = ["Max Mustermann", "Erika Mustermann", "Mustermann", "Musterstraße 12"]
DEMO_DESCRIPTIONS = {
    "finanzamt_bescheid": {"title": "Tax assessment", "kind": "letter", "blurb": "Finanzamt wants 412 €. Deadlines, and an objection drafted for approval."},
    "vermieter_nebenkosten": {"title": "Service-charge bill", "kind": "letter", "blurb": "The extractor misreads an amount; the verifier catches it and the agent fixes it."},
    "gehaltsabrechnung": {"title": "Payslip", "kind": "payslip", "blurb": "Every line explained; code checks that gross → net → payout adds up."},
    "handyvertrag": {"title": "Phone contract", "kind": "contract", "blurb": "Clauses to watch out for, and the cancel-by date counted back from the term end."},
}


def available_replays() -> list[str]:
    return sorted(p.stem for p in (SAMPLES / "replays").glob("*.json"))


def load_replay(name: str, delay: float = 0.0) -> tuple[str, str | None, LLMRouter]:
    """Returns (letter text, reply intent, router) for a recorded sample; `delay` paces each model call."""
    path = SAMPLES / "replays" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"No replay '{name}'. Available: {', '.join(available_replays())}")
    spec = json.loads(path.read_text(encoding="utf-8"))
    letter = (SAMPLES / "letters" / spec["letter"]).read_text(encoding="utf-8")
    llm = ScriptedLLM(spec["responses"], delay=delay)
    return letter, spec.get("intent"), LLMRouter(smart=llm, fast=llm)
