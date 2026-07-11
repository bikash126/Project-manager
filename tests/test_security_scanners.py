"""Deterministic security scanners: the built-in secret scanner and the
CLI-scanner parsers (tested offline with sample tool output)."""

from pathlib import Path

from pm_system.sandbox.runner import LocalSandbox, SandboxResult
from pm_system.security.scanners import (
    GitleaksScanner,
    PipAuditScanner,
    RegexSecretScanner,
    SecurityScanSuite,
    SemgrepScanner,
    Severity,
)


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_regex_scanner_flags_aws_key(tmp_path):
    _write(tmp_path, "config.py", 'KEY = "AKIAIOSFODNN7EXAMPLE1"\n')
    findings = RegexSecretScanner().scan(tmp_path, LocalSandbox(), 30)
    assert any(f.rule_id == "aws-access-key" and f.severity == Severity.CRITICAL for f in findings)


def test_regex_scanner_flags_private_key_and_assignment(tmp_path):
    _write(tmp_path, "id_rsa", "-----BEGIN RSA PRIVATE KEY-----\nabc\n")
    _write(tmp_path, "settings.py", 'password = "hunter2supersecret"\n')
    findings = RegexSecretScanner().scan(tmp_path, LocalSandbox(), 30)
    rules = {f.rule_id for f in findings}
    assert "private-key" in rules
    assert "generic-secret-assignment" in rules


def test_regex_scanner_ignores_placeholders(tmp_path):
    _write(tmp_path, "example.py", 'api_key = "your-api-key-here"\ntoken = "xxxxxxxxxxxx"\n')
    findings = RegexSecretScanner().scan(tmp_path, LocalSandbox(), 30)
    assert findings == []


def test_regex_scanner_clean_code(tmp_path):
    _write(tmp_path, "greeter.py", 'def greet(name):\n    return f"Hello, {name}!"\n')
    assert RegexSecretScanner().scan(tmp_path, LocalSandbox(), 30) == []


def test_regex_scanner_skips_git_dir(tmp_path):
    _write(tmp_path, ".git/config", 'password = "realsecretvalue123"\n')
    assert RegexSecretScanner().scan(tmp_path, LocalSandbox(), 30) == []


def test_semgrep_parser():
    sample = (
        '{"results": [{"check_id": "python.lang.security.audit.dangerous-eval",'
        ' "path": "app.py", "start": {"line": 12},'
        ' "extra": {"severity": "ERROR", "message": "avoid eval"}}]}'
    )
    findings = SemgrepScanner().parse(SandboxResult(0, sample, "", 0.1), Path("."))
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH
    assert findings[0].path == "app.py"
    assert findings[0].line == 12


def test_gitleaks_parser_marks_secrets_critical():
    sample = '[{"RuleID": "generic-api-key", "File": "cfg.py", "StartLine": 3, "Description": "leaked key"}]'
    findings = GitleaksScanner().parse(SandboxResult(1, sample, "", 0.1), Path("."))
    assert findings[0].severity == Severity.CRITICAL
    assert findings[0].path == "cfg.py"


def test_pip_audit_parser():
    sample = (
        '{"dependencies": [{"name": "requests", "version": "2.0.0",'
        ' "vulns": [{"id": "CVE-2021-1", "description": "bad"}]}]}'
    )
    findings = PipAuditScanner().parse(SandboxResult(1, sample, "", 0.1), Path("."))
    assert findings[0].rule_id == "CVE-2021-1"
    assert findings[0].severity == Severity.HIGH
    assert "requests==2.0.0" == findings[0].path


def test_cli_scanner_skips_when_binary_missing(tmp_path):
    # semgrep/gitleaks/pip-audit aren't installed here -> exit 127 -> no findings
    findings = SemgrepScanner().scan(tmp_path, LocalSandbox(), 30)
    assert findings == []


def test_scan_suite_orders_by_severity(tmp_path):
    _write(tmp_path, "a.py", 'token = "xoxb-1234567890abcd"\n')  # HIGH
    _write(tmp_path, "b.py", 'KEY = "AKIAIOSFODNN7EXAMPLE1"\n')  # CRITICAL
    suite = SecurityScanSuite([RegexSecretScanner()])
    result = suite.scan(tmp_path, LocalSandbox())
    assert result.has_findings
    assert result.findings[0].severity == Severity.CRITICAL  # highest first
    assert result.ran["regex-secrets"] == len(result.findings)


def test_severity_ordering_and_parse():
    assert Severity.CRITICAL > Severity.HIGH > Severity.MEDIUM > Severity.LOW
    assert Severity.parse("ERROR", Severity.LOW) == Severity.HIGH
    assert Severity.parse("unknown", Severity.MEDIUM) == Severity.MEDIUM
