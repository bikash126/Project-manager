"""Doc Writer agent: code + PRD -> README, API docs, user guides.

Cheap tier. Writes docs into the project workspace; must include a README.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

DOCS_SCHEMA = {
    "type": "object",
    "required": ["docs"],
    "additionalProperties": False,
    "properties": {
        "docs": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["path", "content"],
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}


class DocWriterAgent(Agent):
    role = "doc_writer"
    output_schema = DOCS_SCHEMA
