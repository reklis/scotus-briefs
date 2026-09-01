"""Crash-safe SQLite and filesystem spool for edge calls."""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ragchew.contracts import CaptureEnvelope


@dataclass(frozen=True)
class SpoolEntry:
    capture_id: str
    state: str
    envelope: CaptureEnvelope
    audio_path: Path
    attempts: int
    available_at: datetime
    last_error: str | None


class SpoolFullError(Exception):
    pass


class EdgeSpool:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.audio_root = root / "audio"
        self.audio_root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.database = sqlite3.connect(root / "spool.sqlite3")
        self.database.row_factory = sqlite3.Row
        self.database.execute("PRAGMA journal_mode=WAL")
        self.database.execute("PRAGMA synchronous=FULL")
        self.database.execute(
            """CREATE TABLE IF NOT EXISTS spool (
              capture_id TEXT PRIMARY KEY,
              state TEXT NOT NULL,
              envelope_json TEXT NOT NULL,
              audio_path TEXT NOT NULL,
              audio_bytes INTEGER NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              available_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              acknowledged_at TEXT,
              last_error TEXT
            )"""
        )
        self.database.commit()

    def close(self) -> None:
        self.database.close()

    def used_bytes(self) -> int:
        row = self.database.execute(
            "SELECT COALESCE(sum(audio_bytes),0) AS used FROM spool WHERE state <> 'acknowledged'"
        ).fetchone()
        return int(row["used"])

    def add(self, envelope: CaptureEnvelope, source_audio: Path) -> bool:
        existing = self.database.execute(
            "SELECT capture_id FROM spool WHERE capture_id=?", (envelope.capture_id,)
        ).fetchone()
        if existing:
            return False
        if self.used_bytes() + envelope.audio.byte_count > self.max_bytes:
            raise SpoolFullError("unacknowledged edge spool capacity would be exceeded")

        destination = self.audio_root / f"{envelope.capture_id}{source_audio.suffix.lower()}"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with source_audio.open("rb") as source, temporary.open("wb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
        now = datetime.now(UTC).isoformat()
        try:
            with self.database:
                self.database.execute(
                    """INSERT INTO spool(capture_id,state,envelope_json,audio_path,audio_bytes,
                       available_at,created_at) VALUES (?,?,?,?,?,?,?)""",
                    (
                        envelope.capture_id,
                        "finalized",
                        envelope.model_dump_json(),
                        str(destination),
                        envelope.audio.byte_count,
                        now,
                        now,
                    ),
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return True

    @staticmethod
    def _entry(row: sqlite3.Row) -> SpoolEntry:
        return SpoolEntry(
            capture_id=row["capture_id"],
            state=row["state"],
            envelope=CaptureEnvelope.model_validate_json(row["envelope_json"]),
            audio_path=Path(row["audio_path"]),
            attempts=row["attempts"],
            available_at=datetime.fromisoformat(row["available_at"]),
            last_error=row["last_error"],
        )

    def claim(self, now: datetime | None = None) -> SpoolEntry | None:
        current = (now or datetime.now(UTC)).isoformat()
        with self.database:
            row = self.database.execute(
                """SELECT * FROM spool WHERE state IN ('finalized','retryable')
                   AND available_at <= ? ORDER BY created_at LIMIT 1""",
                (current,),
            ).fetchone()
            if row is None:
                return None
            updated = self.database.execute(
                """UPDATE spool SET state='uploading',attempts=attempts+1
                   WHERE capture_id=? AND state IN ('finalized','retryable')""",
                (row["capture_id"],),
            )
            if updated.rowcount != 1:
                return None
            claimed = self.database.execute(
                "SELECT * FROM spool WHERE capture_id=?", (row["capture_id"],)
            ).fetchone()
        return self._entry(claimed)

    def retry(self, capture_id: str, error: str, delay_seconds: float) -> None:
        available = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        with self.database:
            self.database.execute(
                """UPDATE spool SET state='retryable',available_at=?,last_error=?
                   WHERE capture_id=? AND state='uploading'""",
                (available.isoformat(), error[:2_000], capture_id),
            )

    def acknowledge(self, capture_id: str) -> None:
        with self.database:
            self.database.execute(
                """UPDATE spool SET state='acknowledged',acknowledged_at=?,last_error=NULL
                   WHERE capture_id=?""",
                (datetime.now(UTC).isoformat(), capture_id),
            )

    def conflict(self, capture_id: str, error: str) -> None:
        with self.database:
            self.database.execute(
                "UPDATE spool SET state='conflicted',last_error=? WHERE capture_id=?",
                (error[:2_000], capture_id),
            )

    def recover_uploads(self) -> int:
        with self.database:
            result = self.database.execute(
                """UPDATE spool SET state='retryable',available_at=?
                   WHERE state='uploading'""",
                (datetime.now(UTC).isoformat(),),
            )
        return result.rowcount

    def cleanup_acknowledged(self, grace_seconds: int, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)) - timedelta(seconds=grace_seconds)
        rows = self.database.execute(
            """SELECT capture_id,audio_path FROM spool
               WHERE state='acknowledged' AND acknowledged_at <= ?""",
            (cutoff.isoformat(),),
        ).fetchall()
        with self.database:
            for row in rows:
                Path(row["audio_path"]).unlink(missing_ok=True)
                self.database.execute("DELETE FROM spool WHERE capture_id=?", (row["capture_id"],))
        return len(rows)

    def stats(self, now: datetime | None = None) -> tuple[int, float]:
        current = now or datetime.now(UTC)
        row = self.database.execute(
            """SELECT count(*) AS depth,min(created_at) AS oldest FROM spool
               WHERE state <> 'acknowledged'"""
        ).fetchone()
        age = 0.0
        if row["oldest"]:
            age = max(0.0, (current - datetime.fromisoformat(row["oldest"])).total_seconds())
        return int(row["depth"]), age

    def get(self, capture_id: str) -> SpoolEntry | None:
        row = self.database.execute(
            "SELECT * FROM spool WHERE capture_id=?", (capture_id,)
        ).fetchone()
        return self._entry(row) if row else None
