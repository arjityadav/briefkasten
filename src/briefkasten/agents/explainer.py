from __future__ import annotations

from ..models import Explanation, TraceEvent, Triage
from .base import LETTER_RULES, Agent, letter_search_tool, wrap_letter

SYSTEM = f"""You are the Explainer agent in Briefkasten. Your reader lives in Germany but reads German poorly.
Explain the letter in plain English, as a knowledgeable friend would.

Rules:
- Only state what the letter says or what directly follows from it. Do not invent amounts, dates or consequences.
  Check figures you are unsure about with find_in_letter.
- Say plainly what the reader has to do and what happens if they do nothing, when the letter says so.
- Mention useful general facts only when they are standard and certain, for example: filing an objection
  to a tax decision does not by itself postpone the payment; after an employer's dismissal, a lawsuit against it
  (Kündigungsschutzklage) must be filed within 3 weeks of receiving it (§ 4 KSchG); for a Mahnbescheid or
  Bußgeldbescheid the deadline runs from the delivery date on the yellow envelope.
- The document may also be a contract, payslip, invoice or form: explain what it is and what matters in it.
- Glossary: up to 6 German words from the letter that the reader will meet again.
- This is not legal advice; do not tell the reader they will win or lose a dispute.

{LETTER_RULES}"""


class ExplainerAgent(Agent):
    name = "explainer"
    tier = "smart"
    max_tokens = 1500

    def run(self, letter: str, triage: Triage, trace: list[TraceEvent], feedback: list[str] | None = None) -> Explanation:
        prompt = (
            f"Triage result: sender={triage.sender_type.value} ({triage.sender_name or 'unknown'}), "
            f"document={triage.doc_type.value}, official decision={triage.is_official_decision}.\n\n"
            f"{wrap_letter(letter)}"
        )
        if feedback:
            prompt += "\n\nA reviewer found these problems with your previous explanation. Fix them:\n" + "\n".join(
                f"- {f}" for f in feedback
            )
        return self.react(
            system=SYSTEM, prompt=prompt, output=Explanation, trace=trace, tools=[letter_search_tool(letter)]
        )
