"""UX agent: user stories -> user flows, wireframe specs, information architecture.

Mid tier (Phase 7). Produces the interaction design the UI agent and the
Developer build against. Weak on novel interaction design (design doc capability
assessment), so it is best on conventional flows.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

UX_SCHEMA = {
    "type": "object",
    "required": ["flows", "information_architecture"],
    "additionalProperties": False,
    "properties": {
        "flows": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "name", "story_ids", "steps"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^FLOW-\\d{3}$"},
                    "name": {"type": "string", "minLength": 1},
                    "story_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": "^US-\\d{3}$"},
                    },
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        "information_architecture": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
}


class UXAgent(Agent):
    role = "ux"
    output_schema = UX_SCHEMA
