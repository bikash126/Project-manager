"""DevOps agent: architecture -> CI/CD pipeline, IaC, environment definitions.

Mid tier. The only agent that would hold cloud credentials in a real
deployment (credential scoping, design doc §4) — here it produces the config
files that get written into the project workspace.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

_FILES = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["path", "content"],
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string"},
        },
    },
}

DEVOPS_SCHEMA = {
    "type": "object",
    "required": ["pipeline_files", "environments"],
    "additionalProperties": False,
    "properties": {
        "pipeline_files": {**_FILES, "minItems": 1},
        "iac_files": _FILES,
        "environments": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
}


class DevOpsAgent(Agent):
    role = "devops"
    output_schema = DEVOPS_SCHEMA
