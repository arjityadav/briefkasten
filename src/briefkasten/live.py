"""Live terminal view of a run: prints each agent step as it happens (to stderr)."""

from __future__ import annotations

import logging
import textwrap
import threading
import time

import click

from .models import TraceEvent

COLORS = {
    "supervisor": "magenta",
    "triage": "blue",
    "explainer": "cyan",
    "extractor": "green",
    "critic": "yellow",
    "drafter": "bright_blue",
    "transcriber": "white",
}


class ConsoleProgress:
    """Implements agents.Progress. Specialists are indented under the supervisor that called them."""

    def __init__(self, show_thoughts: bool = True):
        self.show_thoughts = show_thoughts
        self._t0 = time.perf_counter()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ Progress

    def started(self, agent: str, step: int) -> None:
        self._line(agent, click.style(f"thinking… (step {step})", dim=True))

    def decided(self, event: TraceEvent) -> None:
        acts = ", ".join(event.tool_calls)
        action = click.style(f"→ {acts}", bold=True) if acts else click.style("(no action)", dim=True)
        meta = click.style(f"  {event.duration_ms / 1000:.1f}s · {event.input_tokens}→{event.output_tokens} tok", dim=True)
        lines = []
        if self.show_thoughts and event.thought:
            lines = textwrap.wrap(" ".join(event.thought.split()), 96, max_lines=3, placeholder=" …")
        self._line(event.agent, action + meta, [click.style(f"“{l}”" if len(lines) == 1 else l, dim=True, italic=True) for l in lines])

    def problem(self, event: TraceEvent) -> None:
        note = textwrap.shorten(event.note or "", 200, placeholder=" …")
        self._line(event.agent, click.style(f"✗ {note}", fg="yellow"))

    # ------------------------------------------------------------------ other messages

    def info(self, text: str) -> None:
        with self._lock:
            click.echo(click.style(f"{self._clock()}  {text}", dim=True), err=True)

    def _clock(self) -> str:
        s = int(time.perf_counter() - self._t0)
        return f"{s // 60:>2}:{s % 60:02d}"

    def _line(self, agent: str, text: str, extra: list[str] | None = None) -> None:
        indent = "" if agent in ("supervisor", "transcriber") else "  "
        name = click.style(f"{indent}{agent:<11}", fg=COLORS.get(agent), bold=agent == "supervisor")
        pad = " " * (len(self._clock()) + 2 + len(indent) + 11 + 1)
        with self._lock:
            click.echo(f"{click.style(self._clock(), dim=True)}  {name} {text}", err=True)
            for line in extra or []:
                click.echo(pad + line, err=True)


class _ProgressLogHandler(logging.Handler):
    def __init__(self, progress: ConsoleProgress):
        super().__init__(logging.WARNING)
        self.progress = progress

    def emit(self, record: logging.LogRecord) -> None:
        self.progress.info(f"⏳ {record.getMessage()}")


def attach_http_logging(progress: ConsoleProgress) -> None:
    """Show rate-limit and connection retries in the live view."""
    logger = logging.getLogger("briefkasten.http")
    logger.handlers = [h for h in logger.handlers if not isinstance(h, _ProgressLogHandler)]
    logger.addHandler(_ProgressLogHandler(progress))
    logger.propagate = False
