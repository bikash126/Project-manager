"""Release Manager agent: passing build -> version, changelog, deploy + rollback.

Cheap tier. The only agent that can trigger a deploy (credential scoping,
design doc §4). The rollback plan it writes is consumed by Ops in Phase 6.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

RELEASE_SCHEMA = {
    "type": "object",
    "required": ["version", "changelog", "deploy_plan", "rollback_plan"],
    "additionalProperties": False,
    "properties": {
        "version": {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$"},
        "changelog": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["type", "description"],
                "additionalProperties": False,
                "properties": {
                    "type": {"enum": ["added", "changed", "fixed", "removed", "security"]},
                    "description": {"type": "string", "minLength": 1},
                },
            },
        },
        "deploy_plan": {"type": "string", "minLength": 1},
        "rollback_plan": {"type": "string", "minLength": 1},
    },
}


class ReleaseManagerAgent(Agent):
    role = "release_manager"
    output_schema = RELEASE_SCHEMA
