"""Run persistence: one JSON file per run on local disk, or short-lived memory for the public demo."""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

from .models import RunState

RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


class RunNotFound(KeyError):
    pass


class RunStore:
    def __init__(self, data_dir: Path | str):
        self.dir = Path(data_dir) / "runs"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id):  # also blocks path traversal
            raise RunNotFound(run_id)
        return self.dir / f"{run_id}.json"

    def save(self, state: RunState) -> None:
        path = self._path(state.run_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def load(self, run_id: str) -> RunState:
        path = self._path(run_id)
        if not path.is_file():
            raise RunNotFound(run_id)
        return RunState.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self, limit: int = 50) -> list[RunState]:
        files = sorted(self.dir.glob("*.json"), reverse=True)[:limit]
        return [RunState.model_validate_json(f.read_text(encoding="utf-8")) for f in files]


class MemoryRunStore:
    """Keeps runs in memory for a limited time and never writes to disk (public demo mode)."""

    def __init__(self, ttl_seconds: int = 3600, max_runs: int = 500):
        self.ttl = ttl_seconds
        self.max_runs = max_runs
        self._runs: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()

    def _expire(self) -> None:
        cutoff = time.monotonic() - self.ttl
        while self._runs and (next(iter(self._runs.values()))[0] < cutoff or len(self._runs) > self.max_runs):
            self._runs.popitem(last=False)

    def save(self, state: RunState) -> None:
        with self._lock:
            self._runs.pop(state.run_id, None)
            self._runs[state.run_id] = (time.monotonic(), state.model_dump_json())
            self._expire()

    def load(self, run_id: str) -> RunState:
        with self._lock:
            self._expire()
            if run_id not in self._runs:
                raise RunNotFound(run_id)
            return RunState.model_validate_json(self._runs[run_id][1])

    def list(self, limit: int = 50) -> list[RunState]:
        return []  # visitors must not see each other's runs
