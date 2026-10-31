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
from collections import Counter
from datetime import date, datetime

from .agents import (
    ContractAgent,
    CriticAgent,
    DrafterAgent,
    ExplainerAgent,
    ExtractorAgent,
    FormAgent,
    LiveTrace,
    PayslipAgent,
    Progress,
    SupervisorAgent,
    TriageAgent,
    Workspace,
)
from .config import Settings
from .documents import load_documents
from .observability import current_run, observer
from .readers import build_image_reader
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
            payslip=PayslipAgent(router),
            contract=ContractAgent(router),
            form=FormAgent(router),
            max_extraction_retries=settings.max_extraction_retries,
        )

    # ------------------------------------------------------------------ run

    def process_file(self, data: bytes, filename: str, progress: Progress | None = None, **kwargs) -> RunState:
        return self.process_files([(data, filename)], progress=progress, **kwargs)

    def process_files(self, files: list[tuple[bytes, str]], progress: Progress | None = None, **kwargs) -> RunState:
        """Several files that make up one document, e.g. one photo per page, in order."""
        name = files[0][1] if len(files) == 1 else f"{files[0][1]} (+{len(files) - 1} more)"
        with self._trace(name, kwargs.get("session_id")):  # intake (OCR, vision) belongs to the same trace
            return self._process_files(files, name, progress, **kwargs)

    def _process_files(self, files: list[tuple[bytes, str]], name: str, progress: Progress | None, **kwargs) -> RunState:
        intake_trace: list[TraceEvent] = LiveTrace(progress=progress)
        used: list[str] = []

        def read_images(images, trace):  # built on first use: text documents never need an image reader
            used.append(self.settings.image_reader)
            return build_image_reader(self.settings, self.router)(images, trace)

        text = load_documents(files, read_images, intake_trace)
        return self.process(
            text, source_name=name, extra_trace=intake_trace, progress=progress, image_reader=used[0] if used else None, **kwargs
        )

    def process(
        self,
        text: str,
        *,
        source_name: str = "letter.txt",
        received_on: date | None = None,
        reply_intent: str | None = None,
        today: date | None = None,
        extra_trace: list[TraceEvent] | None = None,
        progress: Progress | None = None,
        image_reader: str | None = None,
        session_id: str | None = None,
    ) -> RunState:
        """Run the agents on a letter. `progress`, if given, is told about every agent step as it happens.

        With tracing on, the run is one Langfuse trace (grouped by `session_id`, e.g. an eval run).
        """
        with self._trace(source_name, session_id):
            return self._process(text, source_name, received_on, reply_intent, today, extra_trace, progress, image_reader)

    def _trace(self, name: str, session_id: str | None):
        r = self.router
        model = r.for_tier("smart").model if r else "none"
        tags = ["briefkasten", self.settings.provider] + (["replay"] if model == "scripted-replay" else [])
        metadata = {"bundesland": self.settings.state.upper(), "model": model, "model_fast": r.for_tier("fast").model if r else "none"}
        return observer().run(name, session_id=session_id, tags=tags, metadata=metadata)

    def _process(self, text, source_name, received_on, reply_intent, today, extra_trace, progress, image_reader) -> RunState:
        if reply_intent and reply_intent not in REPLY_INTENTS:
            raise ValueError(f"Unknown intent '{reply_intent}'. Choose from: {', '.join(REPLY_INTENTS)}")
        if not text.strip():
            raise ValueError("The letter is empty")

        now = datetime.now()
        redacted = Redactor(self.settings.redact_terms, auto=self.settings.auto_redact).redact(text)
        state = RunState(
            run_id=new_run_id(now),
            created_at=now,
            source_name=source_name,
            bundesland=self.settings.state.upper(),
            today=today or date.today(),
            received_on=received_on,
            reply_intent=reply_intent,
            image_reader=image_reader,
            text_redacted=redacted.text,
            redactions=redacted.mapping,
            trace=list(extra_trace or []),
        )
        state.trace = LiveTrace(state.trace, progress)  # same list type to pydantic, plus live reporting
        if progress is not None and hasattr(progress, "info"):
            progress.info(f"Redactor masked {len(redacted.mapping)} personal identifier(s) before any model call")
        root = current_run()
        state.trace_id = root.trace_id
        # Counts by kind only: the values themselves never leave this machine.
        masked = Counter(ph.strip("[]").rsplit("_", 1)[0] for ph in redacted.mapping)
        root.update(
            input={"letter": redacted.text, "reply_intent": reply_intent, "received_on": received_on, "image_reader": image_reader},
            metadata={"masked": dict(masked), "run_id": state.run_id},
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
            if state.extraction or state.analysis:
                issues = ws.grounding + ws.analysis_issues + ws.critic
                state.verification = Verification(
                    passed=not any(i.severity == "error" for i in issues),
                    issues=issues,
                    quotes_checked=ws.quotes_checked + ws.analysis_quotes,
                    extraction_rounds=ws.extraction_rounds,
                    repaired=ws.repaired,
                )
            self._finish_trace(state, root)
            self.store.save(state)
        return state

    def _finish_trace(self, state: RunState, root) -> None:
        if not state.trace_id:
            return
        root.update(
            output={
                "status": state.status,
                "doc_type": state.triage.doc_type.value if state.triage else None,
                "urgency": state.urgency,
                "deadlines": [{"id": d.id, "due": d.due_date, "basis": d.basis} for d in state.deadlines],
                "analysis": state.analysis.family if state.analysis else None,
                "draft": state.draft.intent if state.draft else None,
                "notes": state.supervisor_notes,
            },
            **({"level": "ERROR", "status_message": state.error} if state.error else {}),
        )
        v = state.verification
        scores: dict[str, float | str] = {
            "status": state.status,
            "agent_steps": len(state.trace),
            "tokens": sum(e.input_tokens + e.output_tokens for e in state.trace),
        }
        if v:
            scores |= {"checks_passed": float(v.passed), "quotes_checked": v.quotes_checked, "repaired": len(v.repaired)}
        for name, value in scores.items():
            root.score_trace(name, value)
        state.trace_url = observer().trace_url(state.trace_id)

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
