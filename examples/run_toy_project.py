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

from examples.toy_responses import (
    ANALYST_RESPONSES,
    ARCHITECT_RESPONSES,
    DEV_TICKET_1,
    DEV_TICKET_1_INSECURE,
    DEV_TICKET_2,
    DEVELOPER_RESPONSES,
    ESTIMATOR_RESPONSES,
    PLANNER_RESPONSES,
    PRODUCT_OWNER_RESPONSES,
    QA_RESPONSES,
    REVIEWER_RESPONSES,
    SECURITY_CONFIRM,
)
from pm_system import (
    AnalystAgent,
    ArchitectAgent,
    ArtifactStore,
    AutoApproveGate,
    ConsoleGate,
    ConsoleNotifier,
    CostLedger,
    DeveloperAgent,
    EstimatorAgent,
    MeteredLLM,
    MockLLM,
    Orchestrator,
    OrchestratorConfig,
    PlannerAgent,
    ProductOwnerAgent,
    QAAgent,
    ReviewerAgent,
    SecurityAgent,
)
from pm_system.config import MID_MODEL, STRONG_MODEL
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
    parser.add_argument(
        "--inject-secret",
        action="store_true",
        help="developer hardcodes a secret on the first TCK-001 attempt; the "
        "Security gate should catch it pre-review, block, and pass on retry",
    )
    args = parser.parse_args(argv)

    workspace_root = args.workspace or Path(tempfile.mkdtemp(prefix="pm-toy-"))

    store = ArtifactStore()
    ledger = CostLedger(
        stage_budgets={
            "intake": 5.0, "scope": 5.0, "estimate": 5.0, "plan": 5.0,
            "architecture": 10.0, "build": 20.0, "security": 10.0,
            "review": 10.0, "qa": 10.0,
        }
    )

    # One MockLLM per role: QA/Reviewer/Security each run on a separate
    # instance from the Developer (D3).
    def metered(responses):
        return MeteredLLM(MockLLM(responses), ledger)

    analyst = AnalystAgent(metered(ANALYST_RESPONSES), model=STRONG_MODEL)
    product_owner = ProductOwnerAgent(metered(PRODUCT_OWNER_RESPONSES), model=STRONG_MODEL)
    estimator = EstimatorAgent(metered(ESTIMATOR_RESPONSES), model=MID_MODEL)
    planner = PlannerAgent(metered(PLANNER_RESPONSES), model=MID_MODEL)
    architect = ArchitectAgent(metered(ARCHITECT_RESPONSES), model=STRONG_MODEL)
    if args.inject_secret:
        dev_responses = [DEV_TICKET_1_INSECURE, DEV_TICKET_1, DEV_TICKET_2]
        security_responses = [SECURITY_CONFIRM]  # confirms the planted key -> block
    else:
        dev_responses = DEVELOPER_RESPONSES
        security_responses = []  # clean code -> scanners find nothing -> agent unused

    developer = DeveloperAgent(metered(dev_responses), model=STRONG_MODEL)
    reviewer = ReviewerAgent(metered(REVIEWER_RESPONSES), model=STRONG_MODEL)
    security = SecurityAgent(metered(security_responses), model=STRONG_MODEL)
    qa = QAAgent(metered(QA_RESPONSES), model=STRONG_MODEL)

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
        product_owner=product_owner,
        estimator=estimator,
        planner=planner,
        architect=architect,
        developer=developer,
        reviewer=reviewer,
        security=security,
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
        print(
            f"  {ticket.ticket_id} ({ticket.story_id}): {ticket.status} in "
            f"{ticket.attempts} attempt(s), PR #{ticket.pr_number} "
            f"[sec findings={ticket.security_findings}, review blocks={ticket.review_blocks}, "
            f"qa fails={ticket.qa_failures}]"
        )
    rate = result.first_try_validation_rate
    pr_rate = result.pr_pass_rate
    print(f"first-try validation rate: {'n/a' if rate is None else f'{rate:.0%}'} (P1/P2 exit: >= 80%)")
    print(f"PR pass rate (Review+QA within 3): {'n/a' if pr_rate is None else f'{pr_rate:.0%}'} (P3 exit: >= 70%)")
    print(f"security findings: {result.security_findings_total} ({result.security_blocks_total} blocking)")
    print(f"total cost: ${result.total_cost_usd:.4f}")
    print(f"workspace: {result.workspace}")

    print("\n=== Artifacts ===")
    for artifact in store.list_project("toy-temp-converter"):
        print(
            f"  {artifact.artifact_id} v{artifact.version} [{artifact.status.value}] "
            f"type={artifact.artifact_type} trace={list(artifact.trace_ids)}"
        )

    print("\n=== Traceability: what depends on US-001? ===")
    for artifact in store.find_by_trace("toy-temp-converter", "US-001"):
        print(f"  {artifact.artifact_id} ({artifact.artifact_type})")
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
