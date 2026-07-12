"""Phase 6 operate + learn: retrospective writes to the KB, calibration feeds
the estimator, and Ops triages a prod incident back into the dev loop."""

import json
import sys

import pytest

from examples.toy_responses import (
    ANALYST_RESPONSES,
    ARCHITECTURE,
    BACKLOG,
    DEV_TICKET_1,
    DEV_TICKET_2,
    DEVOPS,
    DOCS,
    ESTIMATOR_RESPONSES,
    PLANNER_RESPONSES,
    QA_TICKET_1,
    QA_TICKET_2,
    RELEASE,
)
from pm_system import (
    AnalystAgent,
    ArchitectAgent,
    ArtifactStore,
    AutoApproveGate,
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
    RegexSecretScanner,
    ReleaseManagerAgent,
    RetrospectiveAgent,
    ReviewerAgent,
    SecurityAgent,
    SecurityScanSuite,
)
from pm_system.gates.gate import GateDecision, HumanGate
from pm_system.kb.store import KIND_ADR, KIND_CALIBRATION, KIND_LESSON
from pm_system.notify.notifier import NullNotifier
from pm_system.sandbox.runner import LocalSandbox

REVIEW_APPROVE = json.dumps({"verdict": "approve", "comments": []})
TRIAGE_ACCEPT = json.dumps({"decision": "accept", "reason": "prod fix"})
RELEASE_011 = json.dumps(
    {
        "version": "0.1.1",
        "changelog": [{"type": "fixed", "description": "prod fix"}],
        "deploy_plan": "tag 0.1.1",
        "rollback_plan": "revert to 0.1.0",
    }
)
RETRO = json.dumps(
    {"lessons": [{"category": "estimation", "lesson": "conversion work was underestimated"}]}
)
OPS_ACTIONABLE = json.dumps(
    {
        "actionable": True,
        "severity": "high",
        "triage": "US-001 conversion rejects valid input",
        "recommend_rollback": False,
        "target_story_id": "US-001",
        "fix_summary": "accept integer temperatures too",
    }
)
OPS_ROLLBACK = json.dumps(
    {
        "actionable": False,
        "severity": "critical",
        "triage": "release is crashing on startup",
        "recommend_rollback": True,
    }
)
OPS_NOACTION = json.dumps(
    {
        "actionable": False,
        "severity": "low",
        "triage": "transient network blip, self-resolved",
        "recommend_rollback": False,
    }
)


class ScriptedGate(HumanGate):
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def request_approval(self, gate_name, digest):
        return self.decisions.pop(0) if self.decisions else GateDecision(True)


def _dev(system, prompt):
    return DEV_TICKET_2 if "ticket TCK-002" in prompt else DEV_TICKET_1


def _qa(system, prompt):
    return QA_TICKET_2 if "ticket TCK-002" in prompt else QA_TICKET_1


def _po(system, prompt):
    return TRIAGE_ACCEPT if "change request" in prompt.lower() else BACKLOG


def _release(system, prompt):
    return RELEASE_011 if "patch release" in prompt.lower() else RELEASE


def build(tmp_path, *, ops_responses=None, kb=None, gate=None, config=None):
    store = ArtifactStore()
    ledger = CostLedger()
    kb = kb if kb is not None else KnowledgeBase()

    def queue(responses):
        return MeteredLLM(MockLLM(responses), ledger)

    def handler(fn):
        return MeteredLLM(MockLLM(handler=fn), ledger)

    orchestrator = Orchestrator(
        store=store,
        ledger=ledger,
        analyst=AnalystAgent(queue(list(ANALYST_RESPONSES)), model="m"),
        product_owner=ProductOwnerAgent(handler(_po), model="m"),
        estimator=EstimatorAgent(queue(list(ESTIMATOR_RESPONSES)), model="m"),
        planner=PlannerAgent(queue(list(PLANNER_RESPONSES)), model="m"),
        architect=ArchitectAgent(queue([ARCHITECTURE]), model="m"),
        developer=DeveloperAgent(handler(_dev), model="m"),
        reviewer=ReviewerAgent(handler(lambda s, p: REVIEW_APPROVE), model="m"),
        security=SecurityAgent(queue([]), model="m"),
        qa=QAAgent(handler(_qa), model="m"),
        data_engineer=DataEngineerAgent(queue([]), model="m"),
        devops=DevOpsAgent(queue([DEVOPS]), model="m"),
        release_manager=ReleaseManagerAgent(handler(_release), model="m"),
        doc_writer=DocWriterAgent(handler(lambda s, p: DOCS), model="m"),
        ops=OpsAgent(queue(ops_responses or []), model="m"),
        retrospective_agent=RetrospectiveAgent(handler(lambda s, p: RETRO), model="m"),
        kb=kb,
        scan_suite=SecurityScanSuite([RegexSecretScanner()]),
        gate=gate or AutoApproveGate(),
        sandbox=LocalSandbox(),
        workspace_root=tmp_path / "ws",
        notifier=NullNotifier(),
        config=config or OrchestratorConfig(sandbox_timeout=120),
        test_python=sys.executable,
    )
    return orchestrator, store, kb


