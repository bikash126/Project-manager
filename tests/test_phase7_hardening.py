"""Phase 7 hardening: UX/UI design stage, parallel dev + merge-conflict
handling, observability dashboard, stakeholder digest, multi-project runs."""

import json
import sys

import pytest

from pm_system import (
    AnalystAgent,
    ArchitectAgent,
    ArtifactStatus,
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
    ProductOwnerAgent,
    ProjectJob,
    QAAgent,
    RegexSecretScanner,
    ReleaseManagerAgent,
    RetrospectiveAgent,
    ReviewerAgent,
    SecurityAgent,
    SecurityScanSuite,
    UIAgent,
    UXAgent,
    render_dashboard,
    run_projects,
    stakeholder_digest,
)
from pm_system.notify.notifier import NullNotifier
from pm_system.orchestrator.validation import validate_ui, validate_ux
from pm_system.sandbox.runner import LocalSandbox

# --- two INDEPENDENT stories (no dependency) so both tickets share a wave ---

PRD2 = json.dumps({
    "title": "Two Utils",
    "summary": "Two independent helpers.",
    "user_stories": [
        {"id": "US-001", "story": "As a user, I can get one.",
         "acceptance_criteria": [{"id": "AC-001", "given": "call", "when": "one", "then": "1"}]},
        {"id": "US-002", "story": "As a user, I can get two.",
         "acceptance_criteria": [{"id": "AC-002", "given": "call", "when": "two", "then": "2"}]},
    ],
})
BACKLOG2 = json.dumps({
    "mvp_story_ids": ["US-001", "US-002"],
    "backlog": [{"story_id": "US-001", "priority": 1, "rationale": "r"},
                {"story_id": "US-002", "priority": 2, "rationale": "r"}],
    "cut_list": [],
})
WBS2 = json.dumps({
    "wbs_items": [
        {"id": "WBS-001", "story_id": "US-001", "description": "one()",
         "estimate_points": 2, "confidence": {"low": 1, "high": 3}},
        {"id": "WBS-002", "story_id": "US-002", "description": "two()",
         "estimate_points": 2, "confidence": {"low": 1, "high": 3}},
    ],
    "risk_register": [], "total_points": 4,
})
PLAN2 = json.dumps({  # no dependencies -> both in wave 0
    "sprints": [{"number": 1, "goal": "ship utils", "wbs_ids": ["WBS-001", "WBS-002"]}],
    "dependencies": [], "milestones": [{"name": "MVP", "sprint": 1}],
})
ARCH2 = json.dumps({
    "overview": "two functions",
    "components": [
        {"name": "one", "responsibility": "returns one", "story_ids": ["US-001"]},
        {"name": "two", "responsibility": "returns two", "story_ids": ["US-002"]},
    ],
    "adrs": [{"id": "ADR-001", "title": "pure functions", "context": "c", "decision": "d",
              "alternatives": ["classes"], "consequences": "simple"}],
    "api_contracts": [], "data_model": [],
})
REVIEW_APPROVE = json.dumps({"verdict": "approve", "comments": []})
DEVOPS_R = json.dumps({"pipeline_files": [{"path": ".github/workflows/ci.yml", "content": "ci"}],
                       "iac_files": [], "environments": ["prod"]})
RELEASE_R = json.dumps({"version": "0.1.0", "changelog": [{"type": "added", "description": "utils"}],
                        "deploy_plan": "ship", "rollback_plan": "revert"})
DOCS_R = json.dumps({"docs": [{"path": "README.md", "content": "# Two Utils\n"}]})


def _dev_independent(system, prompt):
    if "ticket TCK-002" in prompt:
        return json.dumps({"ticket_id": "TCK-002", "files": [
            {"path": "mod_two.py", "content": "def two():\n    return 2\n"},
            {"path": "tests/test_two.py", "content": "from mod_two import two\n\n\ndef test_two():\n    assert two() == 2\n"},
        ]})
    return json.dumps({"ticket_id": "TCK-001", "files": [
        {"path": "mod_one.py", "content": "def one():\n    return 1\n"},
        {"path": "tests/test_one.py", "content": "from mod_one import one\n\n\ndef test_one():\n    assert one() == 1\n"},
    ]})


def _qa_independent(system, prompt):
    if "ticket TCK-002" in prompt:
        return json.dumps({"ticket_id": "TCK-002",
                           "test_plan": [{"ac_id": "AC-002", "description": "two is 2"}],
                           "test_files": [{"path": "tests/qa/test_two_acc.py",
                                           "content": "from mod_two import two\n\n\ndef test_ac_002():\n    assert two() == 2\n"}]})
    return json.dumps({"ticket_id": "TCK-001",
                       "test_plan": [{"ac_id": "AC-001", "description": "one is 1"}],
                       "test_files": [{"path": "tests/qa/test_one_acc.py",
                                       "content": "from mod_one import one\n\n\ndef test_ac_001():\n    assert one() == 1\n"}]})


