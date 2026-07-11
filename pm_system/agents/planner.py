"""Planner agent: WBS + capacity -> sprint plan, dependency graph, milestones."""

from __future__ import annotations

from pm_system.agents.base import Agent

_WBS_PATTERN = "^WBS-\\d{3}$"

SPRINT_PLAN_SCHEMA = {
    "type": "object",
    "required": ["sprints", "dependencies", "milestones"],
    "additionalProperties": False,
    "properties": {
        "sprints": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["number", "goal", "wbs_ids"],
                "additionalProperties": False,
                "properties": {
                    "number": {"type": "integer", "minimum": 1},
                    "goal": {"type": "string", "minLength": 1},
                    "wbs_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": _WBS_PATTERN},
                    },
                },
            },
        },
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["from", "to"],
                "additionalProperties": False,
                "properties": {
                    # "from" depends on "to": "to" must be done first
                    "from": {"type": "string", "pattern": _WBS_PATTERN},
                    "to": {"type": "string", "pattern": _WBS_PATTERN},
                },
            },
        },
        "milestones": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "sprint"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "sprint": {"type": "integer", "minimum": 1},
                },
            },
        },
    },
}


class PlannerAgent(Agent):
    role = "planner"
    output_schema = SPRINT_PLAN_SCHEMA
