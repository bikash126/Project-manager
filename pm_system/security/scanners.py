"""Deterministic security scanners (design doc §4, §5).

Detection is done by deterministic tools — the design's principle is that
"Semgrep encodes the vulnerability expertise", not the model. The Security
*agent* only triages what these scanners find (see agents/security.py).

Scanners:
- RegexSecretScanner: built-in, no external dependency, always available.
  Catches obvious hardcoded credentials so secret detection works even when
  the sandbox image lacks the heavier tools.
- SemgrepScanner / GitleaksScanner / PipAuditScanner: wrap the free CLI tools
  (SAST / secrets / dependency audit). They run inside the sandbox and are
  skipped gracefully (no findings, a recorded note) when the binary is absent
  — bake them into sandbox/Dockerfile for real runs.

Findings are normalized to a common shape with a comparable Severity so the
orchestrator can apply a single blocking threshold (design decision D7:
verdicts are block/pass, never advisory).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from pm_system.sandbox.runner import Sandbox, SandboxResult


class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, raw: str, default: "Severity") -> "Severity":
        return _SEVERITY_ALIASES.get((raw or "").strip().lower(), default)


_SEVERITY_ALIASES = {
    "info": Severity.INFO,
    "note": Severity.INFO,
    "low": Severity.LOW,
    "warning": Severity.LOW,
    "moderate": Severity.MEDIUM,
    "medium": Severity.MEDIUM,
    "high": Severity.HIGH,
    "error": Severity.HIGH,
    "critical": Severity.CRITICAL,
}


@dataclass(frozen=True)
class Finding:
    scanner: str
    rule_id: str
    severity: Severity
    path: str
    line: int
    message: str

    def render(self, finding_id: str) -> str:
        return (
            f"{finding_id} [{self.severity.name}] {self.scanner}:{self.rule_id} "
            f"{self.path}:{self.line} — {self.message}"
        )


# Files/dirs never worth scanning.
_SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"}


def _iter_files(workdir: Path):
    for path in sorted(workdir.rglob("*")):
        if path.is_file() and not (set(path.relative_to(workdir).parts) & _SKIP_PARTS):
            yield path


class Scanner(ABC):
    name: str = "scanner"

    @abstractmethod
    def scan(self, workdir: Path, sandbox: Sandbox, timeout: int) -> list[Finding]:
        ...


class RegexSecretScanner(Scanner):
    """Built-in hardcoded-secret detector. Pure Python, no sandbox needed."""

    name = "regex-secrets"

    # (rule_id, compiled pattern, severity)
    PATTERNS = [
        ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}"), Severity.CRITICAL),
        (
            "private-key",
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
            Severity.CRITICAL,
        ),
        ("slack-token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"), Severity.HIGH),
        (
            "generic-secret-assignment",
            re.compile(
                r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)"
                r"\s*[=:]\s*['\"]([^'\"]{8,})['\"]"
            ),
            Severity.HIGH,
        ),
    ]

    # Substrings that mark a value as a placeholder rather than a real secret.
    PLACEHOLDERS = ("...", "xxx", "your", "example", "changeme", "placeholder", "dummy", "<", "{")

    TEXT_SUFFIXES = {
        ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".env", ".txt", ".md", ".sh", ".rb", ".go", ".java", ".tf", ".xml", ".html",
    }

    def scan(self, workdir: Path, sandbox: Sandbox, timeout: int) -> list[Finding]:
        findings: list[Finding] = []
        for path in _iter_files(workdir):
            if path.suffix and path.suffix not in self.TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            rel = str(path.relative_to(workdir))
            for lineno, line in enumerate(text.splitlines(), start=1):
                for rule_id, pattern, severity in self.PATTERNS:
                    match = pattern.search(line)
                    if not match:
                        continue
                    # Placeholder filtering only applies to the generic
                    # assignment rule (e.g. api_key = "your-key-here"); the
                    # structural patterns (AWS/private-key/slack) stand on their
                    # own — AWS's own example keys contain "EXAMPLE".
                    if rule_id == "generic-secret-assignment":
                        captured = match.groups()[-1].lower()
                        if any(marker in captured for marker in self.PLACEHOLDERS):
                            continue
                    findings.append(
                        Finding(
                            scanner=self.name,
                            rule_id=rule_id,
                            severity=severity,
                            path=rel,
                            line=lineno,
                            message=f"possible hardcoded secret ({rule_id})",
                        )
                    )
        return findings


class _CliScanner(Scanner):
    """Base for scanners that shell out to a CLI tool inside the sandbox."""

    def command(self) -> list[str]:
        raise NotImplementedError

    def parse(self, result: SandboxResult, workdir: Path) -> list[Finding]:
        raise NotImplementedError

    def scan(self, workdir: Path, sandbox: Sandbox, timeout: int) -> list[Finding]:
        result = sandbox.run(self.command(), workdir=workdir, timeout=timeout)
        if result.exit_code == 127:  # binary not installed — skip gracefully
            return []
        try:
            return self.parse(result, workdir)
        except (ValueError, KeyError, json.JSONDecodeError):
            return []


class SemgrepScanner(_CliScanner):
    name = "semgrep"

    def command(self) -> list[str]:
        return ["semgrep", "--json", "--quiet", "--config", "auto", "."]

    def parse(self, result: SandboxResult, workdir: Path) -> list[Finding]:
        data = json.loads(result.stdout or "{}")
        findings = []
        for item in data.get("results", []):
            extra = item.get("extra", {})
            findings.append(
                Finding(
                    scanner=self.name,
                    rule_id=item.get("check_id", "semgrep"),
                    severity=Severity.parse(extra.get("severity", ""), Severity.MEDIUM),
                    path=item.get("path", "?"),
                    line=item.get("start", {}).get("line", 0),
                    message=extra.get("message", "").strip() or "semgrep finding",
                )
            )
        return findings


class GitleaksScanner(_CliScanner):
    name = "gitleaks"

    def command(self) -> list[str]:
        return [
            "gitleaks", "detect", "--no-git", "--no-banner",
            "--report-format", "json", "--report-path", "/dev/stdout", "--source", ".",
        ]

    def parse(self, result: SandboxResult, workdir: Path) -> list[Finding]:
        data = json.loads(result.stdout or "[]")
        # Every gitleaks hit is a leaked secret — always high severity.
        return [
            Finding(
                scanner=self.name,
                rule_id=item.get("RuleID", "secret"),
                severity=Severity.CRITICAL,
                path=item.get("File", "?"),
                line=item.get("StartLine", 0),
                message=item.get("Description", "leaked secret"),
            )
            for item in data
        ]


class PipAuditScanner(_CliScanner):
    name = "pip-audit"

    def command(self) -> list[str]:
        return ["pip-audit", "--format", "json", "--progress-spinner", "off"]

    def parse(self, result: SandboxResult, workdir: Path) -> list[Finding]:
        data = json.loads(result.stdout or "{}")
        deps = data.get("dependencies", data) if isinstance(data, dict) else data
        findings = []
        for dep in deps:
            for vuln in dep.get("vulns", []):
                findings.append(
                    Finding(
                        scanner=self.name,
                        rule_id=vuln.get("id", "CVE"),
                        severity=Severity.HIGH,
                        path=f"{dep.get('name', '?')}=={dep.get('version', '?')}",
                        line=0,
                        message=(vuln.get("description") or "vulnerable dependency")[:200],
                    )
                )
        return findings


@dataclass
class ScanResult:
    findings: list[Finding]
    # scanner name -> finding count (0 = ran clean or was skipped)
    ran: dict[str, int]

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)


class SecurityScanSuite:
    """Runs a set of scanners and returns aggregated, stably-ordered findings."""

    def __init__(self, scanners: list[Scanner] | None = None):
        self.scanners = scanners if scanners is not None else default_scanners()

    def scan(self, workdir: Path, sandbox: Sandbox, timeout: int = 300) -> ScanResult:
        all_findings: list[Finding] = []
        ran: dict[str, int] = {}
        for scanner in self.scanners:
            found = scanner.scan(Path(workdir), sandbox, timeout)
            ran[scanner.name] = len(found)
            all_findings.extend(found)
        # Highest severity first, then a stable key for reproducible finding ids.
        all_findings.sort(key=lambda f: (-int(f.severity), f.scanner, f.path, f.line, f.rule_id))
        return ScanResult(findings=all_findings, ran=ran)


def default_scanners() -> list[Scanner]:
    """The Phase 3 security stack: SAST + secrets + dependency audit.

    The built-in secret scanner always runs; the CLI tools contribute when
    present in the sandbox image and skip cleanly otherwise.
    """
    return [
        RegexSecretScanner(),
        GitleaksScanner(),
        SemgrepScanner(),
        PipAuditScanner(),
    ]