def build(tmp_path, *, dev_handler=None, qa_handler=None, config=None, store=None,
          ledger=None, ux_ui=False):
    store = store or ArtifactStore()
    ledger = ledger or CostLedger()

    def q(responses):
        return MeteredLLM(MockLLM(responses), ledger)

    def h(fn):
        return MeteredLLM(MockLLM(handler=fn), ledger)

    ux = ui = None
    if ux_ui:
        ux = UXAgent(h(lambda s, p: json.dumps({
            "flows": [{"id": "FLOW-001", "name": "get one", "story_ids": ["US-001"], "steps": ["call one"]},
                      {"id": "FLOW-002", "name": "get two", "story_ids": ["US-002"], "steps": ["call two"]}],
            "information_architecture": ["home"]})), model="m")
        ui = UIAgent(h(lambda s, p: json.dumps({
            "components": [{"name": "Button", "description": "b", "used_in": ["FLOW-001", "FLOW-002"]}],
            "design_tokens": {"colors": {"primary": "#111"}}})), model="m")

    return Orchestrator(
        store=store, ledger=ledger,
        analyst=AnalystAgent(q([PRD2]), model="m"),
        product_owner=ProductOwnerAgent(q([BACKLOG2]), model="m"),
        estimator=EstimatorAgent(q([WBS2]), model="m"),
        planner=PlannerAgent(q([PLAN2]), model="m"),
        architect=ArchitectAgent(q([ARCH2]), model="m"),
        developer=DeveloperAgent(h(dev_handler or _dev_independent), model="m"),
        reviewer=ReviewerAgent(h(lambda s, p: REVIEW_APPROVE), model="m"),
        security=SecurityAgent(q([]), model="m"),
        qa=QAAgent(h(qa_handler or _qa_independent), model="m"),
        data_engineer=DataEngineerAgent(q([]), model="m"),
        devops=DevOpsAgent(q([DEVOPS_R]), model="m"),
        release_manager=ReleaseManagerAgent(q([RELEASE_R]), model="m"),
        doc_writer=DocWriterAgent(q([DOCS_R]), model="m"),
        ux=ux, ui=ui,
        scan_suite=SecurityScanSuite([RegexSecretScanner()]),
        gate=AutoApproveGate(),
        sandbox=LocalSandbox(),
        workspace_root=tmp_path / "ws",
        notifier=NullNotifier(),
        config=config or OrchestratorConfig(sandbox_timeout=120),
        test_python=sys.executable,
    ), store, ledger


# ----------------------------- parallel dev -----------------------------

def test_dependency_waves_separate_dependent_tickets(tmp_path):
    orch, store, _ = build(tmp_path)
    store.put("p:PLAN", artifact_type="sprint_plan",
              content={"dependencies": [{"from": "WBS-002", "to": "WBS-001"}]},
              created_by="t", project_id="p")
    tickets = [{"ticket_id": "TCK-001", "wbs_id": "WBS-001"},
               {"ticket_id": "TCK-002", "wbs_id": "WBS-002"}]
    waves = orch._dependency_waves("p", tickets)
    assert [[t["wbs_id"] for t in w] for w in waves] == [["WBS-001"], ["WBS-002"]]


def test_independent_tickets_share_one_wave(tmp_path):
    orch, store, _ = build(tmp_path)
    store.put("p:PLAN", artifact_type="sprint_plan", content={"dependencies": []},
              created_by="t", project_id="p")
    tickets = [{"ticket_id": "TCK-001", "wbs_id": "WBS-001"},
               {"ticket_id": "TCK-002", "wbs_id": "WBS-002"}]
    waves = orch._dependency_waves("p", tickets)
    assert len(waves) == 1 and len(waves[0]) == 2


def test_parallel_build_ships(tmp_path):
    orch, store, _ = build(
        tmp_path, config=OrchestratorConfig(sandbox_timeout=120, parallel_tickets=True)
    )
    result = orch.run_project("p", "two utils")
    assert result.status == "completed" and result.shipped
    assert [t.status for t in result.tickets] == ["passed", "passed"]
    # both modules landed in main
    assert (result.workspace / "mod_one.py").exists()
    assert (result.workspace / "mod_two.py").exists()
    assert all(pr.state == "merged" for pr in orch.pr_publisher.prs)


# ------------------------- merge conflict handling ----------------------

