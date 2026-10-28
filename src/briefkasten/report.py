"""Terminal report for a run."""

from __future__ import annotations

import textwrap

import click

from .models import RunState

URGENCY_STYLE = {
    "overdue": ("OVERDUE", "red"),
    "urgent": ("URGENT", "red"),
    "soon": ("DUE SOON", "yellow"),
    "relaxed": ("NO RUSH", "green"),
    "none": ("NO DEADLINE", "blue"),
}


def _h(title: str) -> str:
    return "\n" + click.style(title, bold=True)


def _wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(text, width=92, initial_indent=indent, subsequent_indent=indent)


def render(state: RunState, verbose: bool = False) -> str:
    out: list[str] = []
    label, color = URGENCY_STYLE[state.urgency]
    t = state.triage
    head = f"{t.sender_name or t.sender_type.value} · {t.doc_type.value.replace('_', ' ')}" if t else state.source_name
    out.append(click.style(f" {label} ", fg="white", bg=color, bold=True) + "  " + click.style(head, bold=True))
    out.append(click.style(f"run {state.run_id} · {state.source_name} · Bundesland {state.bundesland} · status {state.status}", dim=True))
    if state.error:
        out.append(click.style(f"Error: {state.error}", fg="red"))

    if state.explanation:
        e = state.explanation
        out += [_h("What this is"), _wrap(e.summary_en)]
        out += [_h("Key points")] + [_wrap(f"• {p}") for p in e.key_points]
        if e.if_ignored:
            out += [_h("If you do nothing"), _wrap(e.if_ignored)]

    if state.deadlines:
        out.append(_h("Deadlines"))
        for d in state.deadlines:
            when = d.due_date.strftime("%a %d.%m.%Y") if d.due_date else "unknown"
            left = "" if d.days_left is None else f" ({d.days_left} days left)" if d.days_left >= 0 else f" ({-d.days_left} days ago)"
            tag = {"stated": "", "computed": " [computed]", "estimated": " [estimated]", "unknown": ""}[d.basis]
            fg = "red" if d.status in ("overdue", "due_today") or (d.days_left is not None and d.days_left <= 7) else None
            out.append("  " + click.style(f"{when}{left}", fg=fg, bold=True) + f"  {d.purpose}{tag}")
            if verbose or d.basis != "stated":
                out += [click.style(_wrap(f"↳ {s}", "      "), dim=True) for s in d.steps]

    ex = state.extraction
    if ex and ex.amounts:
        out.append(_h("Amounts"))
        for a in ex.amounts:
            sign = {"you_pay": "you pay", "you_receive": "you get", "info": "info"}[a.direction]
            out.append(f"  {a.value:>10,.2f} {a.currency}  {sign:<8} {a.purpose}")
    if ex and ex.actions:
        out.append(_h("To do"))
        for a in ex.actions:
            mark = {"high": "!!", "medium": "! ", "low": "  "}[a.priority]
            out.append(_wrap(f"{mark} {a.description}"))

    if state.explanation and state.explanation.glossary:
        out.append(_h("German words to know"))
        out += [f"  {g.german} – {g.english}" + (f" ({g.note})" if g.note else "") for g in state.explanation.glossary]

    v = state.verification
    if v:
        status = click.style("passed", fg="green") if v.passed else click.style("FAILED", fg="red")
        out.append(_h("Checks") + f"  {status} · {v.quotes_checked} quotes checked against the letter · {v.extraction_rounds} extraction round(s)")
        for msg in v.repaired:
            out.append(click.style(_wrap(f"repaired: {msg}"), dim=True))
        for i in v.issues:
            out.append(_wrap(f"{'✗' if i.severity == 'error' else '⚠'} {i.message}"))

    if state.draft:
        d = state.draft
        out.append(_h(f"Draft reply ({d.intent}) – {d.status.replace('_', ' ')}"))
        out.append(click.style("  Betreff: " + d.subject_de, bold=True))
        out += [textwrap.indent(d.body_de, "  "), "", click.style("  English translation:", dim=True)]
        out.append(click.style(textwrap.indent(d.body_en, "  "), dim=True))
        out.append(_wrap(f"Note: {d.notes_en}"))

    if state.trace:
        total_ms = sum(e.duration_ms for e in state.trace)
        toks = sum(e.input_tokens + e.output_tokens for e in state.trace)
        out.append(_h("Agent trace") + click.style(f"  {len(state.trace)} calls · {toks:,} tokens · {total_ms/1000:.1f}s model time", dim=True))
        if verbose:
            for e in state.trace:
                out.append(f"  {e.agent:<12} {e.model:<22} {e.duration_ms:>6} ms  {e.input_tokens:>6}→{e.output_tokens:<5}" + (f"  {e.note}" if e.note else ""))
    out.append("\n" + click.style("Briefkasten explains letters; it is not legal or tax advice.", dim=True))
    return "\n".join(out)
