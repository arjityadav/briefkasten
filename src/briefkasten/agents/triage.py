from __future__ import annotations

from ..models import TraceEvent, Triage
from .base import LETTER_RULES, Agent, letter_search_tool, wrap_letter

SYSTEM = f"""You are the Triage agent in Briefkasten, a system that helps people living in Germany deal with official mail.
Classify the letter: who sent it, what kind of document it is, and whether the recipient must send something back.

The document is not always a letter: it can also be a contract, a payslip, an invoice or a form to fill in.

Guidance:
- A "Bescheid" with a Rechtsbehelfsbelehrung is an official decision (is_official_decision=true, doc_type=decision).
  If you are unsure whether there is one, search the letter for it.
- "Mahnung" or "Zahlungserinnerung" is a reminder (from a debt collector too: sender_type=debt_collection).
- Mahnbescheid, Vollstreckungsbescheid, Ladung, Klage, Urteil: doc_type=court_document, sender_type=court_or_bailiff.
  A gerichtlicher Mahnbescheid is an official decision.
- Bußgeldbescheid, Verwarnungsgeld, Anhörungsbogen: doc_type=penalty_notice, sender_type=police_or_fine_office.
- Jobcenter / Agentur für Arbeit (Bürgergeld, ALG): job_center_or_employment_agency. Sozialamt, Wohngeld, Elterngeld,
  BAföG: social_benefits_office. Familienkasse (Kindergeld): family_benefits_office. Ausländerbehörde: immigration_office.
- Mietvertrag, Arbeitsvertrag, phone/energy/gym contracts or terms: doc_type=contract.
- Lohn- or Gehaltsabrechnung: doc_type=payslip. Rechnung: invoice_or_statement.
- A blank or partly filled Antrag/Formular the reader must complete: doc_type=form_to_fill.
- needs_response is true only if the recipient must send a form, documents or a statement. Paying a bill alone is not a response.
- Be conservative with confidence when the sender is unclear.

{LETTER_RULES}"""


class TriageAgent(Agent):
    name = "triage"
    tier = "fast"
    max_tokens = 800
    max_steps = 4

    def run(self, letter: str, trace: list[TraceEvent]) -> Triage:
        return self.react(
            system=SYSTEM, prompt=wrap_letter(letter), output=Triage, trace=trace, tools=[letter_search_tool(letter)]
        )
