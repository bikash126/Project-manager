"""Handoff verification (design doc §3).

The orchestrator validates every agent output before accepting it:
- schema validation happens inside Agent.run (jsonschema);
- the checks here are the content/coverage/traceability layer on top.

Failed validation becomes a revision task with the specific defects listed,
and counts toward the retry cap.
"""

from __future__ import annotations

from pathlib import Path

from pm_system.errors import UnsafePathError


def validate_prd(prd: dict) -> list[str]:
    defects: list[str] = []
    story_ids: set[str] = set()
    ac_ids: set[str] = set()
    for story in prd.get("user_stories", []):
        sid = story.get("id", "")
        if sid in story_ids:
            defects.append(f"duplicate user story id {sid}")
        story_ids.add(sid)
        for ac in story.get("acceptance_criteria", []):
            acid = ac.get("id", "")
            if acid in ac_ids:
                defects.append(f"duplicate acceptance criterion id {acid}")
            ac_ids.add(acid)
    return defects


def validate_dev_output(output: dict, ticket_id: str) -> list[str]:
    defects: list[str] = []
    if output.get("ticket_id") != ticket_id:
        defects.append(
            f"ticket_id is {output.get('ticket_id')!r}, expected {ticket_id!r} (traceability)"
        )
    paths = [f["path"] for f in output.get("files", [])]
    for path in paths:
        if not is_safe_relative_path(path):
            defects.append(f"unsafe file path {path!r} (must be relative, no '..')")
    if not any(Path(p).name.startswith("test_") or p.startswith("tests/") for p in paths):
        defects.append("no unit tests included (at least one test file is required)")
    if len(paths) != len(set(paths)):
        defects.append("duplicate file paths in output")
    return defects


def validate_qa_output(output: dict, ticket_id: str, ac_ids: set[str]) -> list[str]:
    defects: list[str] = []
    if output.get("ticket_id") != ticket_id:
        defects.append(
            f"ticket_id is {output.get('ticket_id')!r}, expected {ticket_id!r} (traceability)"
        )
    covered = {entry["ac_id"] for entry in output.get("test_plan", [])}
    missing = sorted(ac_ids - covered)
    if missing:
        defects.append(f"test plan does not cover acceptance criteria: {', '.join(missing)}")
    for file in output.get("test_files", []):
        path = file["path"]
        if not is_safe_relative_path(path):
            defects.append(f"unsafe file path {path!r} (must be relative, no '..')")
        elif not path.startswith("tests/"):
            defects.append(f"QA test file {path!r} must live under tests/")
    return defects


def is_safe_relative_path(path: str) -> bool:
    if not path or path.startswith(("/", "\\")) or "\\" in path:
        return False
    parts = Path(path).parts
    if not parts or any(part in ("..", "") for part in parts):
        return False
    if ":" in path:  # windows drive / URL-ish
        return False
    return True


def safe_write(root: Path, relative_path: str, content: str) -> Path:
    """Write a file under root, refusing anything that escapes it."""
    if not is_safe_relative_path(relative_path):
        raise UnsafePathError(relative_path)
    root = root.resolve()
    target = (root / relative_path).resolve()
    if root != target and root not in target.parents:
        raise UnsafePathError(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target
