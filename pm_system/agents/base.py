"""Agent base class.

Agents are stateless workers (design doc §1): each invocation receives a
scoped ContextPackage assembled by the orchestrator — the ticket, the traced
artifacts, the role playbook, and any revision feedback. Nothing else.

Every agent output is JSON validated against the role's output schema before
the orchestrator will accept it; failures raise AgentOutputError, which the
orchestrator turns into a revision task (counting toward the retry cap).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from pm_system.config import DEFAULT_MAX_CONTEXT_CHARS
from pm_system.errors import AgentOutputError, ContextOverflowError
from pm_system.llm.client import CostTags, MeteredLLM, extract_json

PLAYBOOK_DIR = Path(__file__).resolve().parents[2] / "playbooks"


@dataclass
class ContextPackage:
    """The minimal per-task context the orchestrator curates for an agent."""

    instructions: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    kb_entries: list[str] = field(default_factory=list)  # placeholder until Phase 6
    feedback: str | None = None


class Agent:
    role: str = "agent"
    output_schema: dict = {}

    def __init__(
        self,
        llm: MeteredLLM,
        *,
        model: str,
        playbook_path: Path | None = None,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ):
        self.llm = llm
        self.model = model
        self.max_context_chars = max_context_chars
        path = playbook_path or PLAYBOOK_DIR / f"{self.role}.md"
        self.playbook = path.read_text() if path.exists() else ""

    def run(self, context: ContextPackage, tags: CostTags, *, schema: dict | None = None) -> dict:
        """Run the agent. ``schema`` overrides the role's default output schema
        for one call — used when an agent has a secondary task (e.g. the Product
        Owner triaging a change request)."""
        schema = schema or self.output_schema
        system = self.system_prompt(schema)
        prompt = self.user_prompt(context)
        total = len(system) + len(prompt)
        if total > self.max_context_chars:
            raise ContextOverflowError(
                f"{self.role} context package is {total} chars, "
                f"cap is {self.max_context_chars}"
            )
        response = self.llm.complete(
            system=system,
            prompt=prompt,
            model=self.model,
            tags=tags,
        )
        try:
            data = extract_json(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise AgentOutputError([f"output is not valid JSON: {exc}"]) from exc
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as exc:
            location = "/".join(str(p) for p in exc.absolute_path) or "(root)"
            raise AgentOutputError([f"schema violation at {location}: {exc.message}"]) from exc
        return data

    def system_prompt(self, schema: dict | None = None) -> str:
        schema = schema or self.output_schema
        parts = [f"You are the {self.role} agent in a multi-agent software delivery system."]
        if self.playbook:
            parts.append(f"# Your playbook\n\n{self.playbook}")
        parts.append(
            "# Output contract\n\n"
            "Respond with exactly one JSON object (optionally inside a ```json fence) "
            "matching this JSON Schema. No prose outside the JSON.\n\n"
            f"```json\n{json.dumps(schema, indent=2)}\n```"
        )
        return "\n\n".join(parts)

    def user_prompt(self, context: ContextPackage) -> str:
        parts = [context.instructions]
        for name, content in context.artifacts.items():
            rendered = content if isinstance(content, str) else json.dumps(content, indent=2)
            parts.append(f"## {name}\n\n```json\n{rendered}\n```")
        if context.kb_entries:
            parts.append("## Knowledge base notes\n\n" + "\n".join(f"- {e}" for e in context.kb_entries))
        if context.feedback:
            parts.append(
                "## Feedback on your previous attempt\n\n"
                "Fix every item below, then resubmit the full artifact.\n\n"
                + context.feedback
            )
        return "\n\n".join(parts)
