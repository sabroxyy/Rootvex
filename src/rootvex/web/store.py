"""SQLite store for the web prototype: each request, its steps, the report and the decision.

One file (data/rootvex.db), standard library only. Device output stored here has
already been redacted by the device tools, so no passwords or keys are kept.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    user          TEXT NOT NULL,
    question      TEXT NOT NULL,
    mode          TEXT NOT NULL,          -- live | recorded
    status        TEXT NOT NULL,          -- running | done | failed | interrupted
    plan          TEXT,
    events        TEXT NOT NULL DEFAULT '[]',
    report        TEXT,
    error         TEXT,
    decision      TEXT,                   -- approved | rejected (recorded only)
    decided_by    TEXT,
    decided_at    TEXT,
    decision_note TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RunStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            # A restart stops any run that was in progress; say so instead of hanging.
            self._db.execute(
                "UPDATE runs SET status='interrupted', error='server restarted during the run' "
                "WHERE status='running'"
            )
            self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._db.execute(sql, args)
            self._db.commit()

    def create(self, run_id: str, user: str, question: str, mode: str) -> None:
        self._exec(
            "INSERT INTO runs (id, created_at, user, question, mode, status) "
            "VALUES (?, ?, ?, ?, ?, 'running')",
            (run_id, _now(), user, question, mode),
        )

    def add_event(self, run_id: str, event: dict) -> None:
        with self._lock:
            row = self._db.execute("SELECT events FROM runs WHERE id=?", (run_id,)).fetchone()
            events = json.loads(row["events"]) if row else []
            events.append(event)
            plan = event.get("plan") if event.get("type") == "plan" else None
            if plan is not None:
                self._db.execute(
                    "UPDATE runs SET events=?, plan=? WHERE id=?",
                    (json.dumps(events), plan, run_id),
                )
            else:
                self._db.execute(
                    "UPDATE runs SET events=? WHERE id=?", (json.dumps(events), run_id)
                )
            self._db.commit()

    def finish(self, run_id: str, report: dict) -> None:
        self._exec(
            "UPDATE runs SET status='done', report=? WHERE id=?", (json.dumps(report), run_id)
        )

    def fail(self, run_id: str, error: str) -> None:
        self._exec("UPDATE runs SET status='failed', error=? WHERE id=?", (error, run_id))

    def decide(self, run_id: str, decision: str, user: str, note: str = "") -> bool:
        """Record Approve/Reject. Only the decision is stored; nothing touches a device."""
        if decision not in ("approved", "rejected"):
            raise ValueError("decision must be approved or rejected")
        with self._lock:
            cur = self._db.execute(
                "UPDATE runs SET decision=?, decided_by=?, decided_at=?, decision_note=? "
                "WHERE id=? AND status='done' AND decision IS NULL",
                (decision, user, _now(), note[:500], run_id),
            )
            self._db.commit()
            return cur.rowcount == 1

    def get(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return _row(row) if row else None

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, created_at, user, question, mode, status, decision, decided_by "
                "FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def _row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["events"] = json.loads(d["events"] or "[]")
    d["report"] = json.loads(d["report"]) if d["report"] else None
    return d
