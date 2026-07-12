"""Model tiers and orchestrator configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Model tiers (design doc §1, tech stack). Analyst/Developer/QA are all
# strong-tier in Phase 1. Overridable via environment.
STRONG_MODEL = os.environ.get("PM_STRONG_MODEL", "claude-opus-4-8")
MID_MODEL = os.environ.get("PM_MID_MODEL", "claude-sonnet-5")
CHEAP_MODEL = os.environ.get("PM_CHEAP_MODEL", "claude-haiku-4-5-20251001")


# Hard context-size cap per agent invocation (system + user prompt, in
# characters). The orchestrator curates minimal context packages; this cap is
# the backstop against agents drowning in project history (design doc §1).
DEFAULT_MAX_CONTEXT_CHARS = 120_000


@dataclass
class OrchestratorConfig:
    max_retries: int = 3  # bounded retry loops (design doc §3)
    sandbox_timeout: int = 300  # seconds per sandboxed command
    stage_budgets: dict[str, float] = field(default_factory=dict)  # stage -> USD cap
    project_budget: float | None = None
    sprint_capacity_points: int = 10  # Planner input (Phase 2)
    gate2_enabled: bool = True  # human gate on architecture (D4)
    # Phase 3 quality + security loop
    enable_security: bool = True  # run the security scan step (Dev -> Security -> Review -> QA)
    enable_review: bool = True  # run the Reviewer step
    blocking_severity: str = "high"  # confirmed findings >= this block the PR (D7)
    # Phase 4 ship path
    enable_ship: bool = True  # run the ship path after the build loop
    gate3_enabled: bool = True  # human gate on release (D4)
    deploy_environment: str = "production"
    # Phase 5 change management
    cr_gate_threshold_points: int = 5  # a change delta above this goes to a human gate
    # Phase 6 operate + learn
    enable_retrospective: bool = True  # write KB entries at project close (needs a KB)
