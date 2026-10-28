from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel

from ..models import DeadlineRule, ExtractedDeadline, Extraction, TraceEvent, Triage
from ..tools.deadlines import resolve_deadline
from .base import LETTER_RULES, Agent, Tool, letter_search_tool, wrap_letter
from .verifier import check_grounding

SYSTEM = f"""You are the Extractor agent in Briefkasten. Pull structured facts out of an official German letter.
Code computes the actual dates, so describe each deadline as a RULE, never compute dates yourself.

Deadline rules:
- An explicit date ("bis zum 16.11.2026") -> kind=fixed_date with fixed_date in ISO format.
- A period starting when an official decision counts as delivered ("innerhalb eines Monats nach Bekanntgabe",
  "Die Frist ... beträgt einen Monat", Einspruch/Widerspruch periods) -> kind=after_notification with months/weeks/days.
  delivery=electronic only if the letter says it was sent electronically.
- A period starting when a private letter was received ("innerhalb von 30 Tagen nach Erhalt", "nach Zugang")
  -> kind=after_receipt.
- deadline.kind: payment for money, right_to_object for Einspruch/Widerspruch/Einwendungen,
  appointment for dates to show up, action_required for everything else.

General rules:
- Every quote must be copied character for character from the letter (a short sentence or fragment).
  Use "..." only to skip words inside one sentence. Confirm doubtful quotes and amounts with find_in_letter.
- Use preview_deadline to see the date the legal rules produce for a rule; if it contradicts the letter, re-read the letter.
- Amounts are numbers: German "1.234,56 €" becomes 1234.56. direction=you_pay only for money the recipient owes.
- letter_date is the date printed on the letter, not a deadline.
- Only extract what is in the letter. An empty list is better than a guess.
- submit runs a verifier that checks every quote, amount and date against the letter.

{LETTER_RULES}"""


class PreviewParams(BaseModel):
    rule: DeadlineRule
    letter_date: date | None = None


@dataclass
class DeadlineContext:
    state: str
    today: date
    received_on: date | None = None


@dataclass
class ExtractionRun:
    extraction: Extraction
    rounds: int = 1
    rejected: list[str] = field(default_factory=list)  # verifier errors the agent had to fix


class ExtractorAgent(Agent):
    name = "extractor"
    tier = "smart"
    max_tokens = 2500
    max_steps = 8

    def run(
        self,
        letter: str,
        triage: Triage,
        trace: list[TraceEvent],
        ctx: DeadlineContext,
        *,
        feedback: list[str] | None = None,
        max_rejections: int = 1,
    ) -> ExtractionRun:
        prompt = f"Document type according to triage: {triage.doc_type.value}.\n\n{wrap_letter(letter)}"
        if feedback:
            prompt += "\n\nInstructions from the supervisor for this extraction:\n" + "\n".join(f"- {f}" for f in feedback)

        rounds, rejected = 0, []

        def verify(ex: Extraction) -> list[str]:
            nonlocal rounds
            rounds += 1
            errors = [i.message for i in check_grounding(ex, letter, triage)[0] if i.severity == "error"]
            if errors and rounds <= max_rejections:
                rejected.extend(errors)
            return errors

        def preview(p: PreviewParams) -> str:
            probe = ExtractedDeadline(id="preview", purpose="preview", kind="action_required", rule=p.rule, quote="")
            r = resolve_deadline(probe, letter_date=p.letter_date, received_on=ctx.received_on, state=ctx.state, today=ctx.today)
            return f"Due: {r.due_date or 'cannot be computed'} ({r.basis})\n" + "\n".join(f"- {s}" for s in r.steps)

        tools = [
            letter_search_tool(letter),
            Tool("preview_deadline", "Compute the due date the legal rules give for a deadline rule.", PreviewParams, preview),
        ]
        ex = self.react(
            system=SYSTEM, prompt=prompt, output=Extraction, trace=trace, tools=tools, check=verify, max_rejections=max_rejections
        )
        return ExtractionRun(ex, rounds=max(rounds, 1), rejected=rejected)
