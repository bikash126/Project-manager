# Multi-Agent Software Development PM System — Phases 1–3

Phases 1–3 of the [design document](https://app.notion.com/p/39ad619bbee38114b98cff069085958a):
a PM Orchestrator plus nine specialist agents that take a raw project idea
through the upstream pipeline — PRD → prioritized backlog/MVP → WBS +
estimates → sprint plan → **Gate 1** → architecture/ADRs → **Gate 2** — and
then through the per-ticket quality loop `Dev → Security → Review → QA`, each
ticket a PR, on a toy project, with the foundations the design says must exist
from day one: versioned artifact store with a status lifecycle, cost ledger,
sandbox platform, and the agent eval harness.

## What's implemented — Phase 3 (quality + security loop)

| Design element | Where | Notes |
|---|---|---|
| Reviewer agent | `pm_system/agents/reviewer.py` | PR diff + standards doc → comments + approve/block; separate model instance (D3) |
| Security agent | `pm_system/agents/security.py` | Triages scanner findings (confirmed/false-positive + reason) + threat-model notes; separate instance |
| Deterministic security stack | `pm_system/security/scanners.py` | SAST (Semgrep), secrets (Gitleaks), deps (pip-audit) run in the sandbox + a built-in regex secret scanner that always works offline; findings normalized with a comparable severity |
| Restructured per-ticket loop | `pm_system/orchestrator/orchestrator.py` | `Dev → unit tests → Security (pre-review, blocking) → Review → QA`, bounded at 3 retries; each block/failure routes targeted feedback to the Developer |
| Block/pass security verdict (D7) | `_security_step` | The orchestrator, not the agent, computes the verdict: a **confirmed** finding at/above `blocking_severity` blocks — the agent can only exclude one by marking it a false positive *with a reason* (auditable); it cannot downgrade a real finding |
| Security is pre-review | loop ordering | Exit criterion "findings caught pre-review": Review runs only after Security passes |
| Real GitHub PRs | `pm_system/orchestrator/pr.py` | `PRPublisher` abstraction: `GitHubPRPublisher` (REST API) + `NullPRPublisher` default; a PR per ticket carries the Security/Review/QA verdicts, merges on green, closes on escalation |
| Coding standards doc | `standards/coding_standards.md` | The KB standards doc the Reviewer checks against (a versioned artifact) |
| P3 metric | `ProjectResult.pr_pass_rate` | Share of PRs passing Review+QA within the retry cap; reported in the final digest alongside security-finding counts |
| P1/P3 playbooks | `playbooks/{reviewer,security}.md` | Review checklist + severity taxonomy; threat-model triage + false-positive protocol |
| Egress allowlist proxy | `pm_system/sandbox/egress_proxy.py` | Now backs the security-tool network needs: allowlist mode lets scanners reach only approved hosts |

**Phase 3 exit criteria:** the toy project runs the full `Dev → Security →
Review → QA` loop with each ticket as a merged PR (`python -m
examples.run_toy_project` — 100% PR pass rate); and security findings are
caught pre-review — `--inject-secret` plants a hardcoded key, the Security
gate blocks it before the Reviewer is ever called, and the fixed retry merges
(`tests/test_toy_project.py`, `tests/test_phase3_loop.py`).

## What's implemented — Phase 2 (upstream + PO)

| Design element | Where | Notes |
|---|---|---|
| Product Owner agent | `pm_system/agents/product_owner.py` | PRD + constraints → prioritized backlog, MVP scope, cut list; human-heavy via Gate 1 |
| Estimator agent | `pm_system/agents/estimator.py` | MVP stories → WBS with three-point estimates, risk register; `kb_calibration` input ready for Phase 6 |
| Planner agent | `pm_system/agents/planner.py` | WBS + capacity → sprint plan, dependency graph, milestones |
| Architect agent | `pm_system/agents/architect.py` | MVP stories → components, ADRs (alternatives mandatory), API contracts, data model |
| Extended state machine | `pm_system/orchestrator/orchestrator.py` | intake → scope → estimate → plan → Gate 1 → architecture → Gate 2 → build; Gate 1 approves PRD/backlog/WBS/plan en bloc; rejections route to the PO (Gate 1) or Architect (Gate 2) as revision tasks |
| Artifact schemas | each agent's `output_schema` | PRD, backlog, WBS, sprint plan, architecture/ADR — enforced before acceptance |
| Traceability IDs + index | `ArtifactStore.find_by_trace` | US → WBS → ticket → code/QA; "what depends on US-001?" is one query |
| Handoff coverage checks | `pm_system/orchestrator/validation.py` | backlog ranks every story; WBS covers every MVP story; plan schedules every WBS item within capacity, dependency-ordered, cycle-free; design covers every MVP story |
| **Eval harness** | `pm_system/evals/` | Golden PRD → expected WBS/ADR/backlog/plan suites for all four Phase-2 agents; named deterministic checks; prompt fingerprinting so regressions are attributable; `python -m pm_system.evals.run` gates prompt changes against real models |
| P1/P2 playbooks | `playbooks/{architect,estimator,planner,product_owner}.md` | Same six-section structure as the P0 set |
| Context-size caps | `Agent.max_context_chars` | Hard stop per invocation, fires before any tokens are spent; overflow escalates instead of burning retries |
| Egress allowlist | `pm_system/sandbox/runner.py` + `egress_proxy.py` | `EgressPolicy.none()/full()/allowlist(hosts)`; allowlist mode runs tasks on an internal Docker network whose only route out is an allowlisting proxy sidecar |

**Phase 2 exit criteria:** the toy project now produces the full plan
(backlog → WBS → sprint plan → architecture) before any code is written, with
Gate 1 and Gate 2 functioning (`python -m examples.run_toy_project`); golden
eval suites exist for all four Phase-2 agents (`pm_system/evals/golden.py`).

## What's implemented — Phase 1 (skeleton)

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

Deliberately **not** built yet (later phases per the build plan): ship path —
DevOps/Release/Docs/Data Engineer + Gate 3 (Phase 4), change management /
`stale` propagation (5), Knowledge Base + retrospectives (6), UX/UI + parallel
dev (7). The hooks they need already exist: `kb_calibration`/`kb_adrs` inputs,
the `stale` status, the traceability index, and Gate 3 is one more
`HumanGate`.

## Exit criteria

- **P1 — toy project completes end-to-end** — `python -m examples.run_toy_project`
  drives a temperature-converter CLI from raw idea to two merged tickets with
  passing sandboxed acceptance tests (also covered by `tests/test_toy_project.py`).
- **P1/P2 — artifacts pass schema validation first try ≥ 80%** (handoff
  success rate) — tracked per run and reported as
  `first_try_validation_rate` in the final digest/result.
- **P2 — full plan for a small project, Gate 1 functioning, eval suites for
  all Phase-2 agents** — see the Phase 2 table above.
- **P3 — PRs pass Review+QA within 3 retries (≥ 70%), security findings caught
  pre-review** — reported as `pr_pass_rate`; the `--inject-secret` run shows
  the Security gate blocking before Review.

## Quickstart

```bash
pip install -e ".[dev]"          # + [llm] for Anthropic, + [slack] for Slack
python -m pytest                 # 108 tests
python -m examples.run_toy_project --sandbox local   # offline, scripted LLM
python -m examples.run_toy_project --sandbox local --inject-secret  # security gate demo

# eval the planning/review/security agents against real models (gates prompt changes)
ANTHROPIC_API_KEY=... python -m pm_system.evals.run --role reviewer --role security
```

The demo uses `MockLLM` (deterministic, offline) — the generated code is real
and executes in the sandbox. To run against real models, build the agents with
`AnthropicLLM` (needs `ANTHROPIC_API_KEY`) and a real gate:

```python
from pm_system import (AnalystAgent, AnthropicLLM, ArchitectAgent, ArtifactStore,
                       CostLedger, DeveloperAgent, EstimatorAgent, GitHubPRPublisher,
                       MeteredLLM, Orchestrator, PlannerAgent, ProductOwnerAgent,
                       QAAgent, ReviewerAgent, SecurityAgent, SlackGate, default_sandbox)
from pm_system.config import MID_MODEL, STRONG_MODEL

ledger = CostLedger("costs.db", stage_budgets={"intake": 5, "build": 20, "qa": 10})
store = ArtifactStore("artifacts.db")
llm = lambda: MeteredLLM(AnthropicLLM(), ledger)   # one instance per role (D3)

orchestrator = Orchestrator(
    store=store, ledger=ledger,
    analyst=AnalystAgent(llm(), model=STRONG_MODEL),
    product_owner=ProductOwnerAgent(llm(), model=STRONG_MODEL),
    estimator=EstimatorAgent(llm(), model=MID_MODEL),
    planner=PlannerAgent(llm(), model=MID_MODEL),
    architect=ArchitectAgent(llm(), model=STRONG_MODEL),
    developer=DeveloperAgent(llm(), model=STRONG_MODEL),
    reviewer=ReviewerAgent(llm(), model=STRONG_MODEL),   # separate instance (D3)
    security=SecurityAgent(llm(), model=STRONG_MODEL),   # separate instance (D3)
    qa=QAAgent(llm(), model=STRONG_MODEL),
    gate=SlackGate(token="xoxb-...", channel="#pm-gates"),
    pr_publisher=GitHubPRPublisher(owner="acme", repo="widget", token="ghp_..."),
    sandbox=default_sandbox(), workspace_root=Path("workspaces"),
)
result = orchestrator.run_project("my-project", "Build a ...",
                                  constraints="budget covers ~2/3 of scope")
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

Egress is policy-controlled: `DockerSandbox(egress=EgressPolicy.none())`
(default) runs with no network; `EgressPolicy.allowlist(["pypi.org",
"*.pythonhosted.org"])` runs tasks on an internal Docker network whose only
route out is an allowlisting HTTP(S) proxy sidecar
(`pm_system/sandbox/egress_proxy.py`) — direct egress is impossible because
internal networks have no external route.

## Layout

```
pm_system/
  orchestrator/   state machine, handoff validation, git workspace, PR publisher
  agents/         base + analyst/PO/estimator/planner/architect/developer/reviewer/security/qa
  artifacts/      versioned store with status lifecycle + traceability index
  costs/          cost ledger + budget caps
  llm/            Anthropic/mock clients, metering, pricing
  sandbox/        Docker/local runners, egress policy + proxy, test runner
  security/       deterministic scanners (SAST/secrets/deps) + severity model
  gates/          Slack/console/auto human gates
  notify/         stage-transition digests (console/Slack)
  evals/          eval harness, golden suites, real-model runner
playbooks/        analyst, developer, qa, architect, estimator, planner, product_owner, reviewer, security
standards/        coding_standards.md (KB standards doc for the Reviewer)
sandbox/          Dockerfile for the task image (+ security stack)
examples/         toy project runner + scripted mock responses
tests/            unit + end-to-end suite
```

Storage is SQLite behind Postgres-shaped interfaces; swapping the backend
(design target: Postgres) touches only `ArtifactStore`/`CostLedger` internals.
