"""Data Engineer agent: data model -> migration scripts, seed data, compat check.

Mid tier. Only runs when the architecture defines a data model; produces
reversible migrations (up/down) and declares backward compatibility so the
ship path can gate risky migrations.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

DATA_ENGINEERING_SCHEMA = {
    "type": "object",
    "required": ["migrations", "backward_compatible"],
    "additionalProperties": False,
    "properties": {
        "migrations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "description", "up", "down"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^MIG-\\d{3}$"},
                    "description": {"type": "string", "minLength": 1},
                    "up": {"type": "string", "minLength": 1},
                    "down": {"type": "string", "minLength": 1},
                },
            },
        },
        "seed_data": {"type": "string"},
        "backward_compatible": {"type": "boolean"},
    },
}


class DataEngineerAgent(Agent):
    role = "data_engineer"
    output_schema = DATA_ENGINEERING_SCHEMA
