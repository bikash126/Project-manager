"""Toy project end-to-end: exit criteria for Phases 1–3."""

from examples.run_toy_project import main


def test_toy_project_end_to_end(tmp_path, capsys):
    assert main(["--workspace", str(tmp_path), "--gate", "auto", "--sandbox", "local"]) == 0
    out = capsys.readouterr().out
    assert "status: completed" in out
    assert "TCK-001 (US-001): passed" in out
    assert "TCK-002 (US-002): passed" in out
    assert "first-try validation rate: 100%" in out
    assert "PR pass rate (Review+QA within 3): 100%" in out
    assert "shipped: True (version 0.1.0)" in out
    # both tickets' code really landed
    assert (tmp_path / "toy-temp-converter" / "temp_converter" / "cli.py").exists()
    assert (tmp_path / "toy-temp-converter" / "tests" / "qa" / "test_tck002_acceptance.py").exists()
    # ship path produced CI, changelog, and docs
    assert (tmp_path / "toy-temp-converter" / ".github" / "workflows" / "ci.yml").exists()
    assert (tmp_path / "toy-temp-converter" / "CHANGELOG.md").exists()
    assert (tmp_path / "toy-temp-converter" / "README.md").exists()


def test_toy_project_change_request_reruns_blast_radius_only(tmp_path, capsys):
    assert main(
        ["--workspace", str(tmp_path), "--gate", "auto", "--sandbox", "local", "--change-request"]
    ) == 0
    out = capsys.readouterr().out
    assert "decision: accepted" in out
    assert "re-run tickets: ['TCK-001']" in out  # only the affected ticket
    assert "new version: 0.1.1" in out


def test_toy_project_security_gate_catches_planted_secret(tmp_path, capsys):
    assert main(
        ["--workspace", str(tmp_path), "--gate", "auto", "--sandbox", "local", "--inject-secret"]
    ) == 0
    out = capsys.readouterr().out
    assert "status: completed" in out
    # security caught the planted key and TCK-001 needed a second attempt
    assert "security findings: 1 (1 blocking)" in out
    assert "TCK-001 (US-001): passed in 2 attempt(s)" in out
    # the merged code does not contain the secret
    convert = (tmp_path / "toy-temp-converter" / "temp_converter" / "convert.py").read_text()
    assert "AKIA" not in convert
