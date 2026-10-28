"""HTTP API and web UI (FastAPI)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from .config import Settings
from .demo import DEMO_REDACT_TERMS, available_replays, load_replay
from .documents import UnsupportedDocument
from .llm import LLMError, build_router
from .models import REPLY_INTENTS
from .orchestrator import ApprovalError, Briefkasten
from .store import RunNotFound, RunStore
from .tools.holidays_de import STATES

MAX_UPLOAD = 15 * 1024 * 1024
WEB = Path(__file__).parent / "web"

app = FastAPI(title="Briefkasten", version="0.1.0", description="Multi-agent assistant for official German mail")


@lru_cache
def settings() -> Settings:
    return Settings.from_env()


def store() -> RunStore:
    return RunStore(settings().data_dir)


def pipeline() -> Briefkasten:
    try:
        return Briefkasten(build_router(settings()), settings())
    except LLMError as e:
        raise HTTPException(503, str(e)) from e


def offline() -> Briefkasten:
    return Briefkasten(None, settings())  # type: ignore[arg-type]  # approval/export never call a model


class ApproveBody(BaseModel):
    body_de: str | None = None
    subject_de: str | None = None


class RejectBody(BaseModel):
    reason: str | None = None


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    s = settings()
    return {
        "intents": REPLY_INTENTS,
        "states": STATES,
        "default_state": s.state,
        "provider": s.provider,
        "models": {"smart": s.model, "fast": s.model_fast},
        "demos": available_replays(),
    }


@app.post("/api/runs")
async def create_run(
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    received_on: date | None = Form(None),
    intent: str | None = Form(None),
    bundesland: str | None = Form(None),
) -> dict:
    if intent and intent not in REPLY_INTENTS:
        raise HTTPException(422, f"Unknown intent '{intent}'")
    bk = pipeline()
    if bundesland:
        if bundesland.upper() not in STATES:
            raise HTTPException(422, f"Unknown Bundesland '{bundesland}'")
        bk.settings = replace(bk.settings, state=bundesland.upper())
    kwargs = {"received_on": received_on, "reply_intent": intent or None}
    try:
        if file is not None and file.filename:
            data = await file.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, "File too large (max 15 MB)")
            state = bk.process_file(data, file.filename, **kwargs)
        elif text and text.strip():
            state = bk.process(text, source_name="pasted-letter.txt", **kwargs)
        else:
            raise HTTPException(422, "Upload a file or paste the letter text")
    except UnsupportedDocument as e:
        raise HTTPException(415, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e
    return state.public_view()


@app.post("/api/demo/{name}")
def run_demo(name: str) -> dict:
    """Replay recorded agent outputs for a sample letter (works without an API key)."""
    if name not in available_replays():
        raise HTTPException(404, f"Unknown demo. Available: {', '.join(available_replays())}")
    letter, intent, router = load_replay(name)
    bk = Briefkasten(router, replace(settings(), redact_terms=DEMO_REDACT_TERMS))
    return bk.process(letter, source_name=f"{name}.txt", reply_intent=intent).public_view()


@app.get("/api/runs")
def list_runs(limit: int = 20) -> list[dict]:
    out = []
    for s in store().list(limit=min(limit, 100)):
        out.append(
            {
                "run_id": s.run_id,
                "created_at": s.created_at,
                "status": s.status,
                "urgency": s.urgency,
                "sender": s.triage.sender_name if s.triage else None,
                "next_deadline": min((d.due_date for d in s.deadlines if d.due_date), default=None),
            }
        )
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    try:
        return store().load(run_id).public_view()
    except RunNotFound:
        raise HTTPException(404, "Run not found") from None


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: str, body: ApproveBody) -> dict:
    try:
        return offline().approve(run_id, body_de=body.body_de, subject_de=body.subject_de).public_view()
    except RunNotFound:
        raise HTTPException(404, "Run not found") from None
    except ApprovalError as e:
        raise HTTPException(409, str(e)) from e


@app.post("/api/runs/{run_id}/reject")
def reject(run_id: str, body: RejectBody) -> dict:
    try:
        return offline().reject(run_id, body.reason).public_view()
    except RunNotFound:
        raise HTTPException(404, "Run not found") from None
    except ApprovalError as e:
        raise HTTPException(409, str(e)) from e


@app.get("/api/runs/{run_id}/letter", response_class=PlainTextResponse)
def letter(run_id: str) -> PlainTextResponse:
    try:
        text = offline().export_letter(run_id)
    except RunNotFound:
        raise HTTPException(404, "Run not found") from None
    except ApprovalError as e:
        raise HTTPException(409, str(e)) from e
    return PlainTextResponse(text, headers={"content-disposition": f'attachment; filename="antwort-{run_id}.txt"'})
