"""HTTP API and web UI (FastAPI).

With BRIEFKASTEN_DEMO_ONLY=1 the app is safe to host publicly: it only replays the fictional
sample letters, accepts no uploads, needs no model API key, keeps runs in memory for an hour,
and never lists one visitor's runs to another.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings
from .demo import DEMO_DESCRIPTIONS, DEMO_REDACT_TERMS, available_replays, load_replay
from .documents import UnsupportedDocument
from .jobs import JobRegistry
from .llm import LLMError, build_router
from .models import REPLY_INTENTS
from .orchestrator import ApprovalError, Briefkasten
from .store import MemoryRunStore, RunNotFound, RunStore
from .tools.holidays_de import STATES

MAX_UPLOAD = 15 * 1024 * 1024
DEMO_PACE = 0.7  # seconds per recorded model call, so the agents can be watched at work
MAX_FILES = 12  # e.g. one photo per page
WEB = Path(__file__).parent / "web"
DIST = WEB / "dist"  # the TypeScript UI, built from frontend/ with `npm run build`

app = FastAPI(title="Briefkasten", version="0.1.0", description="Multi-agent assistant for official German mail")
jobs = JobRegistry()
if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@lru_cache
def settings() -> Settings:
    return Settings.from_env()


@lru_cache
def _memory_store() -> MemoryRunStore:
    return MemoryRunStore()


def store() -> RunStore | MemoryRunStore:
    return _memory_store() if settings().demo_only else RunStore(settings().data_dir)


def pipeline() -> Briefkasten:
    if settings().demo_only:
        raise HTTPException(403, DEMO_REFUSAL)
    try:
        return Briefkasten(build_router(settings()), settings())
    except LLMError as e:
        raise HTTPException(503, str(e)) from e


def offline() -> Briefkasten:
    return Briefkasten(None, settings(), store())  # type: ignore[arg-type]  # approval/export never call a model


DEMO_REFUSAL = "This public demo only runs the sample letters. Run Briefkasten yourself to analyse your own mail."


@app.middleware("http")
async def demo_only_guard(request: Request, call_next):
    """In demo-only mode, refuse uploads before the request body is read, so no real letter is ever received."""
    if settings().demo_only and request.method == "POST" and request.url.path.rstrip("/") in ("/api/runs", "/api/jobs"):
        return JSONResponse({"detail": DEMO_REFUSAL}, status_code=403)
    return await call_next(request)


class ApproveBody(BaseModel):
    body_de: str | None = None
    subject_de: str | None = None


class RejectBody(BaseModel):
    reason: str | None = None


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    built = DIST / "index.html"
    return FileResponse(built if built.is_file() else WEB / "index.html")  # simple page until the UI is built


@app.get("/api/meta")
def meta() -> dict:
    s = settings()
    return {
        "intents": REPLY_INTENTS,
        "states": STATES,
        "default_state": s.state,
        "provider": "recorded replays" if s.demo_only else s.provider,
        "models": {} if s.demo_only else {"smart": s.model, "fast": s.model_fast},
        "demos": available_replays(),
        "demo_only": s.demo_only,
        "image_reader": s.image_reader,
        "demo_descriptions": DEMO_DESCRIPTIONS,
        "imprint_url": s.imprint_url,
        "privacy_url": s.privacy_url,
    }


@app.post("/api/runs")
async def create_run(
    file: list[UploadFile] | None = File(None),
    text: str | None = Form(None),
    received_on: date | None = Form(None),
    intent: str | None = Form(None),
    bundesland: str | None = Form(None),
) -> dict:
    bk = pipeline()  # refuses first in demo-only mode, before reading any upload
    if intent and intent not in REPLY_INTENTS:
        raise HTTPException(422, f"Unknown intent '{intent}'")
    if bundesland:
        if bundesland.upper() not in STATES:
            raise HTTPException(422, f"Unknown Bundesland '{bundesland}'")
        bk.settings = replace(bk.settings, state=bundesland.upper())
    kwargs = {"received_on": received_on, "reply_intent": intent or None}
    try:
        uploads = [f for f in file or [] if f.filename]
        if uploads:
            files, total = [], 0
            for f in uploads[:MAX_FILES]:
                data = await f.read(MAX_UPLOAD + 1)
                total += len(data)
                if total > MAX_UPLOAD:
                    raise HTTPException(413, "Files too large (max 15 MB in total)")
                files.append((data, f.filename))
            state = bk.process_files(files, **kwargs)
        elif text and text.strip():
            state = bk.process(text, source_name="pasted-letter.txt", **kwargs)
        else:
            raise HTTPException(422, "Upload a file or paste the letter text")
    except UnsupportedDocument as e:
        raise HTTPException(415, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e
    return state.public_view()


# --------------------------------------------------------------------------- live jobs (web UI)


async def _read_uploads(file: list[UploadFile] | None) -> list[tuple[bytes, str]]:
    files, total = [], 0
    for f in [f for f in file or [] if f.filename][:MAX_FILES]:
        data = await f.read(MAX_UPLOAD + 1)
        total += len(data)
        if total > MAX_UPLOAD:
            raise HTTPException(413, "Files too large (max 15 MB in total)")
        files.append((data, f.filename))
    return files


@app.post("/api/jobs")
async def create_job(
    file: list[UploadFile] | None = File(None),
    text: str | None = Form(None),
    received_on: date | None = Form(None),
    intent: str | None = Form(None),
    bundesland: str | None = Form(None),
) -> dict:
    """Start a run in the background; follow it at /api/jobs/{job_id}/events."""
    bk = pipeline()
    if intent and intent not in REPLY_INTENTS:
        raise HTTPException(422, f"Unknown intent '{intent}'")
    if bundesland:
        if bundesland.upper() not in STATES:
            raise HTTPException(422, f"Unknown Bundesland '{bundesland}'")
        bk.settings = replace(bk.settings, state=bundesland.upper())
    files = await _read_uploads(file)
    if not files and not (text and text.strip()):
        raise HTTPException(422, "Upload a file or paste the letter text")
    kwargs = {"received_on": received_on, "reply_intent": intent or None}

    def work(progress):
        if files:
            return bk.process_files(files, progress=progress, **kwargs)
        return bk.process(text, source_name="pasted-letter.txt", progress=progress, **kwargs)

    return {"job_id": jobs.start(work).id}


@app.post("/api/demo/{name}/job")
def create_demo_job(name: str, pace: float = DEMO_PACE) -> dict:
    """Replay a sample live: recorded model turns at a watchable pace, every tool running for real."""
    if name not in available_replays():
        raise HTTPException(404, f"Unknown demo. Available: {', '.join(available_replays())}")
    letter, intent, router = load_replay(name, delay=min(max(pace, 0.0), 3.0))
    bk = Briefkasten(router, replace(settings(), redact_terms=DEMO_REDACT_TERMS), store())
    return {"job_id": jobs.start(lambda progress: bk.process(letter, source_name=f"{name}.txt", reply_intent=intent, progress=progress)).id}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events: every agent step as it happens, then `done` with the run (or `error`)."""
    import asyncio
    import json

    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    start = int(request.headers.get("last-event-id", -1)) + 1

    async def stream():
        index = start
        while True:
            events, done = job.since(index)
            for e in events:
                yield f"id: {index}\nevent: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"
                index += 1
            if done and not events:
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


@app.post("/api/demo/{name}")
def run_demo(name: str) -> dict:
    """Replay recorded agent outputs for a sample letter (works without an API key)."""
    if name not in available_replays():
        raise HTTPException(404, f"Unknown demo. Available: {', '.join(available_replays())}")
    letter, intent, router = load_replay(name)
    bk = Briefkasten(router, replace(settings(), redact_terms=DEMO_REDACT_TERMS), store())
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
