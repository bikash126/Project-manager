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


def validate_backlog(output: dict, story_ids: set[str]) -> list[str]:
    defects: list[str] = []
    backlog_ids = [entry["story_id"] for entry in output.get("backlog", [])]
    unknown = sorted(set(backlog_ids) - story_ids)
    if unknown:
        defects.append(f"backlog references unknown stories: {', '.join(unknown)}")
    missing = sorted(story_ids - set(backlog_ids))
    if missing:
        defects.append(f"backlog must rank every story; missing: {', '.join(missing)}")
    if len(backlog_ids) != len(set(backlog_ids)):
        defects.append("backlog ranks a story more than once")
    priorities = [entry["priority"] for entry in output.get("backlog", [])]
    if len(priorities) != len(set(priorities)):
        defects.append("backlog priorities must be unique")

    mvp = output.get("mvp_story_ids", [])
    if len(mvp) != len(set(mvp)):
        defects.append("duplicate ids in mvp_story_ids")
    unknown_mvp = sorted(set(mvp) - story_ids)
    if unknown_mvp:
        defects.append(f"MVP references unknown stories: {', '.join(unknown_mvp)}")
    cut = {entry["story_id"] for entry in output.get("cut_list", [])}
    overlap = sorted(cut & set(mvp))
    if overlap:
        defects.append(f"stories both in MVP and cut list: {', '.join(overlap)}")
    unknown_cut = sorted(cut - story_ids)
    if unknown_cut:
        defects.append(f"cut list references unknown stories: {', '.join(unknown_cut)}")
    return defects


def validate_wbs(output: dict, mvp_story_ids: set[str]) -> list[str]:
    defects: list[str] = []
    items = output.get("wbs_items", [])
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        defects.append("duplicate WBS ids")
    covered = {item["story_id"] for item in items}
    unknown = sorted(covered - mvp_story_ids)
    if unknown:
        defects.append(f"WBS covers stories outside the MVP scope: {', '.join(unknown)}")
    missing = sorted(mvp_story_ids - covered)
    if missing:
        defects.append(f"WBS does not cover MVP stories: {', '.join(missing)}")
    for item in items:
        low, high = item["confidence"]["low"], item["confidence"]["high"]
        if not low <= item["estimate_points"] <= high:
            defects.append(
                f"{item['id']}: estimate {item['estimate_points']} outside "
                f"confidence range [{low}, {high}]"
            )
    total = sum(item["estimate_points"] for item in items)
    if output.get("total_points") != total:
        defects.append(
            f"total_points is {output.get('total_points')}, sum of estimates is {total}"
        )
    risk_ids = [risk["id"] for risk in output.get("risk_register", [])]
    if len(risk_ids) != len(set(risk_ids)):
        defects.append("duplicate risk ids")
    return defects


def validate_sprint_plan(output: dict, wbs_items: list[dict], capacity: int) -> list[str]:
    defects: list[str] = []
    points = {item["id"]: item["estimate_points"] for item in wbs_items}
    known = set(points)

    numbers = [sprint["number"] for sprint in output.get("sprints", [])]
    if numbers != list(range(1, len(numbers) + 1)):
        defects.append(f"sprint numbers must be 1..n consecutive, got {numbers}")

    scheduled: dict[str, int] = {}
    for sprint in output.get("sprints", []):
        for wbs_id in sprint["wbs_ids"]:
            if wbs_id in scheduled:
                defects.append(f"{wbs_id} scheduled more than once")
            scheduled[wbs_id] = sprint["number"]
        load = sum(points.get(w, 0) for w in sprint["wbs_ids"])
        if load > capacity:
            defects.append(
                f"sprint {sprint['number']} is over capacity: {load} > {capacity} points"
            )
    unknown = sorted(set(scheduled) - known)
    if unknown:
        defects.append(f"plan schedules unknown WBS items: {', '.join(unknown)}")
    missing = sorted(known - set(scheduled))
    if missing:
        defects.append(f"plan does not schedule: {', '.join(missing)}")

    deps = output.get("dependencies", [])
    for dep in deps:
        if dep["from"] == dep["to"]:
            defects.append(f"{dep['from']} depends on itself")
        for key in ("from", "to"):
            if dep[key] not in known:
                defects.append(f"dependency references unknown WBS item {dep[key]}")
        if dep["from"] in scheduled and dep["to"] in scheduled:
            if scheduled[dep["to"]] > scheduled[dep["from"]]:
                defects.append(
                    f"{dep['from']} (sprint {scheduled[dep['from']]}) depends on "
                    f"{dep['to']} scheduled later (sprint {scheduled[dep['to']]})"
                )
    if _has_cycle(known, deps):
        defects.append("dependency graph contains a cycle")

    for milestone in output.get("milestones", []):
        if milestone["sprint"] not in numbers:
            defects.append(
                f"milestone {milestone['name']!r} references unknown sprint {milestone['sprint']}"
            )
    return defects


