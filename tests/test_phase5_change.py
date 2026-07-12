"""Phase 5 change management: CR triage, impact analysis, stale invalidation,
and re-running only the blast radius."""

import json
import sys

import pytest

from examples.toy_responses import (
    ANALYST_RESPONSES,
    ARCHITECT_RESPONSES,
    DEV_TICKET_1,
    DEV_TICKET_2,
    DEVOPS_RESPONSES,
    DOCS,
    ESTIMATOR_RESPONSES,
    PLANNER_RESPONSES,
    PRODUCT_OWNER_RESPONSES,
    QA_TICKET_1,
    QA_TICKET_2,
    RELEASE,
)
from pm_system import (
    AnalystAgent,
    ArchitectAgent,
    ArtifactStatus,
    ArtifactStore,
    AutoApproveGate,
    ChangeRequest,
    CostLedger,
    DataEngineerAgent,
    DeveloperAgent,
    DevOpsAgent,
    DocWriterAgent,
    EstimatorAgent,
    MeteredLLM,
    MockLLM,
    Orchestrator,
    OrchestratorConfig,
    PlannerAgent,
    ProductOwnerAgent,
    QAAgent,
    RegexSecretScanner,
    ReleaseManagerAgent,
    ReviewerAgent,
    SecurityAgent,
    SecurityScanSuite,
)
from pm_system.gates.gate import GateDecision, HumanGate
from pm_system.notify.notifier import NullNotifier

REVIEW_APPROVE = json.dumps({"verdict": "approve", "comments": []})
TRIAGE_ACCEPT = json.dumps({"decision": "accept", "reason": "small, high-value fix"})
TRIAGE_REJECT = json.dumps({"decision": "reject", "reason": "out of scope"})
TRIAGE_DEFER = json.dumps({"decision": "defer", "reason": "next milestone"})
RELEASE_011 = json.dumps(
    {
        "version": "0.1.1",
        "changelog": [{"type": "changed", "description": "tightened validation"}],
        "deploy_plan": "tag 0.1.1 and promote",
        "rollback_plan": "revert to 0.1.0",
    }
)

NEW_ACS = [
    {"id": "AC-001", "given": "100 Celsius", "when": "converting to F", "then": "212"},
    {"id": "AC-002", "given": "37 Celsius", "when": "converting to F", "then": "98.6"},
]


class ScriptedGate(HumanGate):
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def request_approval(self, gate_name, digest):
        return self.decisions.pop(0)


def build(tmp_path, *, po_responses, dev_responses, qa_responses, release_responses,
          docs_responses, gate=None, config=None):
    store = ArtifactStore()
    ledger = CostLedger()

    def metered(responses):
        return MeteredLLM(MockLLM(responses), ledger)

    orchestrator = Orchestrator(
        store=store,
        ledger=ledger,
        analyst=AnalystAgent(metered(list(ANALYST_RESPONSES)), model="m"),
        product_owner=ProductOwnerAgent(metered(po_responses), model="m"),
        estimator=EstimatorAgent(metered(list(ESTIMATOR_RESPONSES)), model="m"),
        planner=PlannerAgent(metered(list(PLANNER_RESPONSES)), model="m"),
        architect=ArchitectAgent(metered(list(ARCHITECT_RESPONSES)), model="m"),
        developer=DeveloperAgent(metered(dev_responses), model="m"),
        reviewer=ReviewerAgent(
            MeteredLLM(MockLLM(handler=lambda s, p: REVIEW_APPROVE), ledger), model="m"
        ),
        security=SecurityAgent(metered([]), model="m"),
        qa=QAAgent(metered(qa_responses), model="m"),
        data_engineer=DataEngineerAgent(metered([]), model="m"),
        devops=DevOpsAgent(metered(list(DEVOPS_RESPONSES)), model="m"),
        release_manager=ReleaseManagerAgent(metered(release_responses), model="m"),
        doc_writer=DocWriterAgent(metered(docs_responses), model="m"),
        scan_suite=SecurityScanSuite([RegexSecretScanner()]),
        gate=gate or AutoApproveGate(),
        sandbox=__import__("pm_system.sandbox.runner", fromlist=["LocalSandbox"]).LocalSandbox(),
        workspace_root=tmp_path / "ws",
        notifier=NullNotifier(),
        config=config or OrchestratorConfig(sandbox_timeout=120),
        test_python=sys.executable,
    )
    return orchestrator, store


