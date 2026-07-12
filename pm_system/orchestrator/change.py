"""Change-request types (design doc §3, Phase 5).

A CR can be raised by a human or by the Ops agent (Phase 6). The orchestrator
triages it (Product Owner), traces the blast radius via traceability IDs,
re-estimates the delta, gates if it is large, flips only the affected
artifacts to `stale`, and re-runs only those — not the whole project.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChangeRequest:
    cr_id: str  # "CR-001"
    description: str
    target_story_id: str  # the user story this change touches (US-xxx)
    # If given, replaces that story's acceptance criteria (an AC change).
    new_acceptance_criteria: list[dict] | None = None
    raised_by: str = "human"  # "human" | "ops"


@dataclass
class ChangeResult:
    cr_id: str
    decision: str  # "accepted" | "deferred" | "rejected" | "escalated"
    reason: str = ""
    blast_radius: list[str] = field(default_factory=list)  # artifact ids re-run/invalidated
    total_artifacts: int = 0
    rerun_tickets: list = field(default_factory=list)  # list[TicketResult]
    new_version: str | None = None
    delta_points: int = 0
    escalations: list[str] = field(default_factory=list)

    @property
    def blast_radius_ratio(self) -> float | None:
        if not self.total_artifacts:
            return None
        return len(self.blast_radius) / self.total_artifacts


# Product Owner triage output for a CR (accept / defer / reject).
CR_TRIAGE_SCHEMA = {
    "type": "object",
    "required": ["decision", "reason"],
    "additionalProperties": False,
    "properties": {
        "decision": {"enum": ["accept", "defer", "reject"]},
        "reason": {"type": "string", "minLength": 1},
    },
}
