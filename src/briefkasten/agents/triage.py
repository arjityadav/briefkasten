from __future__ import annotations

from ..models import TraceEvent, Triage
from .base import LETTER_RULES, Agent, wrap_letter

SYSTEM = f"""You are the Triage agent in Briefkasten, a system that helps people living in Germany deal with official mail.
Classify the letter: who sent it, what kind of document it is, and whether the recipient must send something back.

Guidance:
- A "Bescheid" with a Rechtsbehelfsbelehrung is an official decision (is_official_decision=true, doc_type=decision).
- "Mahnung" or "Zahlungserinnerung" is a reminder.
- needs_response is true only if the recipient must send a form, documents or a statement. Paying a bill alone is not a response.
- Be conservative with confidence when the sender is unclear.

{LETTER_RULES}"""


class TriageAgent(Agent):
    name = "triage"
    tier = "fast"
    max_tokens = 800

    def run(self, letter: str, trace: list[TraceEvent]) -> Triage:
        return self.ask_json(system=SYSTEM, prompt=wrap_letter(letter), schema=Triage, trace=trace)
