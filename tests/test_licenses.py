"""Dependency license compliance check."""

from pm_system.security.licenses import LicenseChecker


def _reqs(tmp_path, content):
    (tmp_path / "requirements.txt").write_text(content)
    return tmp_path


def test_clean_when_no_dependencies(tmp_path):
    report = LicenseChecker().check(tmp_path)
    assert report.clean
    assert report.findings == []


def test_permissive_deps_pass(tmp_path):
    _reqs(tmp_path, "requests==2.31.0\nflask>=3\n")
    report = LicenseChecker().check(tmp_path)
    assert report.clean


def test_copyleft_dep_blocks(tmp_path):
    _reqs(tmp_path, "evilpkg==1.0\n")
    report = LicenseChecker(licenses={"evilpkg": "AGPL-3.0"}).check(tmp_path)
    assert not report.clean
    assert report.blocking[0].package == "evilpkg"
    assert "copyleft" in report.blocking[0].reason


def test_unknown_license_is_warning_not_block(tmp_path):
    _reqs(tmp_path, "mysterypkg==0.1\n")
    report = LicenseChecker().check(tmp_path)
    assert report.clean  # unknown is non-blocking
    assert report.findings[0].license == "unknown"


def test_requirements_parsing_ignores_comments(tmp_path):
    _reqs(tmp_path, "# a comment\n\nrequests==2.31.0\n")
    assert LicenseChecker().declared_dependencies(tmp_path) == ["requests"]
