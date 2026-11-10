"""Supervisor agent: an LLM that plans the work and delegates it to specialist agents.

The specialists are exposed to it as tools. It decides what to run, in what order, what to
run in parallel, and whether a result is good enough or should be redone with feedback.
Agents never talk to each other directly: they read from and write to a shared Workspace
(blackboard), and the supervisor only sees compact summaries of what they produced.

Code still owns the guarantees: each tool checks its preconditions, every specialist has a
call budget, deadlines come only from the deterministic calculator, and `submit` is refused
until the required work is done on the current results.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ..models import REPLY_INTENTS, DocType, Issue, ReplyDraft, RunState, TraceEvent
from ..tools.deadlines import resolve_deadline, urgency
from ..tools.redact import open_placeholders
from .base import Agent, NoParams, Tool, ToolError
from .extractor import DeadlineContext
from .verifier import check_analysis, check_grounding

if TYPE_CHECKING:
    from .analysts import ContractAgent, FormAgent, PayslipAgent
    from .drafter import DrafterAgent
    from .explainer import ExplainerAgent
    from .extractor import ExtractorAgent
    from .triage import TriageAgent
    from .verifier import CriticAgent

SYSTEM = f"""You are the Supervisor of Briefkasten, a team of AI agents that helps people in Germany deal with official mail
and everyday documents: letters, contracts, payslips, invoices and forms. You do not read the document yourself. You plan the work, delegate it to specialists through tools, judge what
they return, and decide what happens next.

Your team:
- triage_letter: classifies sender, document type, and whether the recipient must reply.
- explain_letter: writes the plain-English explanation. Accepts feedback to revise it.
- extract_facts: extracts deadlines (as legal rules), amounts, references and actions, each with a verbatim
  quote. Its output is verified against the letter; remaining verifier errors are reported to you.
- compute_deadlines: deterministic legal date calculator (not an AI). The only source of due dates.
- review_explanation: a critic that checks the explanation against the letter.
- draft_reply: drafts a formal German reply that the user must approve.
- analyse_payslip: explains a payslip line by line; code checks that the numbers add up.
- analyse_contract: costs, term, renewal, cancellation and clauses to watch out for.
- guide_form: explains each field of a form the user must fill in.

How to work:
- Start with triage; its doc_type decides the route:
  letters and invoices: explain + extract (+ draft if needed); contract: explain + extract + analyse_contract
  (the extractor turns the notice period into a cancel-by deadline); payslip: explain + analyse_payslip
  (extracting is optional); form_to_fill: explain + extract + guide_form.
- Specialists that don't depend on each other should be requested in the same turn.
- Always compute deadlines from the current extraction, and always review the current explanation.
- If the critic reports unsupported claims or important omissions, ask the explainer to revise once with that
  feedback, then review again. Minor wording issues are not worth a revision.
- If extraction still has verifier errors, you may ask once more with specific instructions; otherwise move on,
  the errors will be shown to the user.
- Draft a reply when the user asked for one, or when a letter needs a response. Pass the user's intent if given.
- Finish with submit. In notes, tell the user in 1-3 plain-English sentences what the team did and anything to double-check.

