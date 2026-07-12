"""Knowledge Base — cross-project memory (design doc §1, §6).

Accumulates experience the Retrospective writes at project close and agents
read at task start:
- calibration: estimated vs. actual effort, so the Estimator self-corrects
- adr: reusable architecture decisions for the Architect
- failure_pattern: known ways things broke, to avoid repeating them
- human_correction: diffs/decisions a human made, captured as lessons
- lesson: free-form retrospective lessons

Retrieval is hybrid keyword/tag match (a vector index would slot in behind the
same `query` interface). Backed by SQLite, Postgres-shaped like the other
stores. The calibration metric (design §9, P6) is estimate error shrinking
across projects: `calibration_factor` is the correction the KB has learned.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass

KIND_CALIBRATION = "calibration"
KIND_ADR = "adr"
KIND_FAILURE = "failure_pattern"
KIND_HUMAN_CORRECTION = "human_correction"
KIND_LESSON = "lesson"


@dataclass(frozen=True)
class KBEntry:
    entry_id: str
    kind: str
    project_id: str
    content: dict
    tags: tuple[str, ...]
    created_at: float


class KnowledgeBase:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_entries (
                entry_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                project_id TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def add(self, kind: str, *, project_id: str, content: dict,
            tags: list[str] | tuple[str, ...] = ()) -> KBEntry:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) AS c FROM kb_entries").fetchone()["c"]
            entry_id = f"KB-{n + 1:04d}"
            now = time.time()
            self._conn.execute(
                "INSERT INTO kb_entries VALUES (?, ?, ?, ?, ?, ?)",
                (entry_id, kind, project_id, json.dumps(content), json.dumps(list(tags)), now),
            )
            self._conn.commit()
            return KBEntry(entry_id, kind, project_id, content, tuple(tags), now)

    def query(self, *, kind: str | None = None, tags: list[str] | None = None,
              keyword: str | None = None, limit: int = 20) -> list[KBEntry]:
        clauses, params = [], []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if keyword is not None:
            clauses.append("content LIKE ?")
            params.append(f"%{keyword}%")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM kb_entries{where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        entries = [self._to_entry(r) for r in rows]
        if tags:
            wanted = set(tags)
            entries = [e for e in entries if wanted & set(e.tags)]
        return entries

    def calibration_factor(self, tags: list[str] | None = None) -> float:
        """Mean actual/estimated across calibration entries (1.0 if none).

        > 1.0 means past work ran over its estimate; the Estimator multiplies
        new estimates by this to shrink error over time.
        """
        ratios = []
        for entry in self.query(kind=KIND_CALIBRATION, tags=tags, limit=1000):
            est = entry.content.get("estimated_points")
            act = entry.content.get("actual_points")
            if est:
                ratios.append(act / est)
        return sum(ratios) / len(ratios) if ratios else 1.0

    def calibration_summary(self, tags: list[str] | None = None) -> dict:
        entries = self.query(kind=KIND_CALIBRATION, tags=tags, limit=1000)
        return {
            "multiplier": round(self.calibration_factor(tags), 3),
            "samples": len(entries),
            "note": (
                "past work ran ~{:.0%} of estimate; scale new estimates by the "
                "multiplier".format(self.calibration_factor(tags))
                if entries
                else "no calibration data yet"
            ),
        }

    def reusable_adrs(self, limit: int = 10) -> list[dict]:
        return [e.content for e in self.query(kind=KIND_ADR, limit=limit)]

    @staticmethod
    def apply_calibration(points: int, factor: float) -> int:
        return max(1, round(points * factor))

    @staticmethod
    def _to_entry(row) -> KBEntry:
        return KBEntry(
            entry_id=row["entry_id"],
            kind=row["kind"],
            project_id=row["project_id"],
            content=json.loads(row["content"]),
            tags=tuple(json.loads(row["tags"])),
            created_at=row["created_at"],
        )
