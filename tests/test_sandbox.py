import sys

from pm_system.sandbox.runner import LocalSandbox, TestRunner


def test_local_sandbox_runs_commands(tmp_path):
    result = LocalSandbox().run(
        [sys.executable, "-c", "print('hello')"], workdir=tmp_path, timeout=30
    )
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert not result.timed_out


def test_local_sandbox_timeout(tmp_path):
    result = LocalSandbox().run(
        [sys.executable, "-c", "import time; time.sleep(5)"], workdir=tmp_path, timeout=1
    )
    assert result.timed_out
    assert result.exit_code == 124


def test_test_runner_pass_and_fail(tmp_path):
    runner = TestRunner(LocalSandbox(), python=sys.executable, timeout=60)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    report = runner.run(tmp_path)
    assert report.passed

    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    report = runner.run(tmp_path)
    assert not report.passed
    assert "test_bad" in report.summary
