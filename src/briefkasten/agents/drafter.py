from __future__ import annotations

from pydantic import BaseModel

from ..models import REPLY_INTENTS, DraftOutput, Explanation, Extraction, ResolvedDeadline, TraceEvent, Triage
from ..tools.redact import PLACEHOLDER, open_placeholders
from .base import LETTER_RULES, Agent, Tool, letter_search_tool, wrap_letter

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
- Use check_draft before submitting to see which blanks the user must fill and whether any placeholder is unknown.

Allowed intents:
{chr(10).join(f"- {k}: {v}" for k, v in REPLY_INTENTS.items())}

{LETTER_RULES}"""


class DraftCheckParams(BaseModel):
    subject_de: str
    body_de: str


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
        known_placeholders: set[str],
        trace: list[TraceEvent],
        instructions: str | None = None,
    ) -> DraftOutput:
        due = "\n".join(f"- {d.purpose}: {d.due_date or 'unknown'}" for d in deadlines) or "- none"
        refs = "\n".join(f"- {r.label}: {r.value}" for r in extraction.references) or "- none"
        wanted = f"Use intent: {intent}" if intent else "Choose the most appropriate intent."
        prompt = (
            f"{wanted}\n\nSender: {triage.sender_name or triage.sender_type.value}\n"
            f"Letter date: {extraction.letter_date or 'unknown'}\n"
            f"Summary: {explanation.summary_en}\nReferences:\n{refs}\nDeadlines:\n{due}\n\n{wrap_letter(letter)}"
        )
        if instructions:
            prompt += f"\n\nInstructions from the supervisor: {instructions}"

        def unknown_placeholders(text: str) -> list[str]:
            return sorted({m for m in PLACEHOLDER.findall(text) if m not in known_placeholders})

        def check_draft(p: DraftCheckParams) -> str:
            text = p.subject_de + "\n" + p.body_de
            blanks = list(dict.fromkeys(open_placeholders(text, {k: "" for k in known_placeholders})))
            bad = unknown_placeholders(text)
            return (
                f"Words in body: {len(p.body_de.split())}\n"
                f"Blanks the user must fill: {', '.join(blanks) or 'none'}\n"
                f"Unknown redaction placeholders (not in the letter): {', '.join(bad) or 'none'}"
            )

        def problems(d: DraftOutput) -> list[str]:
            out = []
            if d.intent not in REPLY_INTENTS:
                out.append(f"intent '{d.intent}' is not one of: {', '.join(REPLY_INTENTS)}")
            elif intent and d.intent != intent:
                out.append(f"the user asked for intent '{intent}', not '{d.intent}'")
            bad = unknown_placeholders(d.subject_de + "\n" + d.body_de)
            if bad:
                out.append(f"these placeholders do not exist in the letter: {', '.join(bad)}")
            return out

        tools = [
            letter_search_tool(letter),
            Tool("check_draft", "Check a draft for open blanks, unknown placeholders and length.", DraftCheckParams, check_draft),
        ]
        return self.react(
            system=SYSTEM, prompt=prompt, output=DraftOutput, trace=trace, tools=tools, check=problems, max_rejections=2
        )
