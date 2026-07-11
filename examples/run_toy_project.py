"""Run the toy project end-to-end (Phase 1 exit criterion).

Uses scripted MockLLM responses so the run is deterministic and offline; the
generated code is real and executes in the sandbox. Swap the MockLLM clients
for AnthropicLLM (and the AutoApproveGate for ConsoleGate/SlackGate) to run
against real models.

    python -m examples.run_toy_project [--gate console] [--workspace DIR]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from examples.toy_responses import ANALYST_RESPONSES, DEVELOPER_RESPONSES, QA_RESPONSES
from pm_system import (
    AnalystAgent,
    ArtifactStore,
    AutoApproveGate,
    ConsoleGate,
    ConsoleNotifier,
    CostLedger,
    DeveloperAgent,
    MeteredLLM,
    MockLLM,
    Orchestrator,
    OrchestratorConfig,
    QAAgent,
)
from pm_system.config import STRONG_MODEL
from pm_system.sandbox.runner import DockerSandbox, LocalSandbox, default_sandbox


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", choices=["auto", "console"], default="auto")
    parser.add_argument(
        "--sandbox",
        choices=["auto", "local", "docker"],
        default="auto",
        help="docker requires the pm-sandbox:latest image (see sandbox/Dockerfile)",
    )
    parser.add_argument("--workspace", type=Path, default=None)
    args = parser.parse_args(argv)

    workspace_root = args.workspace or Path(tempfile.mkdtemp(prefix="pm-toy-"))

    store = ArtifactStore()
    ledger = CostLedger(stage_budgets={"intake": 5.0, "build": 20.0, "qa": 10.0})

    # One MockLLM per role: QA runs on a separate instance from the Developer (D3).
    analyst = AnalystAgent(MeteredLLM(MockLLM(ANALYST_RESPONSES), ledger), model=STRONG_MODEL)
    developer = DeveloperAgent(MeteredLLM(MockLLM(DEVELOPER_RESPONSES), ledger), model=STRONG_MODEL)
    qa = QAAgent(MeteredLLM(MockLLM(QA_RESPONSES), ledger), model=STRONG_MODEL)

    if args.sandbox == "local":
        sandbox = LocalSandbox()
    elif args.sandbox == "docker":
        sandbox = DockerSandbox()
    else:
        sandbox = default_sandbox()
    # Inside the Docker image "python" is correct; the local fallback must use
    # the interpreter that has pytest installed.
    test_python = sys.executable if isinstance(sandbox, LocalSandbox) else "python"

    orchestrator = Orchestrator(
        store=store,
        ledger=ledger,
        analyst=analyst,
        developer=developer,
        qa=qa,
        gate=ConsoleGate() if args.gate == "console" else AutoApproveGate(),
        sandbox=sandbox,
        workspace_root=workspace_root,
        notifier=ConsoleNotifier(),
        config=OrchestratorConfig(),
        test_python=test_python,
    )

    result = orchestrator.run_project("toy-temp-converter", "Build a CLI temperature converter")

    print("\n=== Result ===")
    print(f"status: {result.status}")
    for ticket in result.tickets:
        print(f"  {ticket.ticket_id} ({ticket.story_id}): {ticket.status} in {ticket.attempts} attempt(s)")
    rate = result.first_try_validation_rate
    print(f"first-try validation rate: {'n/a' if rate is None else f'{rate:.0%}'} (exit criterion: >= 80%)")
    print(f"total cost: ${result.total_cost_usd:.4f}")
    print(f"workspace: {result.workspace}")

    print("\n=== Artifacts ===")
    for artifact in store.list_project("toy-temp-converter"):
        print(
            f"  {artifact.artifact_id} v{artifact.version} [{artifact.status.value}] "
            f"type={artifact.artifact_type} trace={list(artifact.trace_ids)}"
        )
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
