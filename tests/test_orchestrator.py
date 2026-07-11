"""End-to-end tests of the Phase 1+2 state machine on a one-story mini project."""

import json
import sys

import pytest

from pm_system.agents.analyst import AnalystAgent
from pm_system.agents.architect import ArchitectAgent
from pm_system.agents.developer import DeveloperAgent
from pm_system.agents.estimator import EstimatorAgent
from pm_system.agents.planner import PlannerAgent
from pm_system.agents.product_owner import ProductOwnerAgent
from pm_system.agents.qa import QAAgent
from pm_system.agents.reviewer import ReviewerAgent
from pm_system.agents.security import SecurityAgent
from pm_system.artifacts.store import ArtifactStatus, ArtifactStore
from pm_system.config import OrchestratorConfig
from pm_system.costs.ledger import CostLedger
from pm_system.gates.gate import AutoApproveGate, GateDecision, HumanGate
from pm_system.llm.client import MeteredLLM, MockLLM
from pm_system.notify.notifier import NullNotifier
from pm_system.orchestrator.orchestrator import Orchestrator
from pm_system.sandbox.runner import LocalSandbox
from pm_system.security.scanners import RegexSecretScanner, SecurityScanSuite

PRD = json.dumps(
    {
        "title": "Greeter",
        "summary": "A greeting library.",
        "user_stories": [
            {
                "id": "US-001",
                "story": "As a user, I can greet someone by name.",
                "acceptance_criteria": [
                    {
                        "id": "AC-001",
                        "given": "the name World",
                        "when": "I greet",
                        "then": "the result is 'Hello, World!'",
                    }
                ],
            }
        ],
    }
)

BACKLOG = json.dumps(
    {
        "mvp_story_ids": ["US-001"],
        "backlog": [
            {"story_id": "US-001", "priority": 1, "rationale": "core capability"}
        ],
        "cut_list": [],
    }
)

WBS = json.dumps(
    {
        "wbs_items": [
            {
                "id": "WBS-001",
                "story_id": "US-001",
                "description": "greet function with tests",
                "estimate_points": 2,
                "confidence": {"low": 1, "high": 3},
            }
        ],
        "risk_register": [],
        "total_points": 2,
    }
)

PLAN = json.dumps(
    {
        "sprints": [{"number": 1, "goal": "ship greeter", "wbs_ids": ["WBS-001"]}],
        "dependencies": [],
        "milestones": [{"name": "MVP", "sprint": 1}],
    }
)

ARCH = json.dumps(
    {
        "overview": "Single pure-function module.",
        "components": [
            {
                "name": "greeter-core",
                "responsibility": "greeting formatting",
                "story_ids": ["US-001"],
            }
        ],
        "adrs": [
            {
                "id": "ADR-001",
                "title": "pure function, no classes",
                "context": "single stateless operation",
                "decision": "module-level function",
                "alternatives": ["Greeter class"],
                "consequences": "trivially testable",
            }
        ],
        "api_contracts": [],
        "data_model": [],
    }
)

GOOD_CODE = 'def greet(name):\n    return f"Hello, {name}!"\n'
BROKEN_CODE = 'def greet(name):\n    return f"Hello {name}"\n'  # missing comma and bang
UNIT_TEST = (
    "from greeter import greet\n\n\n"
    "def test_greet():\n    assert greet('World') == 'Hello, World!'\n"
)


def _dev_response(code):
    return json.dumps(
        {
            "ticket_id": "TCK-001",
            "files": [
                {"path": "greeter.py", "content": code},
                {"path": "tests/test_greeter.py", "content": UNIT_TEST},
            ],
        }
    )


QA_RESPONSE = json.dumps(
    {
        "ticket_id": "TCK-001",
        "test_plan": [{"ac_id": "AC-001", "description": "greet('World') is exact"}],
        "test_files": [
            {
                "path": "tests/qa/test_tck001.py",
                "content": (
                    "from greeter import greet\n\n\n"
                    "def test_ac_001():\n    assert greet('World') == 'Hello, World!'\n"
                ),
            }
        ],
    }
)

