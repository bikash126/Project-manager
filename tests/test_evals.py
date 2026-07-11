"""The eval harness itself must be trustworthy: it passes good outputs,
fails bad ones, and fingerprints the prompt for regression attribution."""

import json

from pm_system.agents.estimator import EstimatorAgent
from pm_system.agents.planner import PlannerAgent
from pm_system.agents.product_owner import ProductOwnerAgent
from pm_system.costs.ledger import CostLedger
from pm_system.evals.golden import GOLDEN_WBS, cases_for
from pm_system.evals.harness import EvalHarness, prompt_fingerprint
from pm_system.llm.client import MeteredLLM, MockLLM

GOOD_PLAN = json.dumps(
    {
        "sprints": [
            {"number": 1, "goal": "core flow", "wbs_ids": ["WBS-001", "WBS-002"]}
        ],
        "dependencies": [{"from": "WBS-002", "to": "WBS-001"}],
        "milestones": [{"name": "MVP", "sprint": 1}],
    }
)

# schedules only one item and misses the dependency insight
BAD_PLAN = json.dumps(
    {
        "sprints": [{"number": 1, "goal": "core flow", "wbs_ids": ["WBS-001"]}],
        "dependencies": [],
        "milestones": [],
    }
)


def _planner(responses):
    return PlannerAgent(MeteredLLM(MockLLM(responses), CostLedger()), model="test-model")


def test_harness_passes_good_output():
    report = EvalHarness(_planner([GOOD_PLAN])).run(cases_for("planner"))
    assert report.passed
    assert report.pass_rate == 1.0
    assert report.agent_role == "planner"


def test_harness_fails_bad_output_with_named_checks():
    report = EvalHarness(_planner([BAD_PLAN])).run(cases_for("planner"))
    assert not report.passed
    defects = report.results[0].defects
    assert "handoff" in defects
    assert "redirect-depends-on-shorten" in defects
    assert "FAIL" in report.summary()


def test_harness_records_schema_failures():
    report = EvalHarness(_planner(["not json"])).run(cases_for("planner"))
    assert not report.passed
    assert "schema" in report.results[0].defects


def test_prompt_fingerprint_tracks_playbook_changes():
    ledger = CostLedger()
    agent = _planner([GOOD_PLAN])
    before = prompt_fingerprint(agent)
    agent.playbook += "\n\nNew rule: always frontload risk."
    assert prompt_fingerprint(agent) != before


def test_all_phase2_roles_have_golden_suites():
    for role in ("product_owner", "estimator", "planner", "architect"):
        assert cases_for(role), role


def test_golden_wbs_is_internally_consistent():
    total = sum(i["estimate_points"] for i in GOLDEN_WBS["wbs_items"])
    assert GOLDEN_WBS["total_points"] == total


def test_estimator_suite_rejects_implausible_totals():
    inflated = json.dumps(
        {
            "wbs_items": [
                {
                    "id": "WBS-001",
                    "story_id": "US-001",
                    "description": "everything",
                    "estimate_points": 50,
                    "confidence": {"low": 40, "high": 60},
                },
                {
                    "id": "WBS-002",
                    "story_id": "US-002",
                    "description": "redirects",
                    "estimate_points": 50,
                    "confidence": {"low": 40, "high": 60},
                },
            ],
            "risk_register": [
                {
                    "id": "RISK-001",
                    "description": "d",
                    "likelihood": "low",
                    "impact": "low",
                    "mitigation": "m",
                }
            ],
            "total_points": 100,
        }
    )
    agent = EstimatorAgent(MeteredLLM(MockLLM([inflated]), CostLedger()), model="test-model")
    report = EvalHarness(agent).run(cases_for("estimator"))
    assert not report.passed
    assert "plausible-size" in report.results[0].defects


def test_product_owner_suite_rejects_mvp_everything():
    everything = json.dumps(
        {
            "mvp_story_ids": ["US-001", "US-002", "US-003"],
            "backlog": [
                {"story_id": f"US-00{i}", "priority": i, "rationale": "r"}
                for i in (1, 2, 3)
            ],
            "cut_list": [],
        }
    )
    agent = ProductOwnerAgent(MeteredLLM(MockLLM([everything]), CostLedger()), model="test-model")
    report = EvalHarness(agent).run(cases_for("product_owner"))
    assert not report.passed
    assert "something-was-scoped-out" in report.results[0].defects
