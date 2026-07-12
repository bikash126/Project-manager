"""Ops/SRE agent: prod logs/alerts -> incident triage (design doc §2, §6).

Mid tier. Triages a production error into: a severity, whether it is
actionable, an optional fix ticket (which re-enters the dev loop), and whether
to recommend a rollback. Ops is read-only on prod; a rollback requires human
confirmation (enforced by the orchestrator, not the prompt).
"""

from __future__ import annotations

from pm_system.agents.base import Agent

OPS_TRIAGE_SCHEMA = {
    "type": "object",
    "required": ["actionable", "severity", "triage", "recommend_rollback"],
    "additionalProperties": False,
    "properties": {
        "actionable": {"type": "boolean"},
        "severity": {"enum": ["low", "medium", "high", "critical"]},
        "triage": {"type": "string", "minLength": 1},
        "recommend_rollback": {"type": "boolean"},
        # Present when actionable: where the fix goes and what it is.
        "target_story_id": {"type": "string", "pattern": "^US-\\d{3}$"},
        "fix_summary": {"type": "string"},
    },
}


class OpsAgent(Agent):
    role = "ops"
    output_schema = OPS_TRIAGE_SCHEMA
