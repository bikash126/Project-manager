"""Developer agent: ticket -> code + unit tests as a set of repo-relative files."""

from __future__ import annotations

from pm_system.agents.base import Agent

DEV_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["ticket_id", "files"],
    "additionalProperties": False,
    "properties": {
        "ticket_id": {"type": "string", "pattern": "^TCK-\\d{3}$"},
        "files": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["path", "content"],
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
}


class DeveloperAgent(Agent):
    role = "developer"
    output_schema = DEV_OUTPUT_SCHEMA
