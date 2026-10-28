"""Evaluate Briefkasten against hand-labelled sample letters with a real model.

    BRIEFKASTEN_PROVIDER=anthropic ANTHROPIC_API_KEY=... python evals/run_eval.py
    BRIEFKASTEN_PROVIDER=openai BRIEFKASTEN_MODEL=qwen2.5:14b python evals/run_eval.py --repeats 3

Scores per letter: sender type, document type, letter date, every gold deadline
(matched by kind and date), amounts to pay, grounding pass, plus tokens and latency.
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
from briefkasten.orchestrator import Briefkasten  # noqa: E402

TODAY = date(2026, 10, 28)  # fixed so "days left" is reproducible


def score(state, gold: dict) -> dict:
    s: dict[str, bool] = {}
    t, x = state.triage, state.extraction
    s["sender_type"] = bool(t) and t.sender_type.value == gold["sender_type"]
    s["doc_type"] = bool(t) and t.doc_type.value == gold["doc_type"]
    s["letter_date"] = bool(x) and str(x.letter_date) == gold["letter_date"]
    found = {(d.kind, str(d.due_date)) for d in state.deadlines}
    for kind, due in gold["due_dates"].items():
        s[f"deadline:{kind}"] = (kind, due) in found
    pays = sorted(round(a.value, 2) for a in (x.amounts if x else []) if a.direction == "you_pay")
    s["amounts_you_pay"] = pays == sorted(gold["amounts_you_pay"])
    s["grounding_passed"] = bool(state.verification) and state.verification.passed
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--only", help="substring filter on sample names")
    args = ap.parse_args()

    settings = Settings.from_env(redact_terms=DEMO_REDACT_TERMS, data_dir=Path(tempfile.mkdtemp()))
    bk = Briefkasten(build_router(settings), settings)
    rows = []
    for gold_path in sorted((SAMPLES / "gold").glob("*.json")):
        if args.only and args.only not in gold_path.stem:
            continue
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        letter = (SAMPLES / "letters" / gold["letter"]).read_text(encoding="utf-8")
        bk.settings.state = gold.get("bundesland", "BW")
        for i in range(args.repeats):
            t0 = time.perf_counter()
            state = bk.process(letter, source_name=gold["letter"], today=TODAY)
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
            ok = sum(checks.values())
            print(f"{gold_path.stem:<28} #{i}  {ok}/{len(checks)} checks  {rows[-1]['seconds']:>5}s  "
                  f"{rows[-1]['tokens']:>6} tok  " + " ".join(k for k, v in checks.items() if not v))

    total = sum(len(r["checks"]) for r in rows)
    passed = sum(sum(r["checks"].values()) for r in rows)
    print(f"\nOverall: {passed}/{total} checks ({passed / max(total, 1):.0%}) · model {settings.model} / {settings.model_fast}")
    out = Path(__file__).parent / f"results-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"model": settings.model, "model_fast": settings.model_fast, "rows": rows}, indent=2, default=str))
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
