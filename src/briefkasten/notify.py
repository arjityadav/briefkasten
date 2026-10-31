"""Plain outputs for automations (n8n, scripts): calendar events and a notification text for a finished run.

They work on the public view of a run, so they contain the same redacted placeholders as everything
else that leaves the app; real values only come back in an exported, approved reply.
"""

from __future__ import annotations

from datetime import date


def _fmt(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%a %d.%m.%Y") if iso else "unknown date"


def sender_label(run: dict) -> str:
    """Who sent it, or at least what it is ("court document"), for titles and subjects."""
    t = run.get("triage") or {}
    return t.get("sender_name") or (t.get("doc_type") or "").replace("_", " ") or run.get("source_name", "document")


def calendar_events(run: dict) -> list[dict]:
    """One all-day event per dated deadline, ready for a calendar node."""
    sender = sender_label(run)
    out = []
    for d in run.get("deadlines", []):
        if not d.get("due_date"):
            continue
        out.append(
            {
                "title": f"{d['purpose']} ({sender})",
                "date": d["due_date"],
                "basis": d["basis"],
                "description": "\n".join(d.get("steps", [])) + f"\n\nBriefkasten run {run['run_id']}",
            }
        )
    return out


def summary_text(run: dict) -> str:
    """A short plain-text notification: what arrived, what to do, by when."""
    t = run.get("triage") or {}
    lines = [f"{sender_label(run)}: {(t.get('doc_type') or 'document').replace('_', ' ')}"]
    if run.get("status") == "failed":
        # Partial results still matter (a deadline found before the failure is real); say what is missing.
        lines += ["", f"The analysis stopped early ({run.get('error')}). What was found before that is below; check the rest in Briefkasten."]
    if e := run.get("explanation"):
        lines += ["", e["summary_en"]]
    if run.get("deadlines"):
        lines += ["", "Deadlines:"]
        lines += [f"- {_fmt(d.get('due_date'))}: {d['purpose']}" + (f" ({d['basis']})" if d["basis"] != "stated" else "") for d in run["deadlines"]]
    pays = [a for a in (run.get("extraction") or {}).get("amounts", []) if a["direction"] == "you_pay"]
    if pays:
        lines += ["", "To pay:"] + [f"- {a['value']:,.2f} € {a['purpose']}" for a in pays]
    if (d := run.get("draft")) and d.get("status") == "pending_approval":
        lines += ["", f"A reply ({d['intent'].replace('_', ' ')}) is waiting for your approval in Briefkasten."]
    v = run.get("verification")
    if v and not v["passed"]:
        lines += ["", "Some facts could not be verified against the letter: check them in Briefkasten."]
    lines += ["", "Not legal or tax advice."]
    return "\n".join(lines)
