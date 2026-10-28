"""Verifier: deterministic grounding checks plus an LLM critic for the explanation.

check_grounding is the Extractor's `submit` gate: errors go back to the Extractor as tool
observations to fix (one round by default). Critic findings are reported to the supervisor,
which may ask the Explainer to revise; they never block the run.
"""

from __future__ import annotations

from ..models import CriticReport, Explanation, Extraction, Issue, TraceEvent, Triage
from ..tools.grounding import amount_in_text, date_in_text, quote_in_text
from .base import LETTER_RULES, Agent, letter_search_tool, wrap_letter

CRITIC_SYSTEM = f"""You are the Critic agent in Briefkasten. You check an English explanation of a German letter for faithfulness.
List statements in the explanation that the letter does not support (wrong amounts, dates, senders,
invented consequences), and important content of the letter the explanation omits.
Check doubtful statements with find_in_letter before listing them.
General well-known facts (for example how objections work in general) are acceptable and are not unsupported claims.
If everything is fine, return empty lists.

{LETTER_RULES}"""


def check_grounding(extraction: Extraction, letter: str, triage: Triage | None) -> tuple[list[Issue], int]:
    issues: list[Issue] = []
    checked = 0

    def quote_issue(field: str, quote: str | None, required: bool = True) -> None:
        nonlocal checked
        if not quote:
            if required:
                issues.append(Issue(severity="error", source="grounding", field=field, message=f"{field}: no supporting quote given"))
            return
        checked += 1
        if not quote_in_text(quote, letter):
            issues.append(
                Issue(severity="error", source="grounding", field=field, message=f'{field}: quote not found in the letter: "{quote[:120]}"')
            )

    if extraction.letter_date:
        quote_issue("letter_date", extraction.letter_date_quote, required=False)
        if not date_in_text(extraction.letter_date, letter):
            issues.append(
                Issue(
                    severity="error",
                    source="grounding",
                    field="letter_date",
                    message=f"letter_date {extraction.letter_date:%d.%m.%Y} does not appear in the letter",
                )
            )

    for d in extraction.deadlines:
        f = f"deadlines[{d.id}]"
        quote_issue(f, d.quote)
        r = d.rule
        if r.kind == "fixed_date":
            if r.fixed_date is None:
                issues.append(Issue(severity="error", source="consistency", field=f, message=f"{f}: kind is fixed_date but no date was given"))
            elif not date_in_text(r.fixed_date, letter):
                issues.append(
                    Issue(severity="error", source="grounding", field=f, message=f"{f}: date {r.fixed_date:%d.%m.%Y} does not appear in the letter")
                )
        elif not (r.months or r.weeks or r.days):
            issues.append(Issue(severity="error", source="consistency", field=f, message=f"{f}: a relative deadline needs a period length"))
        if r.kind == "after_notification" and extraction.letter_date is None:
            issues.append(
                Issue(severity="warning", source="consistency", field=f, message=f"{f}: needs the letter date, which was not found")
            )

    for i, a in enumerate(extraction.amounts):
        f = f"amounts[{i}]"
        quote_issue(f, a.quote)
        if not amount_in_text(a.value, letter):
            issues.append(Issue(severity="error", source="grounding", field=f, message=f"{f}: amount {a.value:.2f} does not appear in the letter"))

    for i, ref in enumerate(extraction.references):
        quote_issue(f"references[{i}]", ref.quote)

    for i, act in enumerate(extraction.actions):
        quote_issue(f"actions[{i}]", act.quote, required=False)

    if triage and triage.is_official_decision and extraction.has_legal_remedy_notice:
        if not any(d.kind == "right_to_object" for d in extraction.deadlines):
            issues.append(
                Issue(
                    severity="warning",
                    source="consistency",
                    field="deadlines",
                    message="Official decision with a Rechtsbehelfsbelehrung, but no objection deadline was extracted",
                )
            )
    if triage and triage.is_official_decision and not extraction.has_legal_remedy_notice:
        issues.append(
            Issue(
                severity="warning",
                source="consistency",
                field="has_legal_remedy_notice",
                message="Official decision without an instruction on legal remedies: the objection period may be longer (often one year). Check with an advisor.",
            )
        )
    return issues, checked


class CriticAgent(Agent):
    name = "critic"
    tier = "fast"
    max_tokens = 800
    max_steps = 5

    def run(self, letter: str, explanation: Explanation, trace: list[TraceEvent]) -> list[Issue]:
        prompt = f"Explanation to check:\n{explanation.model_dump_json(indent=2)}\n\n{wrap_letter(letter)}"
        report = self.react(
            system=CRITIC_SYSTEM, prompt=prompt, output=CriticReport, trace=trace, tools=[letter_search_tool(letter)]
        )
        issues = [Issue(severity="warning", source="critic", field="explanation", message=f"Unsupported: {c}") for c in report.unsupported_claims]
        issues += [Issue(severity="warning", source="critic", field="explanation", message=f"Missing: {m}") for m in report.missing_important_points]
        return issues
