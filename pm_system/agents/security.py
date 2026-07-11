"""Security agent: triages deterministic scanner findings (design doc §5, D7).

The scanners (SAST/secrets/deps) do detection; this agent interprets. It
classifies each finding as `confirmed` or `false_positive` (a false positive
requires a written reason — the false-positive protocol) and writes threat-
model notes.

Per design decision D7, verdicts are block/pass, never advisory — and the
agent cannot make a real finding advisory. The orchestrator, not the agent,
computes the verdict: a finding triaged `confirmed` at or above the blocking
severity blocks the PR. The agent can only exclude a finding by explicitly
marking it `false_positive` with a reason, which is auditable. The scanner's
severity stands for confirmed findings; the agent cannot downgrade it.

Strong tier, and a separate model instance from the Developer.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

SECURITY_TRIAGE_SCHEMA = {
    "type": "object",
    "required": ["triage", "threat_model_notes"],
    "additionalProperties": False,
    "properties": {
        "triage": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["finding_id", "status", "reason"],
                "additionalProperties": False,
                "properties": {
                    "finding_id": {"type": "string", "pattern": "^FND-\\d{3}$"},
                    "status": {"enum": ["confirmed", "false_positive"]},
                    "reason": {"type": "string"},
                },
            },
        },
        "threat_model_notes": {"type": "string"},
    },
}


class SecurityAgent(Agent):
    role = "security"
    output_schema = SECURITY_TRIAGE_SCHEMA
