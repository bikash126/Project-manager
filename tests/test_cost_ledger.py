import pytest

from pm_system.costs.ledger import CostLedger
from pm_system.errors import BudgetExceededError
from pm_system.llm.client import CostTags, MeteredLLM, MockLLM, price_usd


def test_record_and_summaries():
    ledger = CostLedger()
    ledger.record(
        project_id="p1", stage="build", agent="developer", model="m",
        input_tokens=100, output_tokens=50, cost_usd=0.5, ticket_id="TCK-001",
    )
    ledger.record(
        project_id="p1", stage="qa", agent="qa", model="m",
        input_tokens=10, output_tokens=5, cost_usd=0.25,
    )
    assert ledger.stage_spend("p1", "build") == 0.5
    assert ledger.project_spend("p1") == 0.75
    assert ledger.summary("p1") == {"build": 0.5, "qa": 0.25}
    assert len(ledger.entries("p1")) == 2


def test_stage_budget_hard_stop():
    ledger = CostLedger(stage_budgets={"build": 0.4})
    ledger.check_budget("p1", "build")  # nothing spent yet
    ledger.record(
        project_id="p1", stage="build", agent="developer", model="m",
        input_tokens=1, output_tokens=1, cost_usd=0.4,
    )
    with pytest.raises(BudgetExceededError):
        ledger.check_budget("p1", "build")
    ledger.check_budget("p1", "qa")  # other stages unaffected


def test_project_budget_hard_stop():
    ledger = CostLedger(project_budget=0.1)
    ledger.record(
        project_id="p1", stage="build", agent="developer", model="m",
        input_tokens=1, output_tokens=1, cost_usd=0.1,
    )
    with pytest.raises(BudgetExceededError):
        ledger.check_budget("p1", "anything")


def test_metered_llm_records_every_call():
    ledger = CostLedger()
    llm = MeteredLLM(MockLLM(["hello"]), ledger)
    llm.complete(
        system="s", prompt="p", model="test-sonnet-model",
        tags=CostTags(project_id="p1", stage="build", agent="developer", ticket_id="TCK-001"),
    )
    entries = ledger.entries("p1")
    assert len(entries) == 1
    assert entries[0].agent == "developer"
    assert entries[0].ticket_id == "TCK-001"
    assert entries[0].cost_usd > 0


def test_pricing_matches_model_family():
    assert price_usd("some-sonnet-model", 1_000_000, 0) == 3.0
    assert price_usd("some-haiku-model", 0, 1_000_000) == 5.0
    assert price_usd("unknown-model", 1_000_000, 0) == 5.0
