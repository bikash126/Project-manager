"""Versioned artifact store — the single source of truth (design doc §1, D2, D8).

Every artifact carries:
- a monotonically increasing version (each ``put`` supersedes the previous one)
- a status lifecycle: draft -> approved -> stale -> superseded
- traceability IDs linking it to its upstream artifacts (US-xxx, TCK-xxx, ...)

Optimistic locking: updating an existing artifact requires ``expected_version``;
a mismatch raises ArtifactConflictError, which the orchestrator handles.
Agents must consume artifacts through ``get_for_consumption``, which refuses
stale/superseded inputs.

Backed by SQLite for Phase 1. The schema and interface are Postgres-shaped so
the backend can be swapped without touching callers.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum

from pm_system.errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    InvalidTransitionError,
    StaleArtifactError,
)


class ArtifactStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    STALE = "stale"
    SUPERSEDED = "superseded"


ALLOWED_TRANSITIONS: dict[ArtifactStatus, set[ArtifactStatus]] = {
    ArtifactStatus.DRAFT: {ArtifactStatus.APPROVED, ArtifactStatus.SUPERSEDED},
    ArtifactStatus.APPROVED: {ArtifactStatus.STALE, ArtifactStatus.SUPERSEDED},
    ArtifactStatus.STALE: {ArtifactStatus.SUPERSEDED},
    ArtifactStatus.SUPERSEDED: set(),
}


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    project_id: str
    artifact_type: str
    version: int
    status: ArtifactStatus
    content: dict
    trace_ids: tuple[str, ...]
    created_by: str
    created_at: float


class ArtifactStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                project_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                status TEXT NOT NULL,
                content TEXT NOT NULL,
                trace_ids TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (artifact_id, version)
            )
            """
        )
        self._conn.commit()

    def put(
        self,
        artifact_id: str,
        *,
        artifact_type: str,
        content: dict,
        created_by: str,
        project_id: str,
        trace_ids: tuple[str, ...] | list[str] = (),
        expected_version: int | None = None,
    ) -> Artifact:
        """Write a new version of an artifact (status starts at ``draft``).

        For updates, ``expected_version`` must equal the current latest
        version — this is the optimistic lock.
        """
        with self._lock:
            row = self._latest_row(artifact_id)
            if row is None:
                if expected_version not in (None, 0):
                    raise ArtifactConflictError(
                        f"{artifact_id}: expected version {expected_version}, "
                        "but artifact does not exist"
                    )
                version = 1
            else:
                if expected_version is None:
                    raise ArtifactConflictError(
                        f"{artifact_id} already exists at v{row['version']}; "
                        "updates must pass expected_version"
                    )
                if expected_version != row["version"]:
                    raise ArtifactConflictError(
                        f"{artifact_id}: expected v{expected_version}, "
                        f"latest is v{row['version']}"
                    )
                self._conn.execute(
                    "UPDATE artifacts SET status = ? WHERE artifact_id = ? AND version = ?",
                    (ArtifactStatus.SUPERSEDED.value, artifact_id, row["version"]),
                )
                version = row["version"] + 1

            now = time.time()
            self._conn.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    version,
                    project_id,
                    artifact_type,
                    ArtifactStatus.DRAFT.value,
                    json.dumps(content),
                    json.dumps(list(trace_ids)),
                    created_by,
                    now,
                ),
            )
            self._conn.commit()
            return self.get(artifact_id, version=version)

    def get(self, artifact_id: str, version: int | None = None) -> Artifact:
        if version is None:
            row = self._latest_row(artifact_id)
        else:
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ? AND version = ?",
                (artifact_id, version),
            ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(artifact_id)
        return self._to_artifact(row)

    def get_for_consumption(self, artifact_id: str) -> Artifact:
        """Fetch an artifact as an agent input. Refuses stale/superseded."""
        artifact = self.get(artifact_id)
        if artifact.status in (ArtifactStatus.STALE, ArtifactStatus.SUPERSEDED):
            raise StaleArtifactError(
                f"{artifact_id} v{artifact.version} is {artifact.status.value}; "
                "agents must not consume it"
            )
        return artifact

    def set_status(self, artifact_id: str, status: ArtifactStatus) -> Artifact:
        with self._lock:
            row = self._latest_row(artifact_id)
            if row is None:
                raise ArtifactNotFoundError(artifact_id)
            current = ArtifactStatus(row["status"])
            if status not in ALLOWED_TRANSITIONS[current]:
                raise InvalidTransitionError(
                    f"{artifact_id}: {current.value} -> {status.value} not allowed"
                )
            self._conn.execute(
                "UPDATE artifacts SET status = ? WHERE artifact_id = ? AND version = ?",
                (status.value, artifact_id, row["version"]),
            )
            self._conn.commit()
        return self.get(artifact_id)

    def mark_stale(self, artifact_id: str) -> Artifact:
        return self.set_status(artifact_id, ArtifactStatus.STALE)

    def history(self, artifact_id: str) -> list[Artifact]:
        rows = self._conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ? ORDER BY version",
            (artifact_id,),
        ).fetchall()
        if not rows:
            raise ArtifactNotFoundError(artifact_id)
        return [self._to_artifact(r) for r in rows]

    def list_project(self, project_id: str, artifact_type: str | None = None) -> list[Artifact]:
        """Latest version of every artifact in a project."""
        query = (
            "SELECT a.* FROM artifacts a JOIN ("
            "  SELECT artifact_id, MAX(version) AS v FROM artifacts"
            "  WHERE project_id = ? GROUP BY artifact_id"
            ") latest ON a.artifact_id = latest.artifact_id AND a.version = latest.v"
        )
        params: list = [project_id]
        if artifact_type is not None:
            query += " WHERE a.artifact_type = ?"
            params.append(artifact_type)
        rows = self._conn.execute(query, params).fetchall()
        return sorted((self._to_artifact(r) for r in rows), key=lambda a: a.artifact_id)

    def find_by_trace(self, project_id: str, trace_id: str) -> list[Artifact]:
        """Traceability index: what depends on US-014? (design doc §4)."""
        return [
            artifact
            for artifact in self.list_project(project_id)
            if trace_id in artifact.trace_ids
        ]

    def _latest_row(self, artifact_id: str):
        return self._conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ? ORDER BY version DESC LIMIT 1",
            (artifact_id,),
        ).fetchone()

    @staticmethod
    def _to_artifact(row) -> Artifact:
        return Artifact(
            artifact_id=row["artifact_id"],
            project_id=row["project_id"],
            artifact_type=row["artifact_type"],
            version=row["version"],
            status=ArtifactStatus(row["status"]),
            content=json.loads(row["content"]),
            trace_ids=tuple(json.loads(row["trace_ids"])),
            created_by=row["created_by"],
            created_at=row["created_at"],
        )
