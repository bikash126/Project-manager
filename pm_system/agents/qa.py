"""QA agent: acceptance criteria + build -> test plan + automated acceptance tests.

Per design decision D3, QA must run on a separate model instance from the
Developer — fresh context, adversarial prompt. The orchestrator enforces the
separate-instance part at construction time; the adversarial framing lives in
the QA playbook.

The QA verdict itself is deterministic: the orchestrator executes the QA
agent's tests in the sandbox, and pass/fail comes from the test run, not from
the model's opinion.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

QA_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["ticket_id", "test_plan", "test_files"],
    "additionalProperties": False,
    "properties": {
        "ticket_id": {"type": "string", "pattern": "^TCK-\\d{3}$"},
        "test_plan": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["ac_id", "description"],
                "additionalProperties": False,
                "properties": {
                    "ac_id": {"type": "string", "pattern": "^AC-\\d{3}$"},
                    "description": {"type": "string", "minLength": 1},
                },
            },
        },
        "test_files": {
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
    },
}


class QAAgent(Agent):
    role = "qa"
    output_schema = QA_OUTPUT_SCHEMA
