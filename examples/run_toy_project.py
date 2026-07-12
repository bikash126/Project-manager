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
    CR_NEW_ACCEPTANCE_CRITERIA,
    CR_TRIAGE_ACCEPT,
    DEV_TICKET_1,
    DEV_TICKET_1_INSECURE,
    DEV_TICKET_2,
    DEVELOPER_RESPONSES,
    DEVOPS_RESPONSES,
    DOCS,
    DOCS_RESPONSES,
    ESTIMATOR_RESPONSES,
    OPS_INCIDENT,
    PLANNER_RESPONSES,
    PRODUCT_OWNER_RESPONSES,
    QA_RESPONSES,
    QA_TICKET_1,
    RELEASE_PATCH,
    RELEASE_RESPONSES,
    RETRO_LESSONS,
    REVIEW_TICKET_1,
    REVIEWER_RESPONSES,
    SECURITY_CONFIRM,
)
from pm_system import (
    AnalystAgent,
    ArchitectAgent,
    ArtifactStore,
    AutoApproveGate,
    ChangeRequest,
    ConsoleGate,
    ConsoleNotifier,
    CostLedger,
    DataEngineerAgent,
    DeveloperAgent,
    DevOpsAgent,
    DocWriterAgent,
    EstimatorAgent,
    KnowledgeBase,
    MeteredLLM,
    MockLLM,
    OpsAgent,
    Orchestrator,
    OrchestratorConfig,
    PlannerAgent,
    ProdIncident,
    ProductOwnerAgent,
    QAAgent,
    ReleaseManagerAgent,
    RetrospectiveAgent,
    ReviewerAgent,
    SecurityAgent,
)
from pm_system.config import CHEAP_MODEL, MID_MODEL, STRONG_MODEL
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
    parser.add_argument(
        "--change-request",
        action="store_true",
        help="after shipping, raise a CR that amends US-001 and show that only "
        "the blast radius re-runs (Phase 5)",
    )
    parser.add_argument(
        "--incident",
        action="store_true",
        help="after shipping, feed a prod incident to Ops; an actionable one is "
        "triaged into a fix that re-enters the dev loop (Phase 6)",
    )
    args = parser.parse_args(argv)

    workspace_root = args.workspace or Path(tempfile.mkdtemp(prefix="pm-toy-"))

    store = ArtifactStore()
    ledger = CostLedger(
        stage_budgets={
            "intake": 5.0, "scope": 5.0, "estimate": 5.0, "plan": 5.0,
            "architecture": 10.0, "build": 20.0, "security": 10.0,
            "review": 10.0, "qa": 10.0, "devops": 5.0, "release": 5.0, "docs": 5.0,
        }
    )

    # One MockLLM per role: QA/Reviewer/Security each run on a separate
    # instance from the Developer (D3).
    def metered(responses):
        return MeteredLLM(MockLLM(responses), ledger)

    # Per-role response queues, extended for the optional demos.
    if args.inject_secret:
        dev_responses = [DEV_TICKET_1_INSECURE, DEV_TICKET_1, DEV_TICKET_2]
        security_responses = [SECURITY_CONFIRM]  # confirms the planted key -> block
    else:
        dev_responses = list(DEVELOPER_RESPONSES)
        security_responses = []  # clean code -> scanners find nothing -> agent unused

    po_responses = list(PRODUCT_OWNER_RESPONSES)
    reviewer_responses = list(REVIEWER_RESPONSES)
    qa_responses = list(QA_RESPONSES)
    release_responses = list(RELEASE_RESPONSES)
    docs_responses = list(DOCS_RESPONSES)
    ops_responses = []
    # A CR (Phase 5) or an actionable incident (Phase 6) both re-run TCK-001 and
    # re-ship a patch, so each needs the same extra responses.
    for flag in (args.change_request, args.incident):
        if flag:
            po_responses += [CR_TRIAGE_ACCEPT]
            dev_responses += [DEV_TICKET_1]
            reviewer_responses += [REVIEW_TICKET_1]
            qa_responses += [QA_TICKET_1]
            release_responses += [RELEASE_PATCH]
            docs_responses += [DOCS]
    if args.incident:
        ops_responses = [OPS_INCIDENT]

    analyst = AnalystAgent(metered(ANALYST_RESPONSES), model=STRONG_MODEL)
    product_owner = ProductOwnerAgent(metered(po_responses), model=STRONG_MODEL)
    estimator = EstimatorAgent(metered(ESTIMATOR_RESPONSES), model=MID_MODEL)
    planner = PlannerAgent(metered(PLANNER_RESPONSES), model=MID_MODEL)
    architect = ArchitectAgent(metered(ARCHITECT_RESPONSES), model=STRONG_MODEL)
    developer = DeveloperAgent(metered(dev_responses), model=STRONG_MODEL)
    reviewer = ReviewerAgent(metered(reviewer_responses), model=STRONG_MODEL)
    security = SecurityAgent(metered(security_responses), model=STRONG_MODEL)
    qa = QAAgent(metered(qa_responses), model=STRONG_MODEL)
    data_engineer = DataEngineerAgent(metered([]), model=MID_MODEL)  # no data model in toy
    devops = DevOpsAgent(metered(DEVOPS_RESPONSES), model=MID_MODEL)
    release_manager = ReleaseManagerAgent(metered(release_responses), model=CHEAP_MODEL)
    doc_writer = DocWriterAgent(metered(docs_responses), model=CHEAP_MODEL)
    ops = OpsAgent(metered(ops_responses), model=MID_MODEL)
    retrospective = RetrospectiveAgent(metered([RETRO_LESSONS]), model=CHEAP_MODEL)
    kb = KnowledgeBase()

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
        data_engineer=data_engineer,
        devops=devops,
        release_manager=release_manager,
        doc_writer=doc_writer,
        ops=ops,
        retrospective_agent=retrospective,
        kb=kb,
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
    print(f"shipped: {result.shipped} (version {result.release_version})  [P4 exit: shipped end-to-end]")
    print(f"human interventions: {result.human_interventions}")
    print(f"KB entries written by retrospective: {result.kb_entries_written}  [P6 exit: retrospective -> KB]")
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

    if args.change_request and result.shipped:
        cr = ChangeRequest(
            cr_id="CR-001",
            description="Amend US-001 to specify body-temperature conversion (37C -> 98.6F)",
            target_story_id="US-001",
            new_acceptance_criteria=CR_NEW_ACCEPTANCE_CRITERIA,
        )
        change = orchestrator.apply_change_request("toy-temp-converter", cr)
        print("\n=== Change request CR-001 ===")
        print(f"decision: {change.decision}")
        print(
            f"blast radius: {len(change.blast_radius)}/{change.total_artifacts} artifacts "
            f"({'n/a' if change.blast_radius_ratio is None else f'{change.blast_radius_ratio:.0%}'}"
            "  [P5 exit: only affected artifacts re-run])"
        )
        print(f"  re-run: {sorted(change.blast_radius)}")
        print(f"  re-run tickets: {[t.ticket_id for t in change.rerun_tickets]}")
        print(f"new version: {change.new_version}")
        untouched = [
            a.artifact_id for a in store.list_project("toy-temp-converter")
            if a.artifact_id not in change.blast_radius
        ]
        print(f"  untouched ({len(untouched)}): {sorted(untouched)}")

    if args.incident and result.shipped:
        incident = ProdIncident(
            "INC-001",
            "Converter returns 500 on integer-valued input from the API",
            logs="ValueError: could not convert ... (see US-001)",
        )
        outcome = orchestrator.handle_incident("toy-temp-converter", incident)
        print("\n=== Prod incident INC-001 ===")
        print(f"severity: {outcome.severity}; decision: {outcome.decision}")
        print(f"triage: {outcome.triage}")
        if outcome.change_result is not None:
            print(
                f"fix re-entered the dev loop -> tickets "
                f"{[t.ticket_id for t in outcome.change_result.rerun_tickets]}, "
                f"new version {outcome.change_result.new_version}"
            )
        print(f"rolled back: {outcome.rolled_back}")

    if args.change_request or args.incident:
        cal = kb.calibration_summary()
        print(
            f"\nKB now holds {len(kb.query())} entries; "
            f"calibration multiplier {cal['multiplier']} from {cal['samples']} sample(s) "
            "(fed to the next project's Estimator)"
        )

    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
