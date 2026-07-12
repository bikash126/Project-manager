"""Production incident types for the operate loop (design doc §3, §6).

Prod feedback IS a change-request stream: an actionable incident becomes a
fix that re-enters the dev loop via the CR machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProdIncident:
    incident_id: str  # "INC-001"
    description: str
    logs: str = ""
    service: str = ""


@dataclass
class IncidentResult:
    incident_id: str
    # "no_action" | "ticketed" | "rolled_back" | "rolled_back+ticketed" | "escalated"
    decision: str
    severity: str = ""
    triage: str = ""
    rolled_back: bool = False
    change_result: object | None = None  # ChangeResult if a fix re-entered the loop
    escalations: list[str] = field(default_factory=list)