REVIEW_APPROVE = json.dumps(
    {"verdict": "approve", "comments": [{"path": "greeter.py", "severity": "info", "comment": "ok"}]}
)
REVIEW_BLOCK = json.dumps(
    {
        "verdict": "block",
        "comments": [
            {"path": "greeter.py", "severity": "major", "comment": "missing input validation"}
        ],
    }
)


class ScriptedGate(HumanGate):
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.digests = []

    def request_approval(self, gate_name, digest):
        self.digests.append((gate_name, digest))
        return self.decisions.pop(0)


def make_orchestrator(
    tmp_path,
    *,
    analyst_responses=None,
    po_responses=None,
    estimator_responses=None,
    planner_responses=None,
    architect_responses=None,
    dev_responses=None,
    reviewer_responses=None,
    security_responses=None,
    qa_responses=None,
    scan_suite=None,
    gate=None,
    gate2=None,
    ledger=None,
    config=None,
):
    store = ArtifactStore()
    ledger = ledger or CostLedger()

    def metered(responses):
        return MeteredLLM(MockLLM(responses if responses is not None else []), ledger)

    # Reviewer defaults to always-approve via a handler so tests needn't count calls.
    reviewer_llm = (
        metered(reviewer_responses)
        if reviewer_responses is not None
        else MeteredLLM(MockLLM(handler=lambda s, p: REVIEW_APPROVE), ledger)
    )

    orchestrator = Orchestrator(
        store=store,
        ledger=ledger,
        analyst=AnalystAgent(metered(analyst_responses or [PRD]), model="test-sonnet"),
        product_owner=ProductOwnerAgent(metered(po_responses or [BACKLOG]), model="test-sonnet"),
        estimator=EstimatorAgent(metered(estimator_responses or [WBS]), model="test-sonnet"),
        planner=PlannerAgent(metered(planner_responses or [PLAN]), model="test-sonnet"),
        architect=ArchitectAgent(metered(architect_responses or [ARCH]), model="test-sonnet"),
        developer=DeveloperAgent(metered(dev_responses or [_dev_response(GOOD_CODE)]), model="test-sonnet"),
        reviewer=ReviewerAgent(reviewer_llm, model="test-sonnet"),
        security=SecurityAgent(metered(security_responses), model="test-sonnet"),
        qa=QAAgent(metered(qa_responses or [QA_RESPONSE]), model="test-sonnet"),
        scan_suite=scan_suite or SecurityScanSuite([RegexSecretScanner()]),
        gate=gate or AutoApproveGate(),
        gate2=gate2,
        sandbox=LocalSandbox(),
        workspace_root=tmp_path / "workspaces",
        notifier=NullNotifier(),
        config=config or OrchestratorConfig(sandbox_timeout=120),
        test_python=sys.executable,
    )
    return orchestrator, store, ledger


