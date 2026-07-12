"""Knowledge Base: storage, retrieval, and estimate calibration (P6 metric)."""

from pm_system.kb.store import (
    KIND_ADR,
    KIND_CALIBRATION,
    KIND_LESSON,
    KnowledgeBase,
)


def test_add_and_query_by_kind():
    kb = KnowledgeBase()
    kb.add(KIND_ADR, project_id="p1", content={"title": "use postgres"}, tags=["db"])
    kb.add(KIND_LESSON, project_id="p1", content={"lesson": "slice thinner"}, tags=["process"])
    assert len(kb.query(kind=KIND_ADR)) == 1
    assert kb.query(kind=KIND_ADR)[0].content["title"] == "use postgres"
    assert len(kb.query()) == 2


def test_query_by_tag_and_keyword():
    kb = KnowledgeBase()
    kb.add(KIND_ADR, project_id="p1", content={"title": "cache layer"}, tags=["perf"])
    kb.add(KIND_ADR, project_id="p2", content={"title": "auth flow"}, tags=["security"])
    assert len(kb.query(tags=["perf"])) == 1
    assert len(kb.query(keyword="auth")) == 1


def test_calibration_factor_and_summary():
    kb = KnowledgeBase()
    assert kb.calibration_factor() == 1.0  # no data
    for est, act in [(2, 3), (4, 6), (2, 3)]:  # consistently 1.5x over
        kb.add(KIND_CALIBRATION, project_id="p1",
               content={"estimated_points": est, "actual_points": act})
    assert kb.calibration_factor() == 1.5
    summary = kb.calibration_summary()
    assert summary["samples"] == 3
    assert summary["multiplier"] == 1.5


def test_apply_calibration():
    assert KnowledgeBase.apply_calibration(4, 1.5) == 6
    assert KnowledgeBase.apply_calibration(1, 1.0) == 1
    assert KnowledgeBase.apply_calibration(3, 0.1) == 1  # never below 1


def test_estimate_error_shrinks_with_calibration():
    """P6 exit metric: applying the KB's learned factor reduces estimate error.

    Ground truth: work runs 1.5x the raw estimate. After project 1's actuals
    land in the KB, project 2's estimate corrected by the factor is exact.
    """
    kb = KnowledgeBase()
    true_factor = 1.5

    # Project 1: estimate 4, no calibration yet -> error against actual (6).
    raw_estimate = 4
    actual_1 = round(raw_estimate * true_factor)
    uncorrected_error = abs(raw_estimate - actual_1)
    kb.add(KIND_CALIBRATION, project_id="p1",
           content={"estimated_points": raw_estimate, "actual_points": actual_1})

    # Project 2: same raw estimate, now corrected by the learned factor.
    corrected = KnowledgeBase.apply_calibration(raw_estimate, kb.calibration_factor())
    actual_2 = round(raw_estimate * true_factor)
    corrected_error = abs(corrected - actual_2)

    assert corrected_error < uncorrected_error
    assert corrected_error == 0


def test_reusable_adrs():
    kb = KnowledgeBase()
    kb.add(KIND_ADR, project_id="p1", content={"id": "ADR-001", "title": "x"})
    assert kb.reusable_adrs() == [{"id": "ADR-001", "title": "x"}]
