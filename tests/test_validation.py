import pytest

from pm_system.errors import UnsafePathError
from pm_system.orchestrator.validation import (
    is_safe_relative_path,
    safe_write,
    validate_dev_output,
    validate_prd,
    validate_qa_output,
)


def test_validate_prd_flags_duplicate_ids():
    prd = {
        "user_stories": [
            {"id": "US-001", "acceptance_criteria": [{"id": "AC-001"}]},
            {"id": "US-001", "acceptance_criteria": [{"id": "AC-001"}]},
        ]
    }
    defects = validate_prd(prd)
    assert any("US-001" in d for d in defects)
    assert any("AC-001" in d for d in defects)


def test_validate_dev_output():
    good = {
        "ticket_id": "TCK-001",
        "files": [{"path": "pkg/mod.py"}, {"path": "tests/test_mod.py"}],
    }
    assert validate_dev_output(good, "TCK-001") == []

    wrong_ticket = validate_dev_output(good, "TCK-002")
    assert any("traceability" in d for d in wrong_ticket)

    no_tests = {"ticket_id": "TCK-001", "files": [{"path": "pkg/mod.py"}]}
    assert any("test" in d for d in validate_dev_output(no_tests, "TCK-001"))

    escape = {"ticket_id": "TCK-001", "files": [{"path": "../evil.py"}, {"path": "tests/test_x.py"}]}
    assert any("unsafe" in d for d in validate_dev_output(escape, "TCK-001"))


def test_validate_qa_output_coverage():
    output = {
        "ticket_id": "TCK-001",
        "test_plan": [{"ac_id": "AC-001", "description": "x"}],
        "test_files": [{"path": "tests/qa/test_a.py"}],
    }
    assert validate_qa_output(output, "TCK-001", {"AC-001"}) == []
    defects = validate_qa_output(output, "TCK-001", {"AC-001", "AC-002"})
    assert any("AC-002" in d for d in defects)

    outside_tests = {
        "ticket_id": "TCK-001",
        "test_plan": [{"ac_id": "AC-001", "description": "x"}],
        "test_files": [{"path": "pkg/test_a.py"}],
    }
    assert any("tests/" in d for d in validate_qa_output(outside_tests, "TCK-001", {"AC-001"}))


@pytest.mark.parametrize(
    "path,ok",
    [
        ("pkg/mod.py", True),
        ("tests/qa/test_x.py", True),
        ("/etc/passwd", False),
        ("../escape.py", False),
        ("a/../../escape.py", False),
        ("c:\\windows\\evil", False),
        ("", False),
    ],
)
def test_is_safe_relative_path(path, ok):
    assert is_safe_relative_path(path) is ok


def test_safe_write(tmp_path):
    target = safe_write(tmp_path, "a/b/file.txt", "hi")
    assert target.read_text() == "hi"
    with pytest.raises(UnsafePathError):
        safe_write(tmp_path, "../outside.txt", "nope")
