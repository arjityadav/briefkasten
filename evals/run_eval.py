"""Evaluate Briefkasten against hand-labelled sample letters with a real model.

    BRIEFKASTEN_PROVIDER=anthropic ANTHROPIC_API_KEY=... python evals/run_eval.py
    BRIEFKASTEN_PROVIDER=openai BRIEFKASTEN_MODEL=qwen2.5:14b python evals/run_eval.py --repeats 3
    uv run python evals/run_eval.py --only mahnbescheid      # Groq settings come from .env

Scores per document, for whatever its gold file specifies: sender and document type, letter date,
every gold deadline (matched by date), amounts to pay, payslip/contract/form analysis fields, and
whether all code checks passed, plus tokens and latency.
Writes evals/results-<timestamp>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from briefkasten.config import Settings  # noqa: E402
from briefkasten.demo import DEMO_REDACT_TERMS, SAMPLES  # noqa: E402
from briefkasten.llm import build_router  # noqa: E402
from briefkasten.observability import observer  # noqa: E402
from briefkasten.orchestrator import Briefkasten  # noqa: E402

TODAY = date(2026, 10, 28)  # fixed so "days left" is reproducible


def score(state, gold: dict) -> dict:
    """Checks only what the gold file specifies, so letters, payslips, contracts and forms share one scorer."""
    s: dict[str, bool] = {}
    t, x = state.triage, state.extraction
    for key in ("sender_type", "doc_type"):
        if key in gold:
            s[key] = bool(t) and getattr(t, key).value == gold[key]
    if "letter_date" in gold:
        s["letter_date"] = bool(x) and str(x.letter_date) == gold["letter_date"]
    found = {str(d.due_date) for d in state.deadlines}
    for kind, due in gold.get("due_dates", {}).items():
        s[f"deadline:{kind}"] = due in found  # matched by date: the label a model picks matters less than the day
    if gold.get("amounts_you_pay"):
        pays = {round(a.value, 2) for a in (x.amounts if x else []) if a.direction == "you_pay"}
        s["amounts_you_pay"] = set(gold["amounts_you_pay"]) <= pays  # itemised extra amounts are fine
    if want := gold.get("analysis"):
        a = state.analysis
        s["analysis:family"] = bool(a) and a.family == want["family"]
        for key in ("gross", "net", "payout"):
            if key in want:
                s[f"analysis:{key}"] = bool(a) and abs((getattr(a, key, None) or 0) - want[key]) < 0.01
        if "end_date" in want:
            s["analysis:end_date"] = bool(a) and str(getattr(a, "end_date", None)) == want["end_date"]
        if "min_fields" in want:
            s["analysis:fields"] = bool(a) and len(getattr(a, "fields", [])) >= want["min_fields"]
    s["checks_passed"] = bool(state.verification) and state.verification.passed
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--only", help="substring filter on sample names")
    args = ap.parse_args()

    settings = Settings.from_env(redact_terms=DEMO_REDACT_TERMS, data_dir=Path(tempfile.mkdtemp()))
    bk = Briefkasten(build_router(settings), settings)
    rows = []
    # With Langfuse on, every run of this eval is one trace in this session, scored per check.
    session = f"eval-{datetime.now():%Y%m%d-%H%M%S}-{settings.model.replace('/', '_')}"
    for gold_path in sorted((SAMPLES / "gold").glob("*.json")):
        if args.only and args.only not in gold_path.stem:
            continue
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        letter = (SAMPLES / "letters" / gold["letter"]).read_text(encoding="utf-8")
        bk.settings.state = gold.get("bundesland", "BW")
        for i in range(args.repeats):
            t0 = time.perf_counter()
            received = date.fromisoformat(gold["received_on"]) if gold.get("received_on") else None
            state = bk.process(letter, source_name=gold["letter"], today=TODAY, received_on=received, session_id=session)
            checks = score(state, gold) if state.status != "failed" else {"run_succeeded": False}
            rows.append(
                {
                    "sample": gold_path.stem,
                    "repeat": i,
                    "status": state.status,
                    "error": state.error,
                    "checks": checks,
                    "seconds": round(time.perf_counter() - t0, 1),
                    "tokens": sum(e.input_tokens + e.output_tokens for e in state.trace),
                    "extraction_rounds": state.verification.extraction_rounds if state.verification else None,
                }
            )
            for check, passed in checks.items():
                observer().score(state.trace_id, f"eval:{check}", float(passed))
            observer().score(state.trace_id, "eval:score", sum(checks.values()) / max(len(checks), 1))
            ok = sum(checks.values())
            print(f"{gold_path.stem:<28} #{i}  {ok}/{len(checks)} checks  {rows[-1]['seconds']:>5}s  "
                  f"{rows[-1]['tokens']:>6} tok  " + " ".join(k for k, v in checks.items() if not v))

    total = sum(len(r["checks"]) for r in rows)
    passed = sum(sum(r["checks"].values()) for r in rows)
    print(f"\nOverall: {passed}/{total} checks ({passed / max(total, 1):.0%}) · model {settings.model} / {settings.model_fast}")
    out = Path(__file__).parent / f"results-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"model": settings.model, "model_fast": settings.model_fast, "rows": rows}, indent=2, default=str))
    print(f"Saved {out}")
    if observer().enabled:
        observer().flush()
        print(f"Langfuse session: {session} (filter traces by this session to compare runs)")


if __name__ == "__main__":
    main()
