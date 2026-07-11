"""Architect agent: PRD + constraints (+ KB ADRs later) -> design doc, ADRs,
API contracts, data model.

Strong tier, and gated: the design doc goes through Gate 2 because the
Architect misses non-obvious trade-offs (design doc capability assessment).
"""

from __future__ import annotations

from pm_system.agents.base import Agent

ARCHITECTURE_SCHEMA = {
    "type": "object",
    "required": ["overview", "components", "adrs"],
    "additionalProperties": False,
    "properties": {
        "overview": {"type": "string", "minLength": 1},
        "components": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "responsibility", "story_ids"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "responsibility": {"type": "string", "minLength": 1},
                    "story_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": "^US-\\d{3}$"},
                    },
                },
            },
        },
        "adrs": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "title", "context", "decision", "alternatives", "consequences"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^ADR-\\d{3}$"},
                    "title": {"type": "string", "minLength": 1},
                    "context": {"type": "string", "minLength": 1},
                    "decision": {"type": "string", "minLength": 1},
                    "alternatives": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "consequences": {"type": "string", "minLength": 1},
                },
            },
        },
        "api_contracts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "description"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                    "request": {"type": "string"},
                    "response": {"type": "string"},
                },
            },
        },
        "data_model": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["entity", "fields"],
                "additionalProperties": False,
                "properties": {
                    "entity": {"type": "string", "minLength": 1},
                    "fields": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["name", "type"],
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string", "minLength": 1},
                                "type": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
        },
    },
}


class ArchitectAgent(Agent):
    role = "architect"
    output_schema = ARCHITECTURE_SCHEMA
