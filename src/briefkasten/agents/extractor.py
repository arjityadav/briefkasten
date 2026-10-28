from __future__ import annotations

from ..models import Extraction, TraceEvent, Triage
from .base import LETTER_RULES, Agent, wrap_letter

SYSTEM = f"""You are the Extractor agent in Briefkasten. Pull structured facts out of an official German letter.
Downstream code computes the actual dates, so describe each deadline as a RULE, never compute dates yourself.

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
  Use "..." only to skip words inside one sentence.
- Amounts are numbers: German "1.234,56 €" becomes 1234.56. direction=you_pay only for money the recipient owes.
- letter_date is the date printed on the letter, not a deadline.
- Only extract what is in the letter. An empty list is better than a guess.

{LETTER_RULES}"""


class ExtractorAgent(Agent):
    name = "extractor"
    tier = "smart"
    max_tokens = 2500

    def run(self, letter: str, triage: Triage, trace: list[TraceEvent], feedback: list[str] | None = None) -> Extraction:
        prompt = f"Document type according to triage: {triage.doc_type.value}.\n\n{wrap_letter(letter)}"
        if feedback:
            issues = "\n".join(f"- {f}" for f in feedback)
            prompt += (
                "\n\nA verifier checked your previous extraction against the letter and found these problems:\n"
                f"{issues}\nFix them. Remove any item you cannot support with a verbatim quote."
            )
        return self.ask_json(system=SYSTEM, prompt=prompt, schema=Extraction, trace=trace)
