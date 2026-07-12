"""Retrospective agent: project close -> structured lessons for the KB.

Cheap tier. The deterministic parts of the retrospective (estimate-vs-actual
calibration, failure patterns) are computed by the orchestrator; this agent
adds the qualitative lessons that a human would jot down at a retro.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

RETRO_SCHEMA = {
    "type": "object",
    "required": ["lessons"],
    "additionalProperties": False,
    "properties": {
        "lessons": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["category", "lesson"],
                "additionalProperties": False,
                "properties": {
                    "category": {
                        "enum": ["estimation", "process", "technical", "quality", "security"]
                    },
                    "lesson": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}


class RetrospectiveAgent(Agent):
    role = "retrospective"
    output_schema = RETRO_SCHEMA
