"""Run golden eval suites against real models.

    ANTHROPIC_API_KEY=... python -m pm_system.evals.run [--role architect] [--model ID]

Runs every Phase-2 suite by default and exits nonzero on any failure, so it
can gate prompt/playbook changes in CI.
"""

from __future__ import annotations

import argparse
import sys

from pm_system.agents.architect import ArchitectAgent
from pm_system.agents.estimator import EstimatorAgent
from pm_system.agents.planner import PlannerAgent
from pm_system.agents.product_owner import ProductOwnerAgent
from pm_system.config import MID_MODEL, STRONG_MODEL
from pm_system.costs.ledger import CostLedger
from pm_system.evals.golden import cases_for
from pm_system.evals.harness import EvalHarness
from pm_system.llm.client import AnthropicLLM, MeteredLLM

AGENTS = {
    "product_owner": (ProductOwnerAgent, STRONG_MODEL),
    "estimator": (EstimatorAgent, MID_MODEL),
    "planner": (PlannerAgent, MID_MODEL),
    "architect": (ArchitectAgent, STRONG_MODEL),
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(AGENTS), action="append", dest="roles")
    parser.add_argument("--model", help="override the model for every role")
    args = parser.parse_args(argv)

    ledger = CostLedger()
    client = AnthropicLLM()
    exit_code = 0
    for role in args.roles or sorted(AGENTS):
        agent_cls, default_model = AGENTS[role]
        agent = agent_cls(MeteredLLM(client, ledger), model=args.model or default_model)
        report = EvalHarness(agent).run(cases_for(role))
        print(report.summary())
        if not report.passed:
            exit_code = 1
    print(f"\neval spend: ${ledger.project_spend('eval'):.4f}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