def test_happy_path_end_to_end(tmp_path):
    orchestrator, store, ledger = make_orchestrator(tmp_path)
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed"
    assert [(t.ticket_id, t.status, t.attempts) for t in result.tickets] == [
        ("TCK-001", "passed", 1)
    ]
    assert result.first_try_validation_rate == 1.0
    assert result.total_cost_usd > 0

    # gate 1 approved the whole upstream chain; gate 2 approved the design
    for suffix in ("PRD", "BACKLOG", "WBS", "PLAN", "ARCH"):
        assert store.get(f"proj:{suffix}").status == ArtifactStatus.APPROVED

    # traceability: ticket -> WBS + story; code -> ticket + WBS + story
    assert store.get("proj:TCK-001").trace_ids == ("WBS-001", "US-001")
    assert store.get("proj:CODE-TCK-001").trace_ids == ("TCK-001", "WBS-001", "US-001")
    assert set(store.get("proj:QA-TCK-001").trace_ids) == {"TCK-001", "AC-001"}
    depends_on_story = {a.artifact_id for a in store.find_by_trace("proj", "US-001")}
    assert {"proj:PRD", "proj:TCK-001", "proj:CODE-TCK-001"} <= depends_on_story

    # a review artifact was recorded and the PR was merged
    assert store.get("proj:REVIEW-TCK-001").content["verdict"] == "approve"
    assert result.pr_pass_rate == 1.0
    assert result.tickets[0].pr_number is not None
    assert orchestrator.pr_publisher.prs[0].state == "merged"

    # code landed and cost is attributed per stage across the whole pipeline
    # (security stage absent: clean code produced no findings, so no LLM call)
    assert (result.workspace / "greeter.py").exists()
    assert set(ledger.summary("proj")) == {
        "intake", "scope", "estimate", "plan", "architecture", "build", "review", "qa",
    }