def _dev_conflicting(system, prompt):
    if "Resolve the git merge conflicts" in prompt:
        # merged shared.py keeping both values
        return json.dumps({"ticket_id": "TCK-002", "files": [
            {"path": "shared.py", "content": "OWNER = 'both'\n"},
        ]})
    if "ticket TCK-002" in prompt:
        return json.dumps({"ticket_id": "TCK-002", "files": [
            {"path": "shared.py", "content": "OWNER = 'two'\n"},
            {"path": "mod_two.py", "content": "def two():\n    return 2\n"},
            {"path": "tests/test_two.py", "content": "from mod_two import two\n\n\ndef test_two():\n    assert two() == 2\n"},
        ]})
    return json.dumps({"ticket_id": "TCK-001", "files": [
        {"path": "shared.py", "content": "OWNER = 'one'\n"},
        {"path": "mod_one.py", "content": "def one():\n    return 1\n"},
        {"path": "tests/test_one.py", "content": "from mod_one import one\n\n\ndef test_one():\n    assert one() == 1\n"},
    ]})


def test_parallel_merge_conflict_is_resolved(tmp_path):
    orch, store, _ = build(
        tmp_path, dev_handler=_dev_conflicting,
        config=OrchestratorConfig(sandbox_timeout=120, parallel_tickets=True),
    )
    result = orch.run_project("p", "two utils with a shared file")
    # both tickets landed despite both writing shared.py (add/add conflict resolved)
    assert [t.status for t in result.tickets] == ["passed", "passed"]
    assert result.shipped
    assert (result.workspace / "shared.py").read_text() == "OWNER = 'both'\n"
    # the resolver invoked the developer with the conflict
    dev_prompts = [p for _, p in orch.developer.llm.client.calls]
    assert any("Resolve the git merge conflicts" in p for p in dev_prompts)


# ------------------------------ design stage ----------------------------

def test_design_stage_produces_specs_and_feeds_tickets(tmp_path):
    orch, store, _ = build(
        tmp_path, ux_ui=True,
        config=OrchestratorConfig(sandbox_timeout=120, enable_design=True),
    )
    result = orch.run_project("p", "two utils")
    assert result.shipped
    assert store.get("p:UX").status == ArtifactStatus.APPROVED
    assert store.get("p:UI").status == ArtifactStatus.APPROVED
    # the ticket for US-001 carried the UI component whose flow covers US-001
    ticket = store.get("p:TCK-001").content
    assert ticket["design"]["components"][0]["name"] == "Button"


def test_ux_ui_validation():
    ux = {"flows": [{"id": "FLOW-001", "name": "f", "story_ids": ["US-001"], "steps": ["s"]}],
          "information_architecture": ["home"]}
    assert validate_ux(ux, {"US-001"}) == []
    assert any("no flow covers" in d for d in validate_ux(ux, {"US-001", "US-002"}))

    ui = {"components": [{"name": "B", "description": "d", "used_in": ["FLOW-001"]}],
          "design_tokens": {"colors": {"p": "#111"}}}
    assert validate_ui(ui, {"FLOW-001"}) == []
    assert any("unknown flows" in d for d in validate_ui(ui, {"FLOW-999"}))
    no_colors = {"components": ui["components"], "design_tokens": {"colors": {}}}
    assert any("color" in d for d in validate_ui(no_colors, {"FLOW-001"}))


# --------------------------- observability ------------------------------

def test_dashboard_renders_html(tmp_path):
    orch, store, ledger = build(tmp_path)
    result = orch.run_project("p", "two utils")
    html = render_dashboard(result, ledger, store)
    assert "<html" in html and "project dashboard" in html
    assert "PR pass rate" in html
    assert "p:CODE-TCK-001" in html  # artifact table


def test_stakeholder_digest_is_markdown(tmp_path):
    orch, store, _ = build(tmp_path)
    result = orch.run_project("p", "two utils")
    digest = stakeholder_digest(result, store)
    assert digest.startswith("# p — status digest")
    assert "Shipped" in digest
    assert "US-001" in digest  # scope section


def test_emit_stakeholder_digest_notifies(tmp_path):
    class Capture(NullNotifier):
        def __init__(self):
            self.msgs = []

        def notify(self, project_id, stage, message):
            self.msgs.append((stage, message))

    orch, _, _ = build(
        tmp_path, config=OrchestratorConfig(sandbox_timeout=120, emit_stakeholder_digest=True)
    )
    orch.notifier = Capture()
    orch.run_project("p", "two utils")
    assert any(stage == "digest" for stage, _ in orch.notifier.msgs)


# ----------------------- multi-project concurrency ----------------------

def test_run_projects_concurrently(tmp_path):
    store = ArtifactStore()
    ledger = CostLedger()
    # two projects, each its own orchestrator, sharing the thread-safe store
    orch_a, _, _ = build(tmp_path / "a", store=store, ledger=ledger)
    orch_b, _, _ = build(tmp_path / "b", store=store, ledger=ledger)
    results = run_projects([
        ProjectJob(orch_a, "proj-a", "utils a"),
        ProjectJob(orch_b, "proj-b", "utils b"),
    ])
    assert set(results) == {"proj-a", "proj-b"}
    assert all(r.shipped for r in results.values())
    # artifacts are isolated by project id
    assert store.get("proj-a:PRD").project_id == "proj-a"
    assert store.get("proj-b:PRD").project_id == "proj-b"
