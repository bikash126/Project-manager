"""Reviewer agent: PR diff + standards doc -> review comments, approve/block.

Per design decision D3, the Reviewer runs on a separate model instance from
the Developer — fresh context, adversarial framing — so it does not
rubber-stamp its own code. The verdict is approve/block; a block must be
justified by at least one major/blocker comment, and any blocker-severity
comment forces a block (enforced in validation).

Strong tier, separate instance.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["verdict", "comments"],
    "additionalProperties": False,
    "properties": {
        "verdict": {"enum": ["approve", "block"]},
        "comments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "severity", "comment"],
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "severity": {"enum": ["info", "minor", "major", "blocker"]},
                    "comment": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


class ReviewerAgent(Agent):
    role = "reviewer"
    output_schema = REVIEW_SCHEMA
