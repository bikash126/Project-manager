"""Product Owner agent: PRD + constraints -> prioritized backlog, MVP scope, cut list.

Strong tier, and per the design's capability assessment kept human-heavy
early — its output goes through Gate 1 before anything is built.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

_US_PATTERN = "^US-\\d{3}$"

BACKLOG_SCHEMA = {
    "type": "object",
    "required": ["mvp_story_ids", "backlog", "cut_list"],
    "additionalProperties": False,
    "properties": {
        "mvp_story_ids": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": _US_PATTERN},
        },
        "backlog": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["story_id", "priority", "rationale"],
                "additionalProperties": False,
                "properties": {
                    "story_id": {"type": "string", "pattern": _US_PATTERN},
                    "priority": {"type": "integer", "minimum": 1},
                    "rationale": {"type": "string", "minLength": 1},
                },
            },
        },
        "cut_list": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["story_id", "reason"],
                "additionalProperties": False,
                "properties": {
                    "story_id": {"type": "string", "pattern": _US_PATTERN},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


class ProductOwnerAgent(Agent):
    role = "product_owner"
    output_schema = BACKLOG_SCHEMA