def test_dev_failure_retries_with_bug_report(tmp_path):
    orchestrator, _, _ = make_orchestrator(
        tmp_path,
        dev_responses=[_dev_response(BROKEN_CODE), _dev_response(GOOD_CODE)],
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed"
    assert result.tickets[0].attempts == 2

    # the retry prompt contained the failing test output
    dev_client = orchestrator.developer.llm.client
    assert "failed" in dev_client.calls[1][1].lower()


def test_retry_cap_escalates_to_human(tmp_path):
    orchestrator, store, _ = make_orchestrator(
        tmp_path,
        dev_responses=[_dev_response(BROKEN_CODE)] * 3,
        qa_responses=[],  # QA is never reached
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "escalated"
    assert result.tickets[0].status == "escalated"
    assert result.tickets[0].attempts == 3
    assert result.escalations
    with pytest.raises(Exception):
        store.get("proj:CODE-TCK-001")


def test_gate1_rejection_routes_feedback_to_product_owner(tmp_path):
    gate = ScriptedGate(
        [
            GateDecision(False, "cut scope further"),  # gate 1, round 1
            GateDecision(True),  # gate 1, round 2
            GateDecision(True),  # gate 2
        ]
    )
    orchestrator, store, _ = make_orchestrator(
        tmp_path,
        po_responses=[BACKLOG, BACKLOG],
        estimator_responses=[WBS, WBS],
        planner_responses=[PLAN, PLAN],
        gate=gate,
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed"
    # the rejection reason reached the Product Owner, not the Analyst
    po_client = orchestrator.product_owner.llm.client
    assert "cut scope further" in po_client.calls[1][1]
    assert len(orchestrator.analyst.llm.client.calls) == 1
    # the whole planning chain re-ran: new versions of backlog/wbs/plan
    for suffix in ("BACKLOG", "WBS", "PLAN"):
        history = store.history(f"proj:{suffix}")
        assert [a.version for a in history] == [1, 2]
        assert history[0].status == ArtifactStatus.SUPERSEDED
        assert history[1].status == ArtifactStatus.APPROVED
    # the PRD was not re-drafted
    assert [a.version for a in store.history("proj:PRD")] == [1]


def test_gate1_rejections_exhaust_retry_cap(tmp_path):
    gate = ScriptedGate([GateDecision(False, "no")] * 3)
    orchestrator, _, _ = make_orchestrator(
        tmp_path,
        po_responses=[BACKLOG] * 3,
        estimator_responses=[WBS] * 3,
        planner_responses=[PLAN] * 3,
        gate=gate,
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "escalated"
    assert "not approved" in result.escalations[0]


def test_gate2_rejection_routes_feedback_to_architect(tmp_path):
    gate = ScriptedGate(
        [
            GateDecision(True),  # gate 1
            GateDecision(False, "missing failure-mode ADR"),  # gate 2, round 1
            GateDecision(True),  # gate 2, round 2
        ]
    )
    orchestrator, store, _ = make_orchestrator(
        tmp_path, architect_responses=[ARCH, ARCH], gate=gate
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed"
    architect_client = orchestrator.architect.llm.client
    assert "missing failure-mode ADR" in architect_client.calls[1][1]
    assert [a.version for a in store.history("proj:ARCH")] == [1, 2]


def test_gate2_can_be_disabled(tmp_path):
    gate = ScriptedGate([GateDecision(True)])  # only gate 1 fires
    orchestrator, store, _ = make_orchestrator(
        tmp_path,
        gate=gate,
        config=OrchestratorConfig(sandbox_timeout=120, gate2_enabled=False),
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "completed"
    assert len(gate.digests) == 1
    assert store.get("proj:ARCH").status == ArtifactStatus.APPROVED


def test_invalid_planning_output_is_retried_with_defects(tmp_path):
    bad_wbs = json.dumps(
        {
            "wbs_items": [
                {
                    "id": "WBS-001",
                    "story_id": "US-001",
                    "description": "greet function",
                    "estimate_points": 2,
                    "confidence": {"low": 1, "high": 3},
                }
            ],
            "risk_register": [],
            "total_points": 99,  # inconsistent with the sum
        }
    )
    orchestrator, _, _ = make_orchestrator(
        tmp_path, estimator_responses=[bad_wbs, WBS]
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "completed"
    estimator_client = orchestrator.estimator.llm.client
    assert "total_points" in estimator_client.calls[1][1]
    # wbs failed first try -> rate below 100% (8 tracked artifacts incl. review)
    assert result.first_try_validation_rate == pytest.approx(7 / 8)


def test_budget_hard_stop(tmp_path):
    ledger = CostLedger(stage_budgets={"intake": 0.0})
    orchestrator, _, _ = make_orchestrator(tmp_path, ledger=ledger)
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "budget_exceeded"


def test_context_cap_escalates(tmp_path):
    orchestrator, _, _ = make_orchestrator(tmp_path)
    orchestrator.analyst.max_context_chars = 50  # force overflow
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "escalated"
    assert "context cap exceeded" in result.escalations[0]


@pytest.mark.parametrize("shared_role", ["qa", "reviewer", "security"])
def test_d3_shared_llm_with_developer_is_refused(tmp_path, shared_role):
    ledger = CostLedger()
    shared = MeteredLLM(MockLLM(handler=lambda s, p: "{}"), ledger)

    def sep():
        return MeteredLLM(MockLLM(handler=lambda s, p: "{}"), ledger)

    kwargs = dict(
        store=ArtifactStore(),
        ledger=ledger,
        analyst=AnalystAgent(sep(), model="m"),
        product_owner=ProductOwnerAgent(sep(), model="m"),
        estimator=EstimatorAgent(sep(), model="m"),
        planner=PlannerAgent(sep(), model="m"),
        architect=ArchitectAgent(sep(), model="m"),
        developer=DeveloperAgent(shared, model="m"),
        qa=QAAgent(shared if shared_role == "qa" else sep(), model="m"),
        reviewer=ReviewerAgent(shared if shared_role == "reviewer" else sep(), model="m"),
        security=SecurityAgent(shared if shared_role == "security" else sep(), model="m"),
        gate=AutoApproveGate(),
        sandbox=LocalSandbox(),
        workspace_root=tmp_path,
    )
    with pytest.raises(ValueError, match="D3"):
        Orchestrator(**kwargs)


def test_invalid_analyst_output_counts_against_first_try_rate(tmp_path):
    orchestrator, _, _ = make_orchestrator(
        tmp_path, analyst_responses=["not json", PRD]
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "completed"
    # prd failed first try; backlog/wbs/plan/arch/code/review/qa passed -> 7/8
    assert result.first_try_validation_rate == pytest.approx(7 / 8)
