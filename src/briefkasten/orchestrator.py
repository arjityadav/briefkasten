"""Entry point: intake and redaction, then hands the run to the Supervisor agent; owns the approval gate.

    intake -> redact -> Supervisor (LLM) --delegates--> triage | explainer || extractor | deadlines | critic | drafter
                                                   <-- summaries, verifier errors, critic findings --+
           -> awaiting human approval -> export

The Supervisor decides what runs and when; each specialist runs its own tool loop. What
stays in plain code: redaction before any model call, the deterministic deadline maths,
grounding checks, call budgets, and the human approval gate.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime

from .agents import CriticAgent, DrafterAgent, ExplainerAgent, ExtractorAgent, SupervisorAgent, TriageAgent, Workspace
from .config import Settings
from .documents import load_document
from .llm import LLMRouter
from .models import REPLY_INTENTS, RunState, TraceEvent, Verification
from .store import RunStore
from .tools.holidays_de import STATES
from .tools.redact import Redactor, open_placeholders, restore


class ApprovalError(RuntimeError):
    pass


def new_run_id(now: datetime) -> str:
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


class Briefkasten:
    def __init__(self, router: LLMRouter, settings: Settings, store: RunStore | None = None):
        if settings.state.upper() not in STATES:
            raise ValueError(f"Unknown Bundesland '{settings.state}'")
        self.settings = settings
        self.router = router
        self.store = store or RunStore(settings.data_dir)
        self.supervisor = SupervisorAgent(
            router,
            triage=TriageAgent(router),
            explainer=ExplainerAgent(router),
            extractor=ExtractorAgent(router),
            critic=CriticAgent(router),
            drafter=DrafterAgent(router),
            max_extraction_retries=settings.max_extraction_retries,
        )

    # ------------------------------------------------------------------ run

    def process_file(self, data: bytes, filename: str, **kwargs) -> RunState:
        intake_trace: list[TraceEvent] = []
        text = load_document(data, filename, self.router, intake_trace)
        return self.process(text, source_name=filename, extra_trace=intake_trace, **kwargs)

    def process(
        self,
        text: str,
        *,
        source_name: str = "letter.txt",
        received_on: date | None = None,
        reply_intent: str | None = None,
        today: date | None = None,
        extra_trace: list[TraceEvent] | None = None,
    ) -> RunState:
        if reply_intent and reply_intent not in REPLY_INTENTS:
            raise ValueError(f"Unknown intent '{reply_intent}'. Choose from: {', '.join(REPLY_INTENTS)}")
        if not text.strip():
            raise ValueError("The letter is empty")

        now = datetime.now()
        redacted = Redactor(self.settings.redact_terms).redact(text)
        state = RunState(
            run_id=new_run_id(now),
            created_at=now,
            source_name=source_name,
            bundesland=self.settings.state.upper(),
            today=today or date.today(),
            received_on=received_on,
            reply_intent=reply_intent,
            text_redacted=redacted.text,
            redactions=redacted.mapping,
            trace=list(extra_trace or []),
        )
        ws = Workspace(state)
        try:
            report = self.supervisor.run(ws, state.trace)
            state.supervisor_notes = report.notes
            state.status = "awaiting_approval" if state.draft else "done"
        except Exception as e:  # keep partial results for inspection
            state.status = "failed"
            state.error = f"{type(e).__name__}: {e}"
        finally:
            if state.extraction:
                issues = ws.grounding + ws.critic
                state.verification = Verification(
                    passed=not any(i.severity == "error" for i in issues),
                    issues=issues,
                    quotes_checked=ws.quotes_checked,
                    extraction_rounds=ws.extraction_rounds,
                    repaired=ws.repaired,
                )
            self.store.save(state)
        return state

    # ------------------------------------------------------------------ human in the loop

    def approve(
        self,
        run_id: str,
        *,
        body_de: str | None = None,
        subject_de: str | None = None,
        allow_open_placeholders: bool = False,
    ) -> RunState:
        state = self.store.load(run_id)
        if not state.draft or state.status != "awaiting_approval":
            raise ApprovalError(f"Run {run_id} has no draft waiting for approval (status: {state.status})")
        draft = state.draft
        if body_de is not None and body_de.strip() != draft.body_de.strip():
            draft.body_de, draft.edited_by_user = body_de.strip(), True
        if subject_de is not None and subject_de.strip() != draft.subject_de.strip():
            draft.subject_de, draft.edited_by_user = subject_de.strip(), True
        blanks = open_placeholders(draft.subject_de + "\n" + draft.body_de, state.redactions)
        if blanks and not allow_open_placeholders:
            raise ApprovalError("Fill in these placeholders before approving: " + ", ".join(dict.fromkeys(blanks)))
        draft.status = "approved"
        draft.decided_at = datetime.now()
        state.status = "done"
        self.store.save(state)
        return state

    def reject(self, run_id: str, reason: str | None = None) -> RunState:
        state = self.store.load(run_id)
        if not state.draft or state.status != "awaiting_approval":
            raise ApprovalError(f"Run {run_id} has no draft waiting for approval (status: {state.status})")
        state.draft.status = "rejected"
        state.draft.rejection_reason = reason
        state.draft.decided_at = datetime.now()
        state.status = "done"
        self.store.save(state)
        return state

    def export_letter(self, run_id: str) -> str:
        """Render the approved reply as a plain-text letter with real values restored."""
        state = self.store.load(run_id)
        if not state.draft or state.draft.status != "approved":
            raise ApprovalError("Only approved drafts can be exported")
        s = self.settings
        sender = "\n".join(x for x in (s.sender_name or "[Ihr Name]", s.sender_address or "[Ihre Anschrift]") if x)
        recipient = (state.triage.sender_name if state.triage else None) or "[Empfänger]"
        letter = (
            f"{sender}\n\n\n{recipient}\n\n\n"
            f"{date.today():%d.%m.%Y}\n\n"
            f"{state.draft.subject_de}\n\n"
            f"{state.draft.body_de}\n\n"
            f"Mit freundlichen Grüßen\n\n\n{s.sender_name or '[Unterschrift]'}\n"
        )
        return restore(letter, state.redactions)
