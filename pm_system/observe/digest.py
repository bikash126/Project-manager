"""Stakeholder digest (design doc §1, Phase 7).

A concise, non-technical project summary for stakeholders — what shipped, the
scope delivered, cost, and anything that needs attention — distinct from the
per-stage engineering notifications.
"""

from __future__ import annotations


def stakeholder_digest(result, store=None) -> str:
    """Return a short markdown digest of a finished project."""
    pid = result.project_id
    lines = [f"# {pid} — status digest", ""]

    if result.shipped:
        lines.append(f"**Shipped** version `{result.release_version}`. ✅")
    elif result.status == "budget_exceeded":
        lines.append("**Halted — budget exceeded.** ⛔")
    else:
        lines.append(f"**Not shipped** — status `{result.status}`. ⚠️")
    lines.append("")

    passed = sum(1 for t in result.tickets if t.status == "passed")
    lines += [
        "## Delivery",
        f"- Tickets delivered: {passed}/{len(result.tickets)}",
        f"- PR pass rate (Review+QA): "
        f"{'n/a' if result.pr_pass_rate is None else format(result.pr_pass_rate, '.0%')}",
        f"- Security findings: {result.security_findings_total} "
        f"({result.security_blocks_total} blocking)",
        f"- Human interventions: {result.human_interventions}",
        f"- Total cost: ${result.total_cost_usd:.2f}",
        "",
    ]

    if store is not None:
        try:
            prd = store.get(f"{pid}:PRD").content
            lines.append("## Scope")
            for story in prd["user_stories"]:
                lines.append(f"- {story['id']}: {story['story']}")
            lines.append("")
        except Exception:  # noqa: BLE001 - digest is best-effort
            pass

    if result.escalations:
        lines.append("## Needs attention")
        for esc in result.escalations:
            lines.append(f"- {esc[:200]}")
        lines.append("")

    return "\n".join(lines)
