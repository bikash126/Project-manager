"""Phase 4 ship path: integration + license check, DevOps, Gate 3, release, docs."""

import json

from pm_system.artifacts.store import ArtifactStatus
from pm_system.config import OrchestratorConfig
from pm_system.gates.gate import GateDecision
from pm_system.security.licenses import LicenseChecker
from tests.test_orchestrator import (
    ARCH,
    GOOD_CODE,
    ScriptedGate,
    _dev_response,
    make_orchestrator,
)

SHIP = OrchestratorConfig(sandbox_timeout=120, enable_ship=True)


def test_project_ships_end_to_end(tmp_path):
    orchestrator, store, ledger = make_orchestrator(tmp_path, config=SHIP)
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed"
    assert result.shipped is True
    assert result.release_version == "0.1.0"
    # 3 gates approved, no escalations
    assert result.human_interventions == 3

    # ship artifacts recorded and approved
    for suffix in ("INTEGRATION", "DEVOPS", "RELEASE", "DOCS"):
        assert store.get(f"proj:{suffix}").status == ArtifactStatus.APPROVED

    # files landed in the workspace
    assert (result.workspace / ".github" / "workflows" / "ci.yml").exists()
    assert (result.workspace / "README.md").read_text().startswith("# Greeter")
    assert "0.1.0" in (result.workspace / "CHANGELOG.md").read_text()

    # the release manager (only it) triggered a deploy
    assert len(orchestrator.deployer.deployments) == 1
    assert orchestrator.deployer.deployments[0].version == "0.1.0"

    # ship stages are cost-attributed
    assert {"devops", "release", "docs"} <= set(ledger.summary("proj"))


def test_escalated_ticket_skips_ship(tmp_path):
    from tests.test_orchestrator import BROKEN_CODE

    orchestrator, _, _ = make_orchestrator(
        tmp_path, dev_responses=[_dev_response(BROKEN_CODE)] * 3, qa_responses=[], config=SHIP
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "escalated"
    assert result.shipped is False
    assert orchestrator.deployer.deployments == []


def test_gate3_rejection_then_approve(tmp_path):
    gate = ScriptedGate(
        [
            GateDecision(True),  # gate 1
            GateDecision(True),  # gate 2
            GateDecision(False, "add a rollback runbook link"),  # gate 3 round 1
            GateDecision(True),  # gate 3 round 2
        ]
    )
    from tests.test_orchestrator import RELEASE_R

    orchestrator, store, _ = make_orchestrator(
        tmp_path, gate=gate, release_responses=[RELEASE_R, RELEASE_R], config=SHIP
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed" and result.shipped
    # release re-run after the rejection
    assert [a.version for a in store.history("proj:RELEASE")] == [1, 2]
    rm_client = orchestrator.release_manager.llm.client
    assert "rollback runbook" in rm_client.calls[1][1]


def test_gate3_rejections_escalate(tmp_path):
    gate = ScriptedGate(
        [GateDecision(True), GateDecision(True)] + [GateDecision(False, "no")] * 3
    )
    from tests.test_orchestrator import RELEASE_R

    orchestrator, _, _ = make_orchestrator(
        tmp_path, gate=gate, release_responses=[RELEASE_R] * 3, config=SHIP
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "escalated"
    assert result.shipped is False


def test_license_check_blocks_release(tmp_path):
    # A developer that also drops a copyleft dependency into requirements.txt.
    dev = json.dumps(
        {
            "ticket_id": "TCK-001",
            "files": [
                {"path": "greeter.py", "content": GOOD_CODE},
                {"path": "tests/test_greeter.py", "content":
                    "from greeter import greet\n\n\ndef test_g():\n    assert greet('World') == 'Hello, World!'\n"},
                {"path": "requirements.txt", "content": "evilpkg==1.0\n"},
            ],
        }
    )
    checker = LicenseChecker(licenses={"evilpkg": "AGPL-3.0"})
    orchestrator, _, _ = make_orchestrator(
        tmp_path, dev_responses=[dev], config=SHIP
    )
    orchestrator.license_checker = checker
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "escalated"
    assert result.shipped is False
    assert any("license check blocked" in e for e in result.escalations)


def test_data_engineer_runs_when_data_model_present(tmp_path):
    arch_with_data = json.loads(ARCH)
    arch_with_data["data_model"] = [
        {"entity": "Greeting", "fields": [{"name": "id", "type": "int"}]}
    ]
    from tests.test_orchestrator import DATA_R

    orchestrator, store, _ = make_orchestrator(
        tmp_path,
        architect_responses=[json.dumps(arch_with_data)],
        data_responses=[DATA_R],
        config=SHIP,
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.shipped
    assert store.get("proj:MIGRATIONS").status == ArtifactStatus.APPROVED
    assert (result.workspace / "migrations" / "MIG-001.sql").exists()
