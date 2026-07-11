"""Intake/Analyst agent: raw idea -> PRD with user stories + acceptance criteria."""

from __future__ import annotations

from pm_system.agents.base import Agent

PRD_SCHEMA = {
    "type": "object",
    "required": ["title", "summary", "user_stories"],
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "user_stories": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "story", "acceptance_criteria"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^US-\\d{3}$"},
                    "story": {"type": "string", "minLength": 1},
                    "acceptance_criteria": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["id", "given", "when", "then"],
                            "additionalProperties": False,
                            "properties": {
                                "id": {"type": "string", "pattern": "^AC-\\d{3}$"},
                                "given": {"type": "string", "minLength": 1},
                                "when": {"type": "string", "minLength": 1},
                                "then": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
        },
    },
}


class AnalystAgent(Agent):
    role = "analyst"
    output_schema = PRD_SCHEMA
