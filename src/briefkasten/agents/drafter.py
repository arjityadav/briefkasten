from __future__ import annotations

from ..models import REPLY_INTENTS, DraftOutput, Explanation, Extraction, ResolvedDeadline, TraceEvent, Triage
from .base import LETTER_RULES, Agent, wrap_letter

SYSTEM = f"""You are the Drafter agent in Briefkasten. Draft a short, formal German reply letter for the recipient,
plus a faithful English translation. A human reviews and approves every draft before it is used.

Rules:
- Formal German (Sie-Form, "Sehr geehrte Damen und Herren," unless a contact person is named).
- Put the sender's reference numbers (Steuernummer, Aktenzeichen, Versichertennummer...) and the date of their
  letter in subject_de. Use the placeholders exactly as they appear, e.g. [STEUERNUMMER_1].
- Never invent facts about the recipient. Where only they can fill something in (reasons, evidence, dates),
  insert an editorial placeholder in square brackets in German, e.g. [Begründung ergänzen].
- body_de starts with the salutation and ends before "Mit freundlichen Grüßen" (that is added later).
- Keep it under 200 words. Do not threaten, do not admit liability, do not promise payment unless the intent says so.
- For objections to tax decisions you may add a request for "Aussetzung der Vollziehung" for the disputed amount.

Allowed intents:
{chr(10).join(f"- {k}: {v}" for k, v in REPLY_INTENTS.items())}

{LETTER_RULES}"""


class DrafterAgent(Agent):
    name = "drafter"
    tier = "smart"
    max_tokens = 2000

    def run(
        self,
        letter: str,
        *,
        triage: Triage,
        explanation: Explanation,
        extraction: Extraction,
        deadlines: list[ResolvedDeadline],
        intent: str | None,
        trace: list[TraceEvent],
    ) -> DraftOutput:
        due = "\n".join(f"- {d.purpose}: {d.due_date or 'unknown'}" for d in deadlines) or "- none"
        refs = "\n".join(f"- {r.label}: {r.value}" for r in extraction.references) or "- none"
        wanted = f"Use intent: {intent}" if intent else "Choose the most appropriate intent."
        prompt = (
            f"{wanted}\n\nSender: {triage.sender_name or triage.sender_type.value}\n"
            f"Letter date: {extraction.letter_date or 'unknown'}\n"
            f"Summary: {explanation.summary_en}\nReferences:\n{refs}\nDeadlines:\n{due}\n\n{wrap_letter(letter)}"
        )
        return self.ask_json(system=SYSTEM, prompt=prompt, schema=DraftOutput, trace=trace)
