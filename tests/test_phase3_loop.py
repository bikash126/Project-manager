"""Phase 3 per-ticket loop: security (pre-review, blocking) and review gates."""

import json

from pm_system.security.scanners import Finding, Scanner, SecurityScanSuite, Severity
from tests.test_orchestrator import (
    GOOD_CODE,
    REVIEW_APPROVE,
    REVIEW_BLOCK,
    _dev_response,
    make_orchestrator,
)


class StubScanner(Scanner):
    """Returns fixed findings, so loop behavior can be tested without tools."""

    name = "stub"

    def __init__(self, findings):
        self._findings = findings

    def scan(self, workdir, sandbox, timeout):
        return list(self._findings)


def _finding(severity):
    return Finding(
        scanner="stub", rule_id="planted", severity=severity,
        path="greeter.py", line=1, message="planted finding",
    )


def _triage(status, reason=""):
    return json.dumps(
        {
            "triage": [{"finding_id": "FND-001", "status": status, "reason": reason}],
            "threat_model_notes": "n/a",
        }
    )


def test_confirmed_critical_finding_blocks_before_review(tmp_path):
    suite = SecurityScanSuite([StubScanner([_finding(Severity.CRITICAL)])])
    orchestrator, _, _ = make_orchestrator(
        tmp_path,
        dev_responses=[_dev_response(GOOD_CODE)] * 3,
        security_responses=[_triage("confirmed")] * 3,
        scan_suite=suite,
    )
    result = orchestrator.run_project("proj", "greeting library")

    ticket = result.tickets[0]
    assert result.status == "escalated"
    assert ticket.status == "escalated"
    assert ticket.security_blocks == 3
    assert result.security_findings_total >= 1
    # security caught it pre-review: the Reviewer was never invoked
    assert orchestrator.reviewer.llm.client.calls == []


def test_false_positive_lets_pr_through(tmp_path):
    suite = SecurityScanSuite([StubScanner([_finding(Severity.CRITICAL)])])
    orchestrator, store, _ = make_orchestrator(
        tmp_path,
        security_responses=[_triage("false_positive", "test fixture, not reachable")],
        scan_suite=suite,
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed"
    ticket = result.tickets[0]
    assert ticket.security_findings == 1
    assert ticket.security_blocks == 0
    assert result.pr_pass_rate == 1.0


def test_confirmed_finding_below_threshold_does_not_block(tmp_path):
    suite = SecurityScanSuite([StubScanner([_finding(Severity.LOW)])])
    orchestrator, _, _ = make_orchestrator(
        tmp_path,
        security_responses=[_triage("confirmed")],
        scan_suite=suite,
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "completed"  # LOW < blocking_severity (high)
    assert result.tickets[0].security_blocks == 0


def test_review_block_then_pass(tmp_path):
    orchestrator, store, _ = make_orchestrator(
        tmp_path,
        dev_responses=[_dev_response(GOOD_CODE)] * 3,
        reviewer_responses=[REVIEW_BLOCK, REVIEW_APPROVE],
    )
    result = orchestrator.run_project("proj", "greeting library")

    assert result.status == "completed"
    ticket = result.tickets[0]
    assert ticket.review_blocks == 1
    assert ticket.attempts == 2
    # the block's comment reached the developer as feedback on the retry
    assert "missing input validation" in orchestrator.developer.llm.client.calls[1][1]
    assert store.get("proj:REVIEW-TCK-001").content["verdict"] == "approve"


def test_review_block_exhausts_retries(tmp_path):
    orchestrator, _, _ = make_orchestrator(
        tmp_path,
        dev_responses=[_dev_response(GOOD_CODE)] * 3,
        reviewer_responses=[REVIEW_BLOCK] * 3,
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "escalated"
    assert result.tickets[0].review_blocks == 3
    assert result.pr_pass_rate == 0.0


def test_security_and_review_can_be_disabled(tmp_path):
    from pm_system.config import OrchestratorConfig

    orchestrator, _, ledger = make_orchestrator(
        tmp_path,
        config=OrchestratorConfig(
            sandbox_timeout=120, enable_security=False, enable_review=False
        ),
    )
    result = orchestrator.run_project("proj", "greeting library")
    assert result.status == "completed"
    # neither stage ran
    assert "security" not in ledger.summary("proj")
    assert "review" not in ledger.summary("proj")
