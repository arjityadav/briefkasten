"""Background runs with a live event stream, for the web UI.

A job runs the agents in a thread and records every step (via the agents' Progress hook) as an
event. The browser follows them over Server-Sent Events and can reconnect from any event index.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from typing import Callable

from .models import RunState, TraceEvent

JOB_TTL = 3600  # seconds a finished job's events are kept
MAX_JOBS = 200


class Job:
    def __init__(self) -> None:
        self.id = secrets.token_hex(8)
        self.events: list[dict] = []
        self.done = False
        self.created = time.monotonic()
        self._t0 = time.perf_counter()
        self._lock = threading.Lock()

    def emit(self, type: str, **data) -> None:
        with self._lock:
            self.events.append({"type": type, "t": round(time.perf_counter() - self._t0, 2), **data})
            if type in ("done", "error"):
                self.done = True

    def since(self, index: int) -> tuple[list[dict], bool]:
        with self._lock:
            return self.events[index:], self.done


class JobProgress:
    """agents.Progress that records steps as job events."""

    def __init__(self, job: Job):
        self.job = job

    def started(self, agent: str, step: int) -> None:
        self.job.emit("started", agent=agent, step=step)

    def decided(self, event: TraceEvent) -> None:
        self.job.emit(
            "decided",
            agent=event.agent,
            step=event.attempt,
            tools=event.tool_calls,
            thought=event.thought,
            duration_ms=event.duration_ms,
            tokens=event.input_tokens + event.output_tokens,
        )

    def problem(self, event: TraceEvent) -> None:
        self.job.emit("problem", agent=event.agent, step=event.attempt, note=event.note)

    def info(self, text: str) -> None:
        self.job.emit("info", text=text)


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        logging.getLogger("briefkasten.http").addHandler(_BroadcastHandler(self))

    def start(self, work: Callable[[JobProgress], RunState]) -> Job:
        job = Job()
        with self._lock:
            self._expire()
            self._jobs[job.id] = job

        def run() -> None:
            try:
                state = work(JobProgress(job))
                job.emit("done", run=state.public_view())
            except Exception as e:  # reported to the browser instead of a silent thread death
                job.emit("error", detail=f"{type(e).__name__}: {e}")

        threading.Thread(target=run, name=f"job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def running(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if not j.done]

    def _expire(self) -> None:
        cutoff = time.monotonic() - JOB_TTL
        for job_id in [k for k, j in self._jobs.items() if j.done and j.created < cutoff]:
            del self._jobs[job_id]
        while len(self._jobs) >= MAX_JOBS:
            del self._jobs[next(iter(self._jobs))]


class _BroadcastHandler(logging.Handler):
    """Rate-limit and retry messages from the HTTP client, shown in every running job's log."""

    def __init__(self, registry: JobRegistry):
        super().__init__(logging.WARNING)
        self.registry = registry

    def emit(self, record: logging.LogRecord) -> None:
        for job in self.registry.running():
            job.emit("info", text=f"⏳ {record.getMessage()}")
