"""UI agent: UX specs -> component specs + design tokens (Figma via MCP).

Mid tier (Phase 7). Offline it produces the component specs and design tokens;
the Figma MCP integration would push these into a design file.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

UI_SCHEMA = {
    "type": "object",
    "required": ["components", "design_tokens"],
    "additionalProperties": False,
    "properties": {
        "components": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "description", "used_in"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                    "used_in": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": "^FLOW-\\d{3}$"},
                    },
                },
            },
        },
        "design_tokens": {
            "type": "object",
            "required": ["colors"],
            "additionalProperties": True,
            "properties": {
                "colors": {"type": "object", "minProperties": 1},
                "spacing": {"type": "object"},
                "typography": {"type": "object"},
            },
        },
    },
}


class UIAgent(Agent):
    role = "ui"
    output_schema = UI_SCHEMA