def _shipped(tmp_path, *, po_extra=None, gate=None, config=None):
    """Run the toy project to a shipped state, ready for a CR."""
    orchestrator, store = build(
        tmp_path,
        po_responses=list(PRODUCT_OWNER_RESPONSES) + (po_extra or []),
        dev_responses=[DEV_TICKET_1, DEV_TICKET_2, DEV_TICKET_1],  # +1 for CR re-run
        qa_responses=[QA_TICKET_1, QA_TICKET_2, QA_TICKET_1],
        release_responses=[RELEASE, RELEASE_011],
        docs_responses=[DOCS, DOCS],
        gate=gate,
        config=config,
    )
    result = orchestrator.run_project("toy", "temperature converter")
    assert result.shipped, result.escalations
    return orchestrator, store


def test_accepted_ac_change_reruns_only_the_blast_radius(tmp_path):
    orchestrator, store = _shipped(tmp_path, po_extra=[TRIAGE_ACCEPT])
    cr = ChangeRequest(
        cr_id="CR-001",
        description="Add a body-temperature conversion example to US-001",
        target_story_id="US-001",
        new_acceptance_criteria=NEW_ACS,
    )
    result = orchestrator.apply_change_request("toy", cr)

    assert result.decision == "accepted"
    # US-001's ticket + code + qa + review re-ran; US-002's did not
    assert store.get("toy:TCK-001").version == 2
    assert store.get("toy:CODE-TCK-001").version == 2
    assert store.get("toy:TCK-002").version == 1
    assert store.get("toy:CODE-TCK-002").version == 1
    assert store.get("toy:QA-TCK-002").version == 1
    # PRD superseded by the amended version; planning artifacts untouched
    assert [a.version for a in store.history("toy:PRD")] == [1, 2]
    assert store.get("toy:WBS").version == 1
    assert store.get("toy:ARCH").version == 1
    # blast radius is a minority of the project
    assert result.blast_radius_ratio < 0.5
    # a patch release went out
    assert result.new_version == "0.1.1"
    assert store.get("toy:PRD").content["user_stories"][0]["acceptance_criteria"] == NEW_ACS


def test_rejected_cr_changes_nothing(tmp_path):
    orchestrator, store = _shipped(tmp_path, po_extra=[TRIAGE_REJECT])
    before = {a.artifact_id: a.version for a in store.list_project("toy")}
    result = orchestrator.apply_change_request(
        "toy", ChangeRequest("CR-001", "nope", "US-001", new_acceptance_criteria=NEW_ACS)
    )
    assert result.decision == "rejected"
    assert result.rerun_tickets == []
    after = {a.artifact_id: a.version for a in store.list_project("toy")}
    assert before == after  # nothing re-run


def test_deferred_cr_changes_nothing(tmp_path):
    orchestrator, store = _shipped(tmp_path, po_extra=[TRIAGE_DEFER])
    result = orchestrator.apply_change_request(
        "toy", ChangeRequest("CR-002", "later", "US-001", new_acceptance_criteria=NEW_ACS)
    )
    assert result.decision == "deferred"
    assert store.get("toy:TCK-001").version == 1


def test_large_delta_goes_to_a_gate_and_can_be_rejected(tmp_path):
    # threshold below the affected WBS points (WBS-001 = 3) so the gate fires
    orchestrator, store = _shipped(
        tmp_path,
        po_extra=[TRIAGE_ACCEPT],
        config=OrchestratorConfig(sandbox_timeout=120, cr_gate_threshold_points=2),
    )
    orchestrator.gate = ScriptedGate([GateDecision(False, "too expensive this sprint")])
    result = orchestrator.apply_change_request(
        "toy", ChangeRequest("CR-003", "big change", "US-001", new_acceptance_criteria=NEW_ACS)
    )
    assert result.decision == "rejected"
    assert result.delta_points == 3
    assert store.get("toy:TCK-001").version == 1  # not re-run


def test_unknown_story_escalates(tmp_path):
    orchestrator, store = _shipped(tmp_path, po_extra=[TRIAGE_ACCEPT])
    result = orchestrator.apply_change_request(
        "toy", ChangeRequest("CR-004", "ghost", "US-999", new_acceptance_criteria=NEW_ACS)
    )
    assert result.decision == "escalated"
    assert any("US-999" in e for e in result.escalations)
