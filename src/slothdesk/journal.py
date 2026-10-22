"""SQLite journal: every move is recorded so it can be undone. Also caches model decisions."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .classify import Decision
from .models import utc_now

_V1 = """
BEGIN;
CREATE TABLE runs(
  id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
  mode TEXT NOT NULL, model TEXT, status TEXT NOT NULL, summary_json TEXT);
CREATE TABLE moves(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id),
  src TEXT NOT NULL, dest TEXT, sha256 TEXT, category TEXT, title TEXT,
  confidence REAL, source TEXT, reason TEXT, model TEXT, prompt_version TEXT,
  status TEXT NOT NULL CHECK(status IN ('pending','moved','undone','skipped','failed')),
  detail TEXT, created_at TEXT NOT NULL);
CREATE INDEX idx_moves_run ON moves(run_id);
CREATE TABLE decisions(
  sha256 TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  category TEXT NOT NULL, title TEXT, confidence REAL NOT NULL, reason TEXT,
  created_at TEXT NOT NULL, PRIMARY KEY(sha256, model, prompt_version));
PRAGMA user_version = 1;
COMMIT;
"""
MIGRATIONS = [_V1]  # append new scripts; each must end by bumping user_version
_MOVE_FIELDS = frozenset({"dest", "sha256", "category", "title", "confidence", "source",
                          "reason", "model", "prompt_version", "detail"})
_UPDATE_SQL = {
    "status": "UPDATE moves SET status=? WHERE id=?",
    "dest": "UPDATE moves SET dest=? WHERE id=?",
    "detail": "UPDATE moves SET detail=? WHERE id=?",
    "sha256": "UPDATE moves SET sha256=? WHERE id=?",
}


class JournalError(RuntimeError):
    pass


class Journal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(path.parent, 0o700)  # noqa: S103 - owner-only state folder
        self.path = path
        self._db = sqlite3.connect(path, isolation_level=None, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def _migrate(self) -> None:
        current = self._db.execute("PRAGMA user_version").fetchone()[0]
        if current > len(MIGRATIONS):
            raise JournalError("journal was created by a newer version of Sloth Desk")
        for script in MIGRATIONS[current:]:
            self._db.executescript(script)

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")

    # runs -----------------------------------------------------------------
    def start_run(self, run_id: str, mode: str, model: str) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO runs(id, started_at, mode, model, status) VALUES (?,?,?,?, 'running')",
                (run_id, utc_now(), mode, model),
            )

    def finish_run(self, run_id: str, status: str, summary: dict[str, Any]) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE runs SET finished_at=?, status=?, summary_json=? WHERE id=?",
                (utc_now(), status, json.dumps(summary), run_id),
            )

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self._db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

    def last_apply_run_id(self) -> str | None:
        row = self._db.execute(
            "SELECT r.id FROM runs r WHERE r.mode='apply' AND EXISTS "
            "(SELECT 1 FROM moves m WHERE m.run_id=r.id AND m.status='moved') "
            "ORDER BY r.started_at DESC, r.rowid DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None

    # moves ----------------------------------------------------------------
    def add_move(self, run_id: str, src: str, status: str, **f: Any) -> int:
        unknown = set(f) - _MOVE_FIELDS
        if unknown:
            raise JournalError(f"unknown move fields: {sorted(unknown)}")
        params = {k: f.get(k) for k in _MOVE_FIELDS}
        with self.tx() as db:
            cur = db.execute(
                "INSERT INTO moves(run_id, src, status, created_at, dest, sha256, category, "
                "title, confidence, source, reason, model, prompt_version, detail) "
                "VALUES (:run_id, :src, :status, :created_at, :dest, :sha256, :category, "
                ":title, :confidence, :source, :reason, :model, :prompt_version, :detail)",
                {"run_id": run_id, "src": src, "status": status, "created_at": utc_now(), **params},
            )
            return int(cur.lastrowid or 0)

    def update_move(self, move_id: int, **f: Any) -> None:
        bad = set(f) - set(_UPDATE_SQL)
        if bad:
            raise JournalError(f"cannot update columns: {sorted(bad)}")
        with self.tx() as db:  # one statement per allow-listed column; no dynamic SQL
            for column, value in f.items():
                db.execute(_UPDATE_SQL[column], (value, move_id))

    def moves_for_run(self, run_id: str) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM moves WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()

    def pending_moves(self) -> list[sqlite3.Row]:
        return self._db.execute("SELECT * FROM moves WHERE status='pending' ORDER BY id").fetchall()

    # decision cache -------------------------------------------------------
    def get_decision(self, sha: str, model: str, prompt_version: str) -> Decision | None:
        r = self._db.execute(
            "SELECT * FROM decisions WHERE sha256=? AND model=? AND prompt_version=?",
            (sha, model, prompt_version),
        ).fetchone()
        if r is None:
            return None
        return Decision(r["category"], r["title"], r["confidence"], r["reason"] or "", "cache")

    def put_decision(self, sha: str, model: str, prompt_version: str, d: Decision) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?)",
                (sha, model, prompt_version, d.category, d.title, d.confidence, d.reason,
                 utc_now()),
            )

    # statistics -----------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        """Totals across all applied runs, for the 'lazy payoff' summary."""
        db = self._db
        by_status = {
            r["status"]: r["n"]
            for r in db.execute(
                "SELECT m.status, COUNT(*) AS n FROM moves m JOIN runs r ON r.id = m.run_id "
                "WHERE r.mode='apply' GROUP BY m.status"
            )
        }
        by_folder = {
            r["category"]: r["n"]
            for r in db.execute(
                "SELECT category, COUNT(*) AS n FROM moves WHERE status='moved' "
                "GROUP BY category ORDER BY n DESC, category"
            )
        }
        left = {
            r["reason"]: r["n"]
            for r in db.execute(
                "SELECT reason, COUNT(*) AS n FROM moves WHERE status='skipped' "
                "GROUP BY reason ORDER BY n DESC"
            )
        }
        llm_seconds, llm_calls, runs = 0.0, 0, 0
        # Model time counts previews too: that is when the model actually does the work.
        for r in db.execute("SELECT mode, summary_json FROM runs"):
            runs += r["mode"] == "apply"
            try:
                data = json.loads(r["summary_json"] or "{}")
            except ValueError:
                continue
            llm_seconds += float(data.get("llm_seconds", 0.0))
            llm_calls += int(data.get("llm_calls", 0))
        return {
            "runs": runs, "moved": by_status.get("moved", 0), "undone": by_status.get("undone", 0),
            "by_folder": by_folder, "left_for_you": left,
            "llm_seconds": llm_seconds, "llm_calls": llm_calls,
        }

    # housekeeping ---------------------------------------------------------
    def purge_older_than(self, days: int) -> int:
        """Delete old history and cached decisions. Never touches your files."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.tx() as db:
            n = db.execute(
                "DELETE FROM moves WHERE created_at < ? AND status != 'pending'", (cutoff,)
            ).rowcount
            n += db.execute("DELETE FROM decisions WHERE created_at < ?", (cutoff,)).rowcount
            db.execute(
                "DELETE FROM runs WHERE started_at < ? AND status != 'running' "
                "AND NOT EXISTS (SELECT 1 FROM moves WHERE moves.run_id = runs.id)",
                (cutoff,),
            )
        return n

    def backup_to(self, dest: Path) -> None:
        if dest.exists():
            raise JournalError("backup target already exists")
        target = sqlite3.connect(dest)
        try:
            self._db.backup(target)
        finally:
            target.close()
