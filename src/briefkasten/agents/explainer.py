from __future__ import annotations

from ..models import Explanation, TraceEvent, Triage
from .base import LETTER_RULES, Agent, wrap_letter

SYSTEM = f"""You are the Explainer agent in Briefkasten. Your reader lives in Germany but reads German poorly.
Explain the letter in plain English, as a knowledgeable friend would.

Rules:
- Only state what the letter says or what directly follows from it. Do not invent amounts, dates or consequences.
- Say plainly what the reader has to do and what happens if they do nothing, when the letter says so.
- Mention useful general facts only when they are standard and certain (for example: filing an objection
  to a tax decision does not by itself postpone the payment).
- Glossary: up to 6 German words from the letter that the reader will meet again.
- This is not legal advice; do not tell the reader they will win or lose a dispute.

{LETTER_RULES}"""


class ExplainerAgent(Agent):
    name = "explainer"
    tier = "smart"
    max_tokens = 1500

    def run(self, letter: str, triage: Triage, trace: list[TraceEvent]) -> Explanation:
        prompt = (
            f"Triage result: sender={triage.sender_type.value} ({triage.sender_name or 'unknown'}), "
            f"document={triage.doc_type.value}, official decision={triage.is_official_decision}.\n\n"
            f"{wrap_letter(letter)}"
        )
        return self.ask_json(system=SYSTEM, prompt=prompt, schema=Explanation, trace=trace)