Each specialist has a small call budget; tools tell you when it is used up."""

BUDGET = {
    "triage_letter": 1,
    "explain_letter": 2,
    "extract_facts": 2,
    "compute_deadlines": 3,
    "review_explanation": 2,
    "draft_reply": 2,
    "analyse_payslip": 2,
    "analyse_contract": 2,
    "guide_form": 2,
}

# Document types that get their own specialist instead of being treated as a letter.
FAMILY = {DocType.PAYSLIP: "payslip", DocType.CONTRACT: "contract", DocType.FORM: "form"}


class FeedbackParams(BaseModel):
    feedback: list[str] = Field(default_factory=list, description="Specific problems to fix from an earlier attempt")


class DraftParams(BaseModel):
    intent: str | None = Field(None, description=f"One of: {', '.join(REPLY_INTENTS)}. Omit to let the drafter choose.")
    instructions: str | None = Field(None, description="Anything the drafter should pay attention to")


class SupervisorReport(BaseModel):
    notes: str = Field(description="1-3 sentences for the user: what the team did and what to double-check")


@dataclass
class Workspace:
    """Shared blackboard for one run. Specialists write here; the supervisor reads summaries."""

    state: RunState
    grounding: list[Issue] = field(default_factory=list)
    critic: list[Issue] = field(default_factory=list)
    quotes_checked: int = 0
    extraction_rounds: int = 0
    repaired: list[str] = field(default_factory=list)
    deadlines_current: bool = False
    review_current: bool = False
    analysis_issues: list[Issue] = field(default_factory=list)
    analysis_quotes: int = 0
    calls: Counter = field(default_factory=Counter)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def letter(self) -> str:
        return self.state.text_redacted

    def spend(self, tool: str) -> None:
        with self.lock:
            if self.calls[tool] >= BUDGET[tool]:
                raise ToolError(f"Budget for {tool} is used up ({BUDGET[tool]} call(s)). Continue with what you have.")
            self.calls[tool] += 1

    @property
    def family(self) -> str:
        """letter, payslip, contract or form, from triage."""
        t = self.state.triage
        return FAMILY.get(t.doc_type, "letter") if t else "letter"

    def reply_required(self) -> bool:
        s = self.state
        return bool(s.reply_intent or (s.triage and s.triage.needs_response and self.family == "letter"))


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)


class SupervisorAgent(Agent):
    name = "supervisor"
    tier = "smart"
    max_tokens = 1200
    max_steps = 12

    def __init__(
        self,
        router,
        *,
        triage: TriageAgent,
        explainer: ExplainerAgent,
        extractor: ExtractorAgent,
        critic: CriticAgent,
        drafter: DrafterAgent,
        payslip: PayslipAgent,
        contract: ContractAgent,
        form: FormAgent,
        max_extraction_retries: int = 1,
    ):
        super().__init__(router)
        self.analysts = {"payslip": payslip, "contract": contract, "form": form}
        self.triage = triage
        self.explainer = explainer
        self.extractor = extractor
        self.critic = critic
        self.drafter = drafter
        self.max_extraction_retries = max_extraction_retries

    def run(self, ws: Workspace, trace: list[TraceEvent]) -> SupervisorReport:
        s = ws.state
        prompt = (
            "A new document has arrived.\n"
            f"User's requested reply: {s.reply_intent or 'none (decide from triage)'}\n"
            f"User received it on: {s.received_on or 'unknown'}\n"
            f"Bundesland: {s.bundesland} · today: {s.today}\n"
            f"The letter has {len(ws.letter.splitlines())} lines; the specialists can read it."
        )
        return self.react(
            system=SYSTEM,
            prompt=prompt,
            output=SupervisorReport,
            trace=trace,
            tools=self._tools(ws, trace),
            check=lambda _: self._missing_work(ws),
            max_rejections=None,
        )

    # ------------------------------------------------------------------ guarantees

    @staticmethod
    def _missing_work(ws: Workspace) -> list[str]:
        s, missing = ws.state, []
        if not s.triage:
            missing.append("the letter has not been triaged")
        if not s.explanation:
            missing.append("there is no explanation yet")
        elif not ws.review_current:
            missing.append("the current explanation has not been reviewed")
        if not s.extraction:
            if ws.family != "payslip":  # payslips carry no deadlines; extracting them is optional
                missing.append("no facts have been extracted")
        elif not ws.deadlines_current:
            missing.append("deadlines have not been computed from the current extraction")
        if ws.family != "letter" and (s.analysis is None or s.analysis.family != ws.family):
            tool = {"payslip": "analyse_payslip", "contract": "analyse_contract", "form": "guide_form"}[ws.family]
            missing.append(f"this is a {ws.family}, but {tool} has not run")
        if ws.reply_required() and not s.draft:
            missing.append("a reply is required (requested by the user or needed per triage) but none was drafted")
        return missing

    # ------------------------------------------------------------------ specialists as tools

    def _tools(self, ws: Workspace, trace: list[TraceEvent]) -> list[Tool]:
        s = ws.state

        def need(cond, msg: str) -> None:
            if not cond:
                raise ToolError(msg)

        def triage_letter(_: NoParams) -> str:
            ws.spend("triage_letter")
            s.triage = self.triage.run(ws.letter, trace)
            return _dump(s.triage.model_dump(mode="json"))

        def explain_letter(p: FeedbackParams) -> str:
            need(s.triage, "Run triage_letter first.")
            ws.spend("explain_letter")
            explanation = self.explainer.run(ws.letter, s.triage, trace, feedback=p.feedback or None)
            with ws.lock:
                s.explanation, ws.review_current = explanation, False
            return _dump({"summary_en": explanation.summary_en, "key_points": explanation.key_points, "if_ignored": explanation.if_ignored})

        def extract_facts(p: FeedbackParams) -> str:
            need(s.triage, "Run triage_letter first.")
            ws.spend("extract_facts")
            ctx = DeadlineContext(state=s.bundesland, today=s.today, received_on=s.received_on)
            run = self.extractor.run(
                ws.letter, s.triage, trace, ctx, feedback=p.feedback or None, max_rejections=self.max_extraction_retries
            )
            issues, checked = check_grounding(run.extraction, ws.letter, s.triage)
            with ws.lock:
                s.extraction = run.extraction
                ws.grounding, ws.quotes_checked = issues, checked
                ws.extraction_rounds += run.rounds
                ws.repaired += run.rejected
                ws.deadlines_current = False
            ex = run.extraction
            return _dump(
                {
                    "letter_date": ex.letter_date,
                    "deadlines": [{"id": d.id, "kind": d.kind, "rule": d.rule.model_dump(exclude_defaults=True)} for d in ex.deadlines],
                    "amounts": [{"value": a.value, "direction": a.direction, "purpose": a.purpose} for a in ex.amounts],
                    "references": [r.label for r in ex.references],
                    "has_legal_remedy_notice": ex.has_legal_remedy_notice,
                    "verifier_rounds": run.rounds,
                    "fixed_by_extractor": run.rejected,
                    "remaining_errors": [i.message for i in issues if i.severity == "error"],
                    "warnings": [i.message for i in issues if i.severity == "warning"],
                }
            )

        def compute_deadlines(_: NoParams) -> str:
            need(s.extraction, "Run extract_facts first.")
            ws.spend("compute_deadlines")
            ex = s.extraction
            s.deadlines = sorted(
                (
                    resolve_deadline(d, letter_date=ex.letter_date, received_on=s.received_on, state=s.bundesland, today=s.today)
                    for d in ex.deadlines
                ),
                key=lambda r: (r.due_date is None, r.due_date or date.max),
            )
            s.urgency = urgency(s.deadlines)
            ws.deadlines_current = True
            return _dump(
                {
                    "urgency": s.urgency,
                    "deadlines": [{"id": d.id, "due": d.due_date, "days_left": d.days_left, "basis": d.basis} for d in s.deadlines],
                }
            )

        def review_explanation(_: NoParams) -> str:
            need(s.explanation, "Run explain_letter first.")
            ws.spend("review_explanation")
            explanation = s.explanation
            issues = self.critic.run(ws.letter, explanation, trace)
            with ws.lock:
                if s.explanation is explanation:  # a newer explanation needs its own review
                    ws.critic, ws.review_current = issues, True
            return _dump({"issues": [i.message for i in issues]} if issues else {"issues": [], "verdict": "faithful"})

        def draft_reply(p: DraftParams) -> str:
            need(s.triage and s.explanation and s.extraction, "Triage, explanation and extraction must exist before drafting.")
            need(ws.deadlines_current, "Run compute_deadlines on the current extraction first.")
            intent = s.reply_intent or p.intent  # the user's choice always wins
            need(intent is None or intent in REPLY_INTENTS, f"Unknown intent '{intent}'. Choose from: {', '.join(REPLY_INTENTS)}")
            ws.spend("draft_reply")
            out = self.drafter.run(
                ws.letter,
                triage=s.triage,
                explanation=s.explanation,
                extraction=s.extraction,
                deadlines=s.deadlines,
                intent=intent,
                known_placeholders=set(s.redactions),
                trace=trace,
                instructions=p.instructions,
            )
            s.draft = ReplyDraft(**out.model_dump())
            blanks = list(dict.fromkeys(open_placeholders(out.subject_de + "\n" + out.body_de, s.redactions)))
            return _dump({"intent": out.intent, "subject_de": out.subject_de, "blanks_for_user": blanks, "notes_en": out.notes_en})

        def analyse(family: str, tool: str):
            def run(p: FeedbackParams) -> str:
                need(s.triage, "Run triage_letter first.")
                need(
                    ws.family == family,
                    f"Triage classified this document as {s.triage.doc_type.value}, not a {family}. Use the matching specialist.",
                )
                ws.spend(tool)
                rejected: list[str] = []
                analysis = self.analysts[family].run(ws.letter, s.triage, trace, feedback=p.feedback or None, rejected=rejected)
                issues, checked = check_analysis(analysis, ws.letter)
                with ws.lock:
                    s.analysis, ws.analysis_issues, ws.analysis_quotes = analysis, issues, checked
                    ws.repaired += rejected
                summary = analysis.model_dump(mode="json", exclude={"family"})
                summary["fixed_by_specialist"] = rejected
                summary["remaining_errors"] = [i.message for i in issues if i.severity == "error"]
                summary["warnings"] = [i.message for i in issues if i.severity == "warning"]
                return _dump(summary)

            return run

        return [
            Tool("triage_letter", "Classify the letter (sender, type, reply needed).", NoParams, triage_letter),
            Tool("explain_letter", "Have the Explainer write (or revise) the plain-English explanation.", FeedbackParams, explain_letter),
            Tool("extract_facts", "Have the Extractor pull out deadlines, amounts, references and actions.", FeedbackParams, extract_facts),
            Tool("compute_deadlines", "Compute legal due dates from the current extraction (deterministic).", NoParams, compute_deadlines),
            Tool("review_explanation", "Have the Critic check the current explanation against the letter.", NoParams, review_explanation),
            Tool("draft_reply", "Have the Drafter write a German reply for the user to approve.", DraftParams, draft_reply),
            Tool("analyse_payslip", "Have the Payslip agent explain a payslip; the arithmetic is checked.", FeedbackParams, analyse("payslip", "analyse_payslip")),
            Tool("analyse_contract", "Have the Contract agent explain a contract's terms and risks.", FeedbackParams, analyse("contract", "analyse_contract")),
            Tool("guide_form", "Have the Form agent explain each field of a form to fill in.", FeedbackParams, analyse("form", "guide_form")),
        ]

