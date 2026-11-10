"""Specialists for documents that are not letters: payslips, contracts and forms.

Each one submits a typed analysis that code checks against the document (quotes, amounts,
dates and, for payslips, the arithmetic) before it is accepted, like the Extractor.
"""

from __future__ import annotations

from typing import TypeVar

from ..models import ContractAnalysis, FormGuide, PayslipAnalysis, TraceEvent, Triage
from .base import LETTER_RULES, Agent, letter_search_tool, wrap_letter
from .verifier import check_analysis

T = TypeVar("T", PayslipAnalysis, ContractAnalysis, FormGuide)

PAYSLIP_SYSTEM = f"""You are the Payslip agent in Briefkasten. Explain a German payslip (Lohn-/Gehaltsabrechnung) line by line.

Rules:
- Copy every amount exactly as printed (German "1.234,56" becomes 1234.56) with a verbatim quote of its line.
- category: tax = Lohnsteuer, Solidaritätszuschlag, Kirchensteuer; social_insurance = Kranken-, Renten-, Arbeitslosen-,
  Pflegeversicherung; other_deduction = anything taken after net (VWL-Sparrate, Vorschuss, Sachbezug, Pfändung);
  addition = anything added after net (e.g. Erstattungen).
- Use the total lines only if individual lines are missing; never count a total and its parts both.
- submit checks that gross minus tax and social insurance equals net, and that net with the other lines equals the payout.
- worth_checking_en: things the employee should verify (tax class, church tax if they are not a church member,
  hours, unusual deductions). Do not judge whether the salary is fair.

{LETTER_RULES}"""

CONTRACT_SYSTEM = f"""You are the Contract agent in Briefkasten. Explain a contract to a reader who reads German poorly.

Rules:
- Find what matters in practice: what it costs, how long it binds, how it renews, how and when it can be cancelled.
- key_terms: the most important terms (max 6). watch_out: clauses that are unusual or bad for the reader, such as
  long minimum terms, automatic renewals, price increase rights, fees, penalties, data sharing. Say plainly why.
- Every clause needs a verbatim quote. Dates you work out yourself (e.g. an end date from a start date and term)
  are allowed for end_date; say so in renewal or notice_period.
- General facts are fine when standard and certain (for example: since March 2022, consumer contracts in Germany that
  renew automatically can be cancelled monthly after the minimum term). This is not legal advice.

{LETTER_RULES}"""

FORM_SYSTEM = f"""You are the Form agent in Briefkasten. Guide the reader through a German form they must fill in.

Rules:
- fields: each field in order, with its label copied exactly as printed, what it means in English, and what kind of
  answer goes there. Never invent the reader's personal data; describe what to enter instead.
- attachments: documents the form asks for. where_to_send: if the form says.
- watch_out: fields with consequences, e.g. religion affects church tax, a wrong move-in date can lead to a fine.

{LETTER_RULES}"""


class _AnalystAgent(Agent):
    system: str = ""
    output: type
    max_tokens = 3000
    max_steps = 6
    max_rejections = 1

    def run(
        self,
        letter: str,
        triage: Triage,
        trace: list[TraceEvent],
        feedback: list[str] | None = None,
        rejected: list[str] | None = None,
    ):
        """`rejected`, if given, collects the check errors the agent was sent back to fix."""
        prompt = f"Document type according to triage: {triage.doc_type.value}.\n\n{wrap_letter(letter)}"
        if feedback:
            prompt += "\n\nInstructions from the supervisor:\n" + "\n".join(f"- {f}" for f in feedback)
        rounds = 0

        def check(analysis) -> list[str]:
            nonlocal rounds
            rounds += 1
            errors = [i.message for i in check_analysis(analysis, letter)[0] if i.severity == "error"]
            if errors and rejected is not None and rounds <= self.max_rejections:
                rejected.extend(errors)
            return errors

        return self.react(
            system=self.system,
            prompt=prompt,
            output=self.output,
            trace=trace,
            tools=[letter_search_tool(letter)],
            check=check,
            max_rejections=self.max_rejections,
        )


class PayslipAgent(_AnalystAgent):
    name = "payslip"
    system = PAYSLIP_SYSTEM
    output = PayslipAnalysis


class ContractAgent(_AnalystAgent):
    name = "contract"
    system = CONTRACT_SYSTEM
    output = ContractAnalysis


class FormAgent(_AnalystAgent):
    name = "form"
    system = FORM_SYSTEM
    output = FormGuide
