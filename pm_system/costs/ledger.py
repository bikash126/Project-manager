"""Cost ledger — wraps every LLM call from day one (design doc §1, cost control).

Each entry is tagged by project / stage / agent / ticket. Per-stage and
per-project budget caps are hard stops: ``check_budget`` raises
BudgetExceededError once spend reaches the cap, and MeteredLLM calls it
before every LLM request.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass

from pm_system.errors import BudgetExceededError


@dataclass(frozen=True)
class CostEntry:
    project_id: str
    stage: str
    agent: str
    ticket_id: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    created_at: float


class CostLedger:
    def __init__(
        self,
        db_path: str = ":memory:",
        *,
        stage_budgets: dict[str, float] | None = None,
        project_budget: float | None = None,
    ):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.stage_budgets = dict(stage_budgets or {})
        self.project_budget = project_budget
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cost_entries (
                project_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                agent TEXT NOT NULL,
                ticket_id TEXT,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def record(
        self,
        *,
        project_id: str,
        stage: str,
        agent: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        ticket_id: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO cost_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    stage,
                    agent,
                    ticket_id,
                    model,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    time.time(),
                ),
            )
            self._conn.commit()

    def check_budget(self, project_id: str, stage: str) -> None:
        """Raise BudgetExceededError if the stage or project cap is spent.

        Called before each LLM request, so the request that crosses a cap
        completes and the next one is refused.
        """
        cap = self.stage_budgets.get(stage)
        if cap is not None and self.stage_spend(project_id, stage) >= cap:
            raise BudgetExceededError(
                f"stage '{stage}' spend reached its ${cap:.4f} cap for {project_id}"
            )
        if self.project_budget is not None and self.project_spend(project_id) >= self.project_budget:
            raise BudgetExceededError(
                f"project {project_id} spend reached its ${self.project_budget:.4f} cap"
            )

    def stage_spend(self, project_id: str, stage: str) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM cost_entries"
            " WHERE project_id = ? AND stage = ?",
            (project_id, stage),
        ).fetchone()
        return float(row["total"])

    def project_spend(self, project_id: str) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM cost_entries WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return float(row["total"])

    def summary(self, project_id: str) -> dict[str, float]:
        rows = self._conn.execute(
            "SELECT stage, SUM(cost_usd) AS total FROM cost_entries"
            " WHERE project_id = ? GROUP BY stage ORDER BY stage",
            (project_id,),
        ).fetchall()
        return {row["stage"]: float(row["total"]) for row in rows}

    def entries(self, project_id: str) -> list[CostEntry]:
        rows = self._conn.execute(
            "SELECT * FROM cost_entries WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        return [
            CostEntry(
                project_id=r["project_id"],
                stage=r["stage"],
                agent=r["agent"],
                ticket_id=r["ticket_id"],
                model=r["model"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                cost_usd=r["cost_usd"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
