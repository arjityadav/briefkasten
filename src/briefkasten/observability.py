"""Tracing to Langfuse: every run as a nested trace of agents, model calls and tool calls.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (and the `observability` extra is
installed). LANGFUSE_BASE_URL picks the server: Langfuse Cloud EU by default, or a self-hosted one.
BRIEFKASTEN_TRACING=off disables it even with keys present.

Privacy: the trace holds what the models already see, i.e. redacted text. Two exceptions are kept
out on purpose: the unredacted intake (raw letter, scanned images and their transcription) is never
sent, and a mask hook re-applies the identifier patterns to everything as a second layer.

    run (agent)                         one trace per letter, scored at the end
     ├─ supervisor (agent)
     │   ├─ supervisor step 1 (generation)   model, prompt, reply, tool calls, tokens
     │   ├─ triage_letter (tool)
     │   │   └─ triage (agent) ─ steps, find_in_letter, submit
     │   └─ ...                         parallel delegations nest correctly across threads
     └─ scores: checks_passed, quotes_checked, repaired, agent_steps, tokens
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator

from .tools.redact import PATTERNS

log = logging.getLogger("briefkasten.observability")

Kind = str  # "span" | "agent" | "generation" | "tool"


class Observation:
    """A no-op observation; the Langfuse one forwards to the SDK."""

    trace_id: str | None = None

    def update(self, **fields: Any) -> None:
        pass

    def event(self, name: str, *, level: str = "DEFAULT", message: str | None = None, metadata: Any = None) -> None:
        pass

    def score_trace(self, name: str, value: float | str, comment: str | None = None) -> None:
        pass


_current_run: ContextVar[Observation | None] = ContextVar("briefkasten_run", default=None)


def current_run() -> Observation:
    """The root observation of the run in progress (a no-op outside a run)."""
    return _current_run.get() or Observation()


class Observer:
    """Default: tracing off. Every hook is a no-op, so the agents never check whether tracing is on."""

    enabled = False

    @contextmanager
    def run(self, name: str, *, input: Any = None, session_id: str | None = None, tags: list[str] | None = None, metadata: dict | None = None) -> Generator[Observation, None, None]:
        """One trace per letter. Nested calls (intake, then processing) reuse the run already open."""
        if (existing := _current_run.get()) is not None:
            yield existing
            return
        with self._start_run(name, input=input, session_id=session_id, tags=tags, metadata=metadata) as obs:
            token = _current_run.set(obs)
            try:
                yield obs
            finally:
                _current_run.reset(token)

    @contextmanager
    def _start_run(self, name, *, input, session_id, tags, metadata) -> Generator[Observation, None, None]:
        yield Observation()

    @contextmanager
    def observe(self, name: str, *, kind: Kind = "span", input: Any = None, metadata: Any = None, model: str | None = None) -> Generator[Observation, None, None]:
        yield Observation()

    def score(self, trace_id: str | None, name: str, value: float | str, comment: str | None = None) -> None:
        pass

    def trace_url(self, trace_id: str | None) -> str | None:
        return None

    def flush(self) -> None:
        pass


# --------------------------------------------------------------------------- Langfuse


def mask_identifiers(*, data: Any, **_: Any) -> Any:
    """Second privacy layer: mask anything that still looks like an IBAN, tax ID, phone number..."""
    if isinstance(data, str):
        for label, pattern in PATTERNS:
            data = pattern.sub(f"[{label}]", data)
        return data
    if isinstance(data, dict):
        return {k: mask_identifiers(data=v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask_identifiers(data=v) for v in data]
    return data


class _LangfuseObservation(Observation):
    def __init__(self, obs: Any):
        self._obs = obs
        self.trace_id = getattr(obs, "trace_id", None)

    def update(self, **fields: Any) -> None:
        self._obs.update(**fields)

    def event(self, name: str, *, level: str = "DEFAULT", message: str | None = None, metadata: Any = None) -> None:
        self._obs.create_event(name=name, level=level, status_message=message, metadata=metadata)

    def score_trace(self, name: str, value: float | str, comment: str | None = None) -> None:
        self._obs.score_trace(name=name, value=value, comment=comment)


class LangfuseObserver(Observer):
    enabled = True

    def __init__(self, client: Any):
        self.client = client

    @contextmanager
    def _start_run(self, name, *, input, session_id, tags, metadata):
        from langfuse import propagate_attributes

        with propagate_attributes(trace_name=name, session_id=session_id, tags=tags, metadata=metadata):
            with self.client.start_as_current_observation(name="run", as_type="agent", input=input) as obs:
                yield _LangfuseObservation(obs)

    @contextmanager
    def observe(self, name, *, kind="span", input=None, metadata=None, model=None):
        kwargs: dict[str, Any] = {"name": name, "as_type": kind, "input": input, "metadata": metadata}
        if model:
            kwargs["model"] = model
        with self.client.start_as_current_observation(**kwargs) as obs:
            yield _LangfuseObservation(obs)

    def score(self, trace_id, name, value, comment=None):
        if trace_id:
            self.client.create_score(trace_id=trace_id, name=name, value=value, comment=comment)

    def trace_url(self, trace_id):
        try:
            return self.client.get_trace_url(trace_id=trace_id) if trace_id else None
        except Exception:  # needs the API; a missing link must never break a run
            return None

    def flush(self):
        self.client.flush()


# --------------------------------------------------------------------------- the process-wide observer

_observer: Observer | None = None
_lock = threading.Lock()


def build_observer(env: dict[str, str] | os._Environ = os.environ) -> Observer:
    if env.get("BRIEFKASTEN_TRACING", "").strip().lower() in ("0", "off", "false", "no"):
        return Observer()
    if not (env.get("LANGFUSE_PUBLIC_KEY") and env.get("LANGFUSE_SECRET_KEY")):
        return Observer()
    try:
        from langfuse import Langfuse
    except ImportError:
        log.warning("LANGFUSE_* keys are set but the langfuse package is missing: pip install 'briefkasten[observability]'")
        return Observer()
    client = Langfuse(
        public_key=env["LANGFUSE_PUBLIC_KEY"],
        secret_key=env["LANGFUSE_SECRET_KEY"],
        base_url=env.get("LANGFUSE_BASE_URL") or env.get("LANGFUSE_HOST") or "https://cloud.langfuse.com",
        environment=env.get("BRIEFKASTEN_ENVIRONMENT") or None,
        mask=mask_identifiers,
    )
    return LangfuseObserver(client)


def observer() -> Observer:
    """The observer for this process, built from the environment on first use."""
    global _observer
    with _lock:
        if _observer is None:
            _observer = build_observer()
        return _observer


def set_observer(obs: Observer | None) -> None:
    """Replace the process-wide observer (tests), or reset it to be rebuilt from the environment."""
    global _observer
    with _lock:
        _observer = obs