def _has_cycle(nodes: set[str], deps: list[dict]) -> bool:
    """Kahn's algorithm; anything left unprocessed is on a cycle."""
    incoming: dict[str, set[str]] = {n: set() for n in nodes}
    outgoing: dict[str, set[str]] = {n: set() for n in nodes}
    for dep in deps:
        if dep["from"] in nodes and dep["to"] in nodes and dep["from"] != dep["to"]:
            incoming[dep["from"]].add(dep["to"])
            outgoing[dep["to"]].add(dep["from"])
    ready = [n for n, blockers in incoming.items() if not blockers]
    processed = 0
    while ready:
        node = ready.pop()
        processed += 1
        for dependent in outgoing[node]:
            incoming[dependent].discard(node)
            if not incoming[dependent]:
                ready.append(dependent)
    return processed != len(nodes)


def topological_order(nodes: list[str], deps: list[dict]) -> list[str]:
    """Stable topo order of WBS ids ("from" depends on "to")."""
    node_set = set(nodes)
    blockers: dict[str, set[str]] = {n: set() for n in nodes}
    for dep in deps:
        if dep["from"] in node_set and dep["to"] in node_set and dep["from"] != dep["to"]:
            blockers[dep["from"]].add(dep["to"])
    ordered: list[str] = []
    remaining = list(nodes)
    while remaining:
        progress = [n for n in remaining if not (blockers[n] - set(ordered))]
        if not progress:  # cycle — validation reports it; keep input order
            ordered.extend(remaining)
            break
        ordered.extend(progress)
        remaining = [n for n in remaining if n not in progress]
    return ordered


def validate_architecture(output: dict, mvp_story_ids: set[str]) -> list[str]:
    defects: list[str] = []
    covered: set[str] = set()
    for component in output.get("components", []):
        covered.update(component["story_ids"])
    unknown = sorted(covered - mvp_story_ids)
    if unknown:
        defects.append(f"components reference stories outside MVP scope: {', '.join(unknown)}")
    missing = sorted(mvp_story_ids - covered)
    if missing:
        defects.append(f"design does not cover MVP stories: {', '.join(missing)}")
    adr_ids = [adr["id"] for adr in output.get("adrs", [])]
    if len(adr_ids) != len(set(adr_ids)):
        defects.append("duplicate ADR ids")
    names = [c["name"] for c in output.get("components", [])]
    if len(names) != len(set(names)):
        defects.append("duplicate component names")
    return defects


def validate_security_triage(output: dict, finding_ids: set[str]) -> list[str]:
    defects: list[str] = []
    triaged = [entry["finding_id"] for entry in output.get("triage", [])]
    unknown = sorted(set(triaged) - finding_ids)
    if unknown:
        defects.append(f"triage references unknown findings: {', '.join(unknown)}")
    missing = sorted(finding_ids - set(triaged))
    if missing:
        defects.append(f"every finding must be triaged; missing: {', '.join(missing)}")
    if len(triaged) != len(set(triaged)):
        defects.append("a finding is triaged more than once")
    for entry in output.get("triage", []):
        if entry["status"] == "false_positive" and not entry["reason"].strip():
            defects.append(
                f"{entry['finding_id']}: a false_positive needs a written reason"
            )
    return defects


def validate_data_engineering(output: dict) -> list[str]:
    defects: list[str] = []
    ids = [m["id"] for m in output.get("migrations", [])]
    if len(ids) != len(set(ids)):
        defects.append("duplicate migration ids")
    return defects


def validate_devops(output: dict) -> list[str]:
    defects: list[str] = []
    for group in ("pipeline_files", "iac_files"):
        for file in output.get(group, []):
            if not is_safe_relative_path(file["path"]):
                defects.append(f"unsafe file path {file['path']!r} in {group}")
    return defects


def validate_release(output: dict) -> list[str]:
    # semver + changelog shape are enforced by the JSON schema; nothing extra.
    return []


def validate_docs(output: dict) -> list[str]:
    defects: list[str] = []
    paths = [d["path"] for d in output.get("docs", [])]
    for path in paths:
        if not is_safe_relative_path(path):
            defects.append(f"unsafe doc path {path!r}")
    if not any("readme" in p.lower() for p in paths):
        defects.append("docs must include a README")
    if len(paths) != len(set(paths)):
        defects.append("duplicate doc paths")
    return defects


def validate_review(output: dict) -> list[str]:
    defects: list[str] = []
    verdict = output.get("verdict")
    severities = [c["severity"] for c in output.get("comments", [])]
    has_blocker = "blocker" in severities
    has_major = "major" in severities
    if has_blocker and verdict != "block":
        defects.append("a blocker-severity comment forces verdict=block")
    if verdict == "block" and not (has_blocker or has_major):
        defects.append("verdict=block requires at least one major or blocker comment")
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
