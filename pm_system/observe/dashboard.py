"""Observability dashboard (design doc §1 observability, Phase 7).

Renders a self-contained HTML dashboard from the cost ledger, the artifact
store, and a ProjectResult: cost per stage and per agent, the metric row, and
the artifact/ticket tables. Deterministic and dependency-free, so it can be
written to the workspace or published as an artifact.
"""

from __future__ import annotations

import html


def _bar_rows(items: list[tuple[str, float]]) -> str:
    if not items:
        return "<tr><td>(none)</td><td></td></tr>"
    top = max(v for _, v in items) or 1.0
    rows = []
    for label, value in items:
        width = int(100 * value / top)
        rows.append(
            f"<tr><td>{html.escape(label)}</td>"
            f"<td><span class='bar' style='width:{width}%'></span> ${value:.4f}</td></tr>"
        )
    return "\n".join(rows)


def render_dashboard(result, ledger, store) -> str:
    """Return an HTML dashboard string for a finished project."""
    pid = result.project_id
    per_stage = sorted(ledger.summary(pid).items(), key=lambda kv: -kv[1])

    per_agent: dict[str, float] = {}
    for entry in ledger.entries(pid):
        per_agent[entry.agent] = per_agent.get(entry.agent, 0.0) + entry.cost_usd
    per_agent_rows = _bar_rows(sorted(per_agent.items(), key=lambda kv: -kv[1]))

    def metric(label, value):
        return f"<div class='metric'><div class='v'>{html.escape(str(value))}</div><div class='l'>{label}</div></div>"

    pr_rate = "n/a" if result.pr_pass_rate is None else f"{result.pr_pass_rate:.0%}"
    ftr = (
        "n/a" if result.first_try_validation_rate is None
        else f"{result.first_try_validation_rate:.0%}"
    )
    metrics = "".join([
        metric("status", result.status),
        metric("shipped", result.release_version or "no"),
        metric("PR pass rate", pr_rate),
        metric("first-try valid.", ftr),
        metric("security findings", result.security_findings_total),
        metric("human interventions", result.human_interventions),
        metric("KB entries", result.kb_entries_written),
        metric("total cost", f"${result.total_cost_usd:.4f}"),
    ])

    ticket_rows = "\n".join(
        f"<tr><td>{html.escape(t.ticket_id)}</td><td>{html.escape(t.story_id)}</td>"
        f"<td>{html.escape(t.status)}</td><td>{t.attempts}</td>"
        f"<td>{t.security_findings}</td><td>{t.review_blocks}</td><td>{t.qa_failures}</td></tr>"
        for t in result.tickets
    ) or "<tr><td colspan='7'>(no tickets)</td></tr>"

    artifact_rows = "\n".join(
        f"<tr><td>{html.escape(a.artifact_id)}</td><td>{html.escape(a.artifact_type)}</td>"
        f"<td>v{a.version}</td><td>{html.escape(a.status.value)}</td></tr>"
        for a in store.list_project(pid)
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(pid)} — dashboard</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }}
 h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1rem; margin-top: 1.5rem; }}
 .metrics {{ display: flex; flex-wrap: wrap; gap: .75rem; }}
 .metric {{ border: 1px solid #ddd; border-radius: 8px; padding: .6rem .9rem; min-width: 110px; }}
 .metric .v {{ font-size: 1.15rem; font-weight: 600; }}
 .metric .l {{ font-size: .72rem; color: #666; text-transform: uppercase; }}
 table {{ border-collapse: collapse; width: 100%; font-size: .85rem; margin-top: .4rem; }}
 td, th {{ border-bottom: 1px solid #eee; padding: .3rem .5rem; text-align: left; }}
 .bar {{ display: inline-block; height: .7rem; background: #4c8bf5; border-radius: 3px; margin-right: .4rem; vertical-align: middle; }}
</style></head><body>
<h1>{html.escape(pid)} — project dashboard</h1>
<div class="metrics">{metrics}</div>
<h2>Cost by stage</h2><table>{_bar_rows(per_stage)}</table>
<h2>Cost by agent</h2><table>{per_agent_rows}</table>
<h2>Tickets</h2><table>
<tr><th>ticket</th><th>story</th><th>status</th><th>attempts</th><th>sec</th><th>rev blocks</th><th>qa fails</th></tr>
{ticket_rows}</table>
<h2>Artifacts</h2><table>
<tr><th>id</th><th>type</th><th>version</th><th>status</th></tr>
{artifact_rows}</table>
</body></html>
"""
