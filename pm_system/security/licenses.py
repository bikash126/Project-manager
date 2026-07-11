"""Dependency license compliance check (design doc §4, integration stage).

In production this wraps ScanCode; offline it parses the workspace's declared
dependencies and checks each against a license policy. Unknown licenses are
non-blocking warnings; denylisted (strong-copyleft) licenses block the release.

The package -> license map is injectable (ScanCode provides it for real runs);
the default map is small, so a greenfield stdlib-only project checks clean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Permissive licenses that pass; strong-copyleft that block a proprietary ship.
DEFAULT_ALLOW = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "PSF", "MPL-2.0"}
DEFAULT_DENY = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-3.0", "SSPL-1.0"}

# Minimal built-in package -> license knowledge (extend or supply via ScanCode).
DEFAULT_LICENSES = {
    "requests": "Apache-2.0",
    "flask": "BSD-3-Clause",
    "jsonschema": "MIT",
    "pytest": "MIT",
    "anthropic": "MIT",
}

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


@dataclass
class LicenseFinding:
    package: str
    license: str
    blocking: bool
    reason: str


@dataclass
class LicenseReport:
    findings: list[LicenseFinding] = field(default_factory=list)

    @property
    def blocking(self) -> list[LicenseFinding]:
        return [f for f in self.findings if f.blocking]

    @property
    def clean(self) -> bool:
        return not self.blocking

    def render(self) -> str:
        return "\n".join(
            f"- {f.package}: {f.license} ({'BLOCK' if f.blocking else 'warn'}) — {f.reason}"
            for f in self.findings
        )


class LicenseChecker:
    def __init__(
        self,
        *,
        allow: set[str] | None = None,
        deny: set[str] | None = None,
        licenses: dict[str, str] | None = None,
    ):
        self.allow = allow or DEFAULT_ALLOW
        self.deny = deny or DEFAULT_DENY
        self.licenses = {**DEFAULT_LICENSES, **(licenses or {})}

    def declared_dependencies(self, workdir: Path) -> list[str]:
        """Package names from requirements.txt (one deterministic source)."""
        req = Path(workdir) / "requirements.txt"
        if not req.exists():
            return []
        packages = []
        for line in req.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = _REQ_LINE.match(line)
            if match:
                packages.append(match.group(1))
        return packages

    def check(self, workdir: Path) -> LicenseReport:
        report = LicenseReport()
        for package in self.declared_dependencies(workdir):
            license_id = self.licenses.get(package.lower())
            if license_id is None:
                report.findings.append(
                    LicenseFinding(package, "unknown", False, "license not resolved")
                )
            elif license_id in self.deny:
                report.findings.append(
                    LicenseFinding(package, license_id, True, "denylisted (copyleft)")
                )
            elif license_id not in self.allow:
                report.findings.append(
                    LicenseFinding(package, license_id, False, "not on the allowlist")
                )
        return report
