"""Agent eval harness (design doc §1, started in Phase 2).

Golden input -> expected-output regression suites per agent. LLM output is
not exact-matchable, so "expected output" is expressed as named deterministic
checks (schema validation runs automatically inside Agent.run; the checks
here add coverage/consistency/plausibility assertions on top).

Prompt/config versioning: every report records a fingerprint of the agent's
system prompt (playbook + output schema). When a prompt change regresses a
suite, the fingerprint pins down which prompt version broke it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

from pm_system.agents.base import Agent, ContextPackage
from pm_system.errors import AgentOutputError
from pm_system.llm.client import CostTags

Check = tuple[str, Callable[[dict], list[str]]]  # (name, output -> defects)


@dataclass
class EvalCase:
    case_id: str
    context: ContextPackage
    checks: list[Check]


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    defects: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class EvalReport:
    agent_role: str
    prompt_fingerprint: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    def summary(self) -> str:
        lines = [
            f"{self.agent_role} evals: {sum(r.passed for r in self.results)}/"
            f"{len(self.results)} passed (prompt {self.prompt_fingerprint})"
        ]
        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            lines.append(f"  [{status}] {result.case_id}")
            for check, defects in result.defects.items():
                for defect in defects:
                    lines.append(f"      {check}: {defect}")
        return "\n".join(lines)


def prompt_fingerprint(agent: Agent) -> str:
    return hashlib.sha256(agent.system_prompt().encode()).hexdigest()[:16]


class EvalHarness:
    def __init__(self, agent: Agent, *, project_id: str = "eval"):
        self.agent = agent
        self.project_id = project_id

    def run(self, cases: list[EvalCase]) -> EvalReport:
        report = EvalReport(
            agent_role=self.agent.role, prompt_fingerprint=prompt_fingerprint(self.agent)
        )
        for case in cases:
            tags = CostTags(project_id=self.project_id, stage="eval", agent=self.agent.role)
            try:
                output = self.agent.run(case.context, tags)
            except AgentOutputError as exc:
                report.results.append(
                    CaseResult(case.case_id, passed=False, defects={"schema": exc.defects})
                )
                continue
            defects = {
                name: found for name, check in case.checks if (found := check(output))
            }
            report.results.append(
                CaseResult(case.case_id, passed=not defects, defects=defects)
            )
        return report
