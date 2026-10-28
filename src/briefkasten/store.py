"""Run persistence: one JSON file per run on local disk."""

from __future__ import annotations

import re
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
