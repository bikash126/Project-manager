"""Handoff-verification checks for the Phase 2 planning artifacts."""

from pm_system.orchestrator.validation import (
    topological_order,
    validate_architecture,
    validate_backlog,
    validate_sprint_plan,
    validate_wbs,
)

STORY_IDS = {"US-001", "US-002", "US-003"}


def _backlog(mvp=("US-001", "US-002"), cut=("US-003",)):
    return {
        "mvp_story_ids": list(mvp),
        "backlog": [
            {"story_id": sid, "priority": i + 1, "rationale": "r"}
            for i, sid in enumerate(sorted(STORY_IDS))
        ],
        "cut_list": [{"story_id": sid, "reason": "later"} for sid in cut],
    }


def test_backlog_valid():
    assert validate_backlog(_backlog(), STORY_IDS) == []


def test_backlog_defects():
    out = _backlog(mvp=("US-001", "US-003"), cut=("US-003",))  # overlap
    assert any("both in MVP and cut" in d for d in validate_backlog(out, STORY_IDS))

    missing = _backlog()
    missing["backlog"] = missing["backlog"][:2]
    assert any("missing" in d for d in validate_backlog(missing, STORY_IDS))

    dup_priority = _backlog()
    for entry in dup_priority["backlog"]:
        entry["priority"] = 1
    assert any("unique" in d for d in validate_backlog(dup_priority, STORY_IDS))

    unknown = _backlog(mvp=("US-999",))
    assert any("unknown" in d for d in validate_backlog(unknown, STORY_IDS))


def _wbs():
    return {
        "wbs_items": [
            {
                "id": "WBS-001",
                "story_id": "US-001",
                "description": "d",
                "estimate_points": 3,
                "confidence": {"low": 2, "high": 5},
            },
            {
                "id": "WBS-002",
                "story_id": "US-002",
                "description": "d",
                "estimate_points": 2,
                "confidence": {"low": 1, "high": 3},
            },
        ],
        "risk_register": [],
        "total_points": 5,
    }


def test_wbs_valid():
    assert validate_wbs(_wbs(), {"US-001", "US-002"}) == []


def test_wbs_defects():
    assert any(
        "does not cover" in d for d in validate_wbs(_wbs(), {"US-001", "US-002", "US-003"})
    )
    assert any("outside the MVP" in d for d in validate_wbs(_wbs(), {"US-001"}))

    bad_total = _wbs()
    bad_total["total_points"] = 42
    assert any("total_points" in d for d in validate_wbs(bad_total, {"US-001", "US-002"}))

    out_of_range = _wbs()
    out_of_range["wbs_items"][0]["estimate_points"] = 9
    out_of_range["total_points"] = 11
    assert any("outside" in d for d in validate_wbs(out_of_range, {"US-001", "US-002"}))


def _plan(sprints=None, deps=None):
    return {
        "sprints": sprints
        or [{"number": 1, "goal": "g", "wbs_ids": ["WBS-001", "WBS-002"]}],
        "dependencies": deps or [],
        "milestones": [{"name": "m", "sprint": 1}],
    }


def test_sprint_plan_valid():
    assert validate_sprint_plan(_plan(), _wbs()["wbs_items"], capacity=10) == []


def test_sprint_plan_defects():
    assert any(
        "over capacity" in d
        for d in validate_sprint_plan(_plan(), _wbs()["wbs_items"], capacity=4)
    )

    missing = _plan(sprints=[{"number": 1, "goal": "g", "wbs_ids": ["WBS-001"]}])
    assert any(
        "does not schedule" in d
        for d in validate_sprint_plan(missing, _wbs()["wbs_items"], capacity=10)
    )

    backwards = _plan(
        sprints=[
            {"number": 1, "goal": "g", "wbs_ids": ["WBS-001"]},
            {"number": 2, "goal": "g", "wbs_ids": ["WBS-002"]},
        ],
        deps=[{"from": "WBS-001", "to": "WBS-002"}],  # sprint 1 depends on sprint 2
    )
    assert any(
        "scheduled later" in d
        for d in validate_sprint_plan(backwards, _wbs()["wbs_items"], capacity=10)
    )

    cycle = _plan(
        deps=[
            {"from": "WBS-001", "to": "WBS-002"},
            {"from": "WBS-002", "to": "WBS-001"},
        ]
    )
    assert any(
        "cycle" in d for d in validate_sprint_plan(cycle, _wbs()["wbs_items"], capacity=10)
    )

    bad_numbering = _plan(
        sprints=[{"number": 3, "goal": "g", "wbs_ids": ["WBS-001", "WBS-002"]}]
    )
    assert any(
        "consecutive" in d
        for d in validate_sprint_plan(bad_numbering, _wbs()["wbs_items"], capacity=10)
    )


def test_topological_order_respects_dependencies():
    order = topological_order(
        ["WBS-002", "WBS-001"], [{"from": "WBS-002", "to": "WBS-001"}]
    )
    assert order.index("WBS-001") < order.index("WBS-002")


def _arch():
    return {
        "overview": "o",
        "components": [
            {"name": "core", "responsibility": "r", "story_ids": ["US-001", "US-002"]}
        ],
        "adrs": [
            {
                "id": "ADR-001",
                "title": "t",
                "context": "c",
                "decision": "d",
                "alternatives": ["x"],
                "consequences": "q",
            }
        ],
    }


def test_architecture_valid():
    assert validate_architecture(_arch(), {"US-001", "US-002"}) == []


def test_architecture_coverage_defects():
    assert any(
        "does not cover" in d
        for d in validate_architecture(_arch(), {"US-001", "US-002", "US-003"})
    )
    assert any(
        "outside MVP" in d for d in validate_architecture(_arch(), {"US-001"})
    )
