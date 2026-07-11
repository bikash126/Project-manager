"""Handoff verification for the security triage and review artifacts."""

from pm_system.orchestrator.validation import validate_review, validate_security_triage


def test_security_triage_valid():
    out = {
        "triage": [
            {"finding_id": "FND-001", "status": "confirmed", "reason": ""},
            {"finding_id": "FND-002", "status": "false_positive", "reason": "test fixture"},
        ],
        "threat_model_notes": "x",
    }
    assert validate_security_triage(out, {"FND-001", "FND-002"}) == []


def test_security_triage_defects():
    missing = {"triage": [{"finding_id": "FND-001", "status": "confirmed", "reason": ""}],
               "threat_model_notes": ""}
    assert any("missing" in d for d in validate_security_triage(missing, {"FND-001", "FND-002"}))

    unknown = {"triage": [{"finding_id": "FND-999", "status": "confirmed", "reason": ""}],
               "threat_model_notes": ""}
    assert any("unknown" in d for d in validate_security_triage(unknown, {"FND-001"}))

    fp_no_reason = {
        "triage": [{"finding_id": "FND-001", "status": "false_positive", "reason": "   "}],
        "threat_model_notes": "",
    }
    assert any("reason" in d for d in validate_security_triage(fp_no_reason, {"FND-001"}))

    dup = {
        "triage": [
            {"finding_id": "FND-001", "status": "confirmed", "reason": ""},
            {"finding_id": "FND-001", "status": "confirmed", "reason": ""},
        ],
        "threat_model_notes": "",
    }
    assert any("more than once" in d for d in validate_security_triage(dup, {"FND-001"}))


def test_review_valid_cases():
    assert validate_review({"verdict": "approve", "comments": []}) == []
    assert validate_review(
        {"verdict": "approve", "comments": [{"path": "a", "severity": "minor", "comment": "c"}]}
    ) == []
    assert validate_review(
        {"verdict": "block", "comments": [{"path": "a", "severity": "major", "comment": "c"}]}
    ) == []


def test_review_blocker_forces_block():
    out = {"verdict": "approve", "comments": [{"path": "a", "severity": "blocker", "comment": "c"}]}
    assert any("forces verdict=block" in d for d in validate_review(out))


def test_review_block_needs_a_major_or_blocker():
    out = {"verdict": "block", "comments": [{"path": "a", "severity": "minor", "comment": "c"}]}
    assert any("requires at least one major or blocker" in d for d in validate_review(out))
