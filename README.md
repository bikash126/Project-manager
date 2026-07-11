# Multi-Agent Software Development PM System — Phase 1 Skeleton

Phase 1 of the [design document](https://app.notion.com/p/39ad619bbee38114b98cff069085958a):
a PM Orchestrator plus Analyst/Developer/QA agents that take a raw project
idea through PRD → human gate → per-ticket build/QA loop → done, on a toy
project, with the foundations the design says must exist from day one —
versioned artifact store with a status lifecycle, cost ledger, and a sandbox
platform.

## What's implemented (Phase 1 scope)

| Design element | Where | Notes |
|---|---|---|
| PM Orchestrator (state machine, D1) | `pm_system/orchestrator/orchestrator.py` | Intake → Gate 1 → ticket derivation → bounded Dev→QA loop → done; never writes code/docs itself |
| Artifact store with versioning + lifecycle (D2, D8) | `pm_system/artifacts/store.py` | `draft → approved → stale → superseded`, optimistic locking, traceability IDs; agents refuse stale inputs |
| Cost ledger from day one | `pm_system/costs/ledger.py` | Every LLM call budget-checked and recorded, tagged project/stage/agent/ticket; per-stage + per-project hard stops |
| Analyst / Developer / QA agents | `pm_system/agents/` | Stateless workers; JSON-schema-validated outputs; scoped context packages |
| QA as a separate model instance (D3) | enforced in the `Orchestrator` constructor | Sharing an LLM client between Developer and QA raises at construction |
| Sandbox platform | `pm_system/sandbox/runner.py`, `sandbox/Dockerfile` | Docker-per-task, `--network none`, memory/CPU caps; unisolated local fallback for dev machines |
| One human gate — Slack (D4) | `pm_system/gates/gate.py` | `SlackGate` posts a digest, waits for `approve`/`reject <reason>` in-thread; rejection reasons become revision tasks; `ConsoleGate`/`AutoApproveGate` for local runs |
| Bounded retry loops | orchestrator | Cap of 3 per ticket and per PRD; each retry carries the specific defects/bug report, not history; cap reached → escalate to human, WIP left on the ticket branch |
| Handoff verification | `pm_system/orchestrator/validation.py` | Schema + coverage (QA plan must cover every AC id) + traceability (ticket/story IDs) + path-safety checks |
| Git, branch-per-ticket | `pm_system/orchestrator/git_workspace.py` | `ticket/TCK-xxx` branches merged to `main` on green QA |
| Status digests on every transition | `pm_system/notify/notifier.py` | Console and Slack notifiers |
| P0 playbooks | `playbooks/{analyst,developer,qa}.md` | Role definition, procedure, template, checklist, failure patterns, escalation rules |
| Model tiering | `pm_system/config.py`, `pm_system/llm/client.py` | Strong/mid/cheap tiers, per-family pricing for the ledger |

Deliberately **not** in Phase 1 (later phases per the build plan): Estimator/
Planner/Architect/PO and Gates 2–3 (Phase 2), Reviewer + security stack +
real GitHub PRs (Phase 3), ship path (4), change management / `stale`
propagation (5), Knowledge Base + retrospectives (6). The KB hook
(`ContextPackage.kb_entries`) and the `stale` status already exist so those
phases bolt on without reworking Phase 1.

## Exit criteria

- **Toy project completes end-to-end** — `python -m examples.run_toy_project`
  drives a temperature-converter CLI from raw idea to two merged tickets with
  passing sandboxed acceptance tests (also covered by `tests/test_toy_project.py`).
- **Artifacts pass schema validation first try ≥ 80%** — tracked per run and
  reported as `first_try_validation_rate` in the final digest/result.

## Quickstart

```bash
pip install -e ".[dev]"          # + [llm] for Anthropic, + [slack] for Slack
python -m pytest                 # 42 tests
python -m examples.run_toy_project --sandbox local   # offline, scripted LLM
```

The demo uses `MockLLM` (deterministic, offline) — the generated code is real
and executes in the sandbox. To run against real models, build the agents with
`AnthropicLLM` (needs `ANTHROPIC_API_KEY`) and a real gate:

```python
from pm_system import (AnalystAgent, AnthropicLLM, ArtifactStore, CostLedger,
                       DeveloperAgent, MeteredLLM, Orchestrator, QAAgent, SlackGate)
from pm_system.config import STRONG_MODEL

ledger = CostLedger("costs.db", stage_budgets={"intake": 5, "build": 20, "qa": 10})
store = ArtifactStore("artifacts.db")
llm = lambda: MeteredLLM(AnthropicLLM(), ledger)   # one instance per role (D3)

orchestrator = Orchestrator(
    store=store, ledger=ledger,
    analyst=AnalystAgent(llm(), model=STRONG_MODEL),
    developer=DeveloperAgent(llm(), model=STRONG_MODEL),
    qa=QAAgent(llm(), model=STRONG_MODEL),
    gate=SlackGate(token="xoxb-...", channel="#pm-gates"),
    sandbox=default_sandbox(), workspace_root=Path("workspaces"),
)
result = orchestrator.run_project("my-project", "Build a ...")
```

### Sandbox

Build the task image once (`pytest` is baked in because tasks run with
`--network none`):

```bash
docker build -t pm-sandbox:latest sandbox/
```

`default_sandbox()` uses Docker when a daemon responds and falls back to
`LocalSandbox` (plain subprocesses, **no isolation** — dev only). Force the
fallback with `PM_SANDBOX=local`.

## Layout

```
pm_system/
  orchestrator/   state machine, handoff validation, git workspace
  agents/         base + analyst/developer/qa (output JSON schemas)
  artifacts/      versioned store with status lifecycle
  costs/          cost ledger + budget caps
  llm/            Anthropic/mock clients, metering, pricing
  sandbox/        Docker/local runners, pytest test runner
  gates/          Slack/console/auto human gates
  notify/         stage-transition digests (console/Slack)
playbooks/        P0 playbooks: analyst, developer, qa
sandbox/          Dockerfile for the task image
examples/         toy project runner + scripted mock responses
tests/            unit + end-to-end suite
```

Storage is SQLite behind Postgres-shaped interfaces; swapping the backend
(design target: Postgres) touches only `ArtifactStore`/`CostLedger` internals.
