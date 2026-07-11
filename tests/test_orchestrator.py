"""End-to-end tests of the Phase 1 state machine on a one-story mini project."""

import json
import sys

import pytest

from pm_system.agents.analyst import AnalystAgent
from pm_system.agents.developer import DeveloperAgent
from pm_system.agents.qa import QAAgent
from pm_system.artifacts.store import ArtifactStatus, ArtifactStore
from pm_system.config import OrchestratorConfig
from pm_system.costs.ledger import CostLedger
from pm_system.gates.gate import AutoApproveGate, GateDecision, HumanGate
from pm_system.llm.client import MeteredLLM, MockLLM
from pm_system.notify.notifier import NullNotifier
from pm_system.orchestrator.orchestrator import Orchestrator
from pm_system.sandbox.runner import LocalSandbox

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


class ScriptedGate(HumanGate):
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.digests = []

    def request_approval(self, gate_name, digest):
        self.digests.append(digest)
        return self.decisions.pop(0)


def make_orchestrator(
    tmp_path,
    *,
    analyst_responses,
    dev_responses,
    qa_responses,
    gate=None,
    ledger=None,
):
    store = ArtifactStore()
    ledger = ledger or CostLedger()
    orchestrator = Orchestrator(
        store=store,
        ledger=ledger,
        analyst=AnalystAgent(MeteredLLM(MockLLM(analyst_responses), ledger), model="test-sonnet"),
        developer=DeveloperAgent(MeteredLLM(MockLLM(dev_responses), ledger), model="test-sonnet"),
        qa=QAAgent(MeteredLLM(MockLLM(qa_responses), ledger), model="test-sonnet"),
        gate=gate or AutoApproveGate(),
        sandbox=LocalSandbox(),
        workspace_root=tmp_path / "workspaces",
        notifier=NullNotifier(),
        config=OrchestratorConfig(sandbox_timeout=120),
        test_python=sys.executable,
    )
    return orchestrator, store, ledger


def test_happy_path_end_to_end(tmp_path):
    orchestrator, store, ledger = make_orchestrator(
        tmp_path,
        analyst_responses=[PRD],
        dev_responses=[_dev_response(GOOD_CODE)],
        qa_responses=[QA_RESPONSE],
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed"
    assert [(t.ticket_id, t.status, t.attempts) for t in result.tickets] == [
        ("TCK-001", "passed", 1)
    ]
    assert result.first_try_validation_rate == 1.0
    assert result.total_cost_usd > 0

    # artifacts: PRD approved, ticket, code, and QA report all traced
    assert store.get("proj:PRD").status == ArtifactStatus.APPROVED
    assert store.get("proj:TCK-001").trace_ids == ("US-001",)
    assert store.get("proj:CODE-TCK-001").trace_ids == ("TCK-001", "US-001")
    assert set(store.get("proj:QA-TCK-001").trace_ids) == {"TCK-001", "AC-001"}

    # code landed in the workspace and cost is attributed per stage
    assert (result.workspace / "greeter.py").exists()
    assert set(ledger.summary("proj")) == {"intake", "build", "qa"}


def test_dev_failure_retries_with_bug_report(tmp_path):
    orchestrator, _, _ = make_orchestrator(
        tmp_path,
        analyst_responses=[PRD],
        dev_responses=[_dev_response(BROKEN_CODE), _dev_response(GOOD_CODE)],
        qa_responses=[QA_RESPONSE],
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed"
    assert result.tickets[0].attempts == 2
    # broken first try drags the first-try rate below 100%
    assert result.first_try_validation_rate == 1.0  # schema was valid both times

    # the retry prompt contained the failing test output
    dev_client = orchestrator.developer.llm.client
    assert "failed" in dev_client.calls[1][1].lower()


def test_retry_cap_escalates_to_human(tmp_path):
    orchestrator, store, _ = make_orchestrator(
        tmp_path,
        analyst_responses=[PRD],
        dev_responses=[_dev_response(BROKEN_CODE)] * 3,
        qa_responses=[],  # QA is never reached
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "escalated"
    assert result.tickets[0].status == "escalated"
    assert result.tickets[0].attempts == 3
    assert result.escalations
    # no code artifact was approved for the failed ticket
    with pytest.raises(Exception):
        store.get("proj:CODE-TCK-001")


def test_gate_rejection_routes_feedback_and_reversions(tmp_path):
    gate = ScriptedGate(
        [GateDecision(False, "split the story further"), GateDecision(True)]
    )
    orchestrator, store, _ = make_orchestrator(
        tmp_path,
        analyst_responses=[PRD, PRD],
        dev_responses=[_dev_response(GOOD_CODE)],
        qa_responses=[QA_RESPONSE],
        gate=gate,
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed"
    history = store.history("proj:PRD")
    assert [a.version for a in history] == [1, 2]
    assert history[0].status == ArtifactStatus.SUPERSEDED
    assert history[1].status == ArtifactStatus.APPROVED
    # the rejection reason reached the analyst
    analyst_client = orchestrator.analyst.llm.client
    assert "split the story further" in analyst_client.calls[1][1]


def test_gate_rejections_exhaust_retry_cap(tmp_path):
    gate = ScriptedGate([GateDecision(False, "no")] * 3)
    orchestrator, _, _ = make_orchestrator(
        tmp_path,
        analyst_responses=[PRD] * 3,
        dev_responses=[],
        qa_responses=[],
        gate=gate,
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "escalated"
    assert "not approved" in result.escalations[0]


def test_budget_hard_stop(tmp_path):
    ledger = CostLedger(stage_budgets={"intake": 0.0})
    orchestrator, _, _ = make_orchestrator(
        tmp_path,
        analyst_responses=[PRD],
        dev_responses=[],
        qa_responses=[],
        ledger=ledger,
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "budget_exceeded"


def test_d3_same_llm_instance_for_dev_and_qa_is_refused(tmp_path):
    ledger = CostLedger()
    shared = MeteredLLM(MockLLM(handler=lambda s, p: "{}"), ledger)
    with pytest.raises(ValueError, match="D3"):
        Orchestrator(
            store=ArtifactStore(),
            ledger=ledger,
            analyst=AnalystAgent(shared, model="m"),
            developer=DeveloperAgent(shared, model="m"),
            qa=QAAgent(shared, model="m"),
            gate=AutoApproveGate(),
            sandbox=LocalSandbox(),
            workspace_root=tmp_path,
        )


def test_invalid_analyst_output_counts_against_first_try_rate(tmp_path):
    orchestrator, _, _ = make_orchestrator(
        tmp_path,
        analyst_responses=["not json", PRD],
        dev_responses=[_dev_response(GOOD_CODE)],
        qa_responses=[QA_RESPONSE],
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "completed"
    # prd failed first try; code and qa passed first try -> 2/3
    assert result.first_try_validation_rate == pytest.approx(2 / 3)
