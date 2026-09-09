"""Versioned portfolio storage.

Layout of one portfolio directory:
    HEAD                  -> current version number (text)
    versions/000001.json  -> {"version", "created_at", "message", "reverted_from", "portfolio"}

Versions are immutable and numbered from 1. `commit` appends. `revert(n)` appends a new
version whose content is a copy of version n, so history is never rewritten.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from pluto.core.model import Portfolio


class VersionInfo(BaseModel):
    version: int
    created_at: datetime
    message: str
    reverted_from: int | None = None
    transactions: int = 0


class VersionRecord(BaseModel):
    version: int
    created_at: datetime
    message: str
    reverted_from: int | None = None
    portfolio: Portfolio

    def info(self) -> VersionInfo:
        return VersionInfo(
            version=self.version,
            created_at=self.created_at,
            message=self.message,
            reverted_from=self.reverted_from,
            transactions=len(self.portfolio.transactions),
        )


class StoreError(RuntimeError):
    pass


class VersionStore:
    def __init__(self, root: Path):
        self.root = root
        self.versions_dir = root / "versions"
        self.head_path = root / "HEAD"

    # --- reading ---------------------------------------------------------------------
    def exists(self) -> bool:
        return self.head_path.exists()

    def head(self) -> int:
        if not self.exists():
            raise StoreError(f"no portfolio at {self.root}")
        return int(self.head_path.read_text().strip())

    def _path(self, version: int) -> Path:
        return self.versions_dir / f"{version:06d}.json"

    def record(self, version: int | None = None) -> VersionRecord:
        v = self.head() if version is None else version
        p = self._path(v)
        if not p.exists():
            raise StoreError(f"version {v} does not exist")
        return VersionRecord.model_validate_json(p.read_text())

    def load(self, version: int | None = None) -> Portfolio:
        return self.record(version).portfolio

    def history(self) -> list[VersionInfo]:
        if not self.versions_dir.exists():
            return []
        out = [VersionRecord.model_validate_json(p.read_text()).info() for p in self._files()]
        return sorted(out, key=lambda i: i.version)

    def _files(self) -> list[Path]:
        return sorted(self.versions_dir.glob("*.json"))

    def latest_number(self) -> int:
        files = self._files()
        return int(files[-1].stem) if files else 0

    # --- writing ---------------------------------------------------------------------
    def commit(
        self, portfolio: Portfolio, message: str, *, reverted_from: int | None = None
    ) -> VersionInfo:
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        number = self.latest_number() + 1
        rec = VersionRecord(
            version=number,
            created_at=datetime.now(UTC),
            message=message,
            reverted_from=reverted_from,
            portfolio=portfolio,
        )
        _atomic_write(self._path(number), rec.model_dump_json(indent=2))
        _atomic_write(self.head_path, f"{number}\n")
        return rec.info()

    def revert(self, version: int, message: str | None = None) -> VersionInfo:
        target = self.record(version)
        msg = message or f"Revert to version {version} ({target.message})"
        return self.commit(target.portfolio, msg, reverted_from=version)


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dump_json(obj: object) -> str:
    return json.dumps(obj, indent=2, default=str)
