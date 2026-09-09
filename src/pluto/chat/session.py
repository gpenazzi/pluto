"""Chat transcripts: one JSON-lines file per session under ~/.pluto/chats."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pluto.chat.providers.base import ChatEvent
from pluto.store import paths


class Transcript:
    def __init__(self, portfolio: str, path: Path | None = None):
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.path = path or paths.home() / "chats" / f"{stamp}-{portfolio}.jsonl"

    def _write(self, record: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **record}
        with self.path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def user(self, text: str) -> None:
        self._write({"role": "user", "text": text})

    def event(self, ev: ChatEvent) -> None:
        rec: dict[str, object] = {"role": "assistant", "event": ev.type}
        if ev.text:
            rec["text"] = ev.text[:4000]
        if ev.name:
            rec["tool"] = ev.name
        if ev.args:
            rec["args"] = ev.args
        if ev.meta:
            rec["meta"] = ev.meta
        self._write(rec)
