"""Phase 1 exit criterion: the toy project completes end-to-end."""

from examples.run_toy_project import main


def test_toy_project_end_to_end(tmp_path, capsys):
    assert main(["--workspace", str(tmp_path), "--gate", "auto", "--sandbox", "local"]) == 0
    out = capsys.readouterr().out
    assert "status: completed" in out
    assert "TCK-001 (US-001): passed" in out
    assert "TCK-002 (US-002): passed" in out
    assert "first-try validation rate: 100%" in out
    # both tickets' code really landed
    assert (tmp_path / "toy-temp-converter" / "temp_converter" / "cli.py").exists()
    assert (tmp_path / "toy-temp-converter" / "tests" / "qa" / "test_tck002_acceptance.py").exists()