def test_retrospective_writes_structured_kb_entries(tmp_path):
    orchestrator, _, kb = build(tmp_path)
    result = orchestrator.run_project("toy", "temperature converter")
    assert result.shipped
    assert result.kb_entries_written > 0
    # calibration (per passed ticket), reusable ADRs, and a lesson were written
    assert len(kb.query(kind=KIND_CALIBRATION)) == 2  # two tickets
    assert len(kb.query(kind=KIND_ADR)) >= 1
    assert len(kb.query(kind=KIND_LESSON)) == 1
    assert kb.query(kind=KIND_LESSON)[0].content["category"] == "estimation"


def test_calibration_from_kb_reaches_the_next_estimator(tmp_path):
    # Pre-seed the KB with calibration so a new project's estimator sees it.
    kb = KnowledgeBase()
    kb.add(KIND_CALIBRATION, project_id="prev",
           content={"estimated_points": 2, "actual_points": 3})
    orchestrator, _, _ = build(tmp_path, kb=kb)
    orchestrator.run_project("toy", "temperature converter")
    # the estimator's prompt carried the calibration summary
    est_calls = orchestrator.estimator.llm.client.calls
    assert any("multiplier" in prompt for _, prompt in est_calls)


def test_incident_triaged_into_a_fix_that_reenters_the_dev_loop(tmp_path):
    orchestrator, store, _ = build(tmp_path, ops_responses=[OPS_ACTIONABLE])
    assert orchestrator.run_project("toy", "temperature converter").shipped

    before = store.get("toy:CODE-TCK-001").version
    incident = ProdIncident("INC-001", "converter 500s on integer input", logs="ValueError")
    outcome = orchestrator.handle_incident("toy", incident)

    assert outcome.decision == "ticketed"
    assert outcome.change_result is not None
    assert outcome.change_result.decision == "accepted"
    # the fix re-ran TCK-001 through the dev loop -> new code version
    assert store.get("toy:CODE-TCK-001").version == before + 1
    assert "TCK-001" in [t.ticket_id for t in outcome.change_result.rerun_tickets]


def test_incident_rollback_needs_human_confirm(tmp_path):
    orchestrator, _, _ = build(tmp_path, ops_responses=[OPS_ROLLBACK])
    assert orchestrator.run_project("toy", "temperature converter").shipped

    # swap in a confirming gate just for the rollback confirmation
    orchestrator.gate = ScriptedGate([GateDecision(True, "confirmed")])
    outcome = orchestrator.handle_incident(
        "toy", ProdIncident("INC-002", "startup crash after release")
    )
    assert outcome.decision == "rolled_back"
    assert outcome.rolled_back
    assert orchestrator.deployer.deployments[0].state == "rolled_back"


def test_incident_rollback_declined_by_human(tmp_path):
    orchestrator, _, _ = build(tmp_path, ops_responses=[OPS_ROLLBACK])
    assert orchestrator.run_project("toy", "temperature converter").shipped

    orchestrator.gate = ScriptedGate([GateDecision(False, "keep it up, fix forward")])
    outcome = orchestrator.handle_incident("toy", ProdIncident("INC-003", "crash"))
    assert not outcome.rolled_back
    assert orchestrator.deployer.deployments[0].state == "deployed"


def test_non_actionable_incident_is_no_op(tmp_path):
    orchestrator, store, _ = build(tmp_path, ops_responses=[OPS_NOACTION])
    assert orchestrator.run_project("toy", "temperature converter").shipped
    before = store.get("toy:CODE-TCK-001").version
    outcome = orchestrator.handle_incident("toy", ProdIncident("INC-004", "blip"))
    assert outcome.decision == "no_action"
    assert store.get("toy:CODE-TCK-001").version == before  # nothing re-ran
