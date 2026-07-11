"""PM Orchestrator — owns the project state machine (design doc §1, §3).

Phase 2 state machine:

    intake (Analyst -> PRD, validated)
    -> scope (Product Owner -> backlog / MVP / cut list)
    -> estimate (Estimator -> WBS + risk register)
    -> plan (Planner -> sprint plan + dependency graph)
      -> [HUMAN GATE 1: scope + budget + plan]
         rejection -> revision task to the Product Owner; estimate/plan re-run
         approval  -> PRD, backlog, WBS, and plan flip draft -> approved
    -> architecture (Architect -> design doc, ADRs, contracts, data model)
      -> [HUMAN GATE 2: design]   (config.gate2_enabled)
    -> per-ticket loop (tickets derived from the sprint plan), retry cap 3:
         Developer -> sandboxed unit tests -> QA -> sandboxed acceptance tests
    -> done (status digest with cost summary)

The orchestrator never writes code, designs, or docs itself; it curates
context packages, validates outputs, enforces gates/retries/budgets/context
caps, and emits a status digest on every stage transition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pm_system.agents.analyst import AnalystAgent
from pm_system.agents.architect import ArchitectAgent
from pm_system.agents.base import Agent, ContextPackage
from pm_system.agents.developer import DeveloperAgent
from pm_system.agents.estimator import EstimatorAgent
from pm_system.agents.planner import PlannerAgent
from pm_system.agents.product_owner import ProductOwnerAgent
from pm_system.agents.qa import QAAgent
from pm_system.artifacts.store import Artifact, ArtifactStatus, ArtifactStore
from pm_system.config import OrchestratorConfig
from pm_system.costs.ledger import CostLedger
from pm_system.errors import (
    AgentOutputError,
    BudgetExceededError,
    ContextOverflowError,
    EscalationError,
)
from pm_system.gates.gate import HumanGate
from pm_system.llm.client import CostTags
from pm_system.notify.notifier import ConsoleNotifier, Notifier
from pm_system.orchestrator.git_workspace import GitWorkspace, NullGitWorkspace
from pm_system.orchestrator.validation import (
    safe_write,
    topological_order,
    validate_architecture,
    validate_backlog,
    validate_dev_output,
    validate_prd,
    validate_qa_output,
    validate_sprint_plan,
    validate_wbs,
)
from pm_system.sandbox.runner import Sandbox, TestRunner

STAGE_INTAKE = "intake"
STAGE_SCOPE = "scope"
STAGE_ESTIMATE = "estimate"
STAGE_PLAN = "plan"
STAGE_GATE1 = "gate1"
STAGE_ARCH = "architecture"
STAGE_GATE2 = "gate2"
STAGE_BUILD = "build"
STAGE_QA = "qa"
STAGE_DONE = "done"


class ValidationTracker:
    """Tracks the P1–P2 success metric: share of artifacts that pass
    schema/content validation on the first try (handoff success rate).
    """

    def __init__(self):
        self.records: list[tuple[str, bool]] = []

    def record(self, kind: str, first_try_ok: bool) -> None:
        self.records.append((kind, first_try_ok))

    @property
    def first_try_rate(self) -> float | None:
        if not self.records:
            return None
        return sum(1 for _, ok in self.records if ok) / len(self.records)


@dataclass
class TicketResult:
    ticket_id: str
    story_id: str
    status: str  # "passed" | "escalated"
    attempts: int
    branch: str | None = None
    detail: str = ""


@dataclass
class ProjectResult:
    project_id: str
    status: str  # "completed" | "escalated" | "budget_exceeded"
    prd_artifact_id: str | None = None
    tickets: list[TicketResult] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    first_try_validation_rate: float | None = None
    total_cost_usd: float = 0.0
    workspace: Path | None = None


class Orchestrator:
    def __init__(
        self,
        *,
        store: ArtifactStore,
        ledger: CostLedger,
        analyst: AnalystAgent,
        product_owner: ProductOwnerAgent,
        estimator: EstimatorAgent,
        planner: PlannerAgent,
        architect: ArchitectAgent,
        developer: DeveloperAgent,
        qa: QAAgent,
        gate: HumanGate,
        sandbox: Sandbox,
        workspace_root: Path,
        gate2: HumanGate | None = None,
        notifier: Notifier | None = None,
        config: OrchestratorConfig | None = None,
        test_python: str = "python",
    ):
        if developer.llm is qa.llm or developer.llm.client is qa.llm.client:
            raise ValueError(
                "design decision D3: QA must run on a separate model instance "
                "from the Developer (pass distinct LLM clients)"
            )
        self.store = store
        self.ledger = ledger
        self.analyst = analyst
        self.product_owner = product_owner
        self.estimator = estimator
        self.planner = planner
        self.architect = architect
        self.developer = developer
        self.qa = qa
        self.gate = gate
        self.gate2 = gate2 or gate
        self.workspace_root = Path(workspace_root)
        self.notifier = notifier or ConsoleNotifier()
        self.config = config or OrchestratorConfig()
        self.test_runner = TestRunner(
            sandbox, python=test_python, timeout=self.config.sandbox_timeout
        )
        self.tracker = ValidationTracker()
        self._versions: dict[str, int] = {}

    # ------------------------------------------------------------------ api

    def run_project(self, project_id: str, raw_idea: str, constraints: str = "") -> ProjectResult:
        result = ProjectResult(project_id=project_id, status="completed")
        self._versions = {}
        try:
            prd = self._stage_prd(project_id, raw_idea)
            result.prd_artifact_id = f"{project_id}:PRD"
            planning = self._planning_with_gate1(project_id, prd, constraints)
            architecture = self._architecture_with_gate2(project_id, prd, planning, constraints)
        except EscalationError as exc:
            result.status = "escalated"
            result.escalations.append(str(exc))
            return self._finish(result)
        except ContextOverflowError as exc:
            result.status = "escalated"
            result.escalations.append(f"context cap exceeded: {exc}")
            return self._finish(result)
        except BudgetExceededError as exc:
            result.status = "budget_exceeded"
            result.escalations.append(str(exc))
            return self._finish(result)

        tickets = self._derive_tickets(project_id, prd, planning, architecture)
        workspace = self._init_workspace(project_id)
        result.workspace = workspace.root

        try:
            for ticket in tickets:
                ticket_result = self._run_ticket(project_id, workspace, ticket)
                result.tickets.append(ticket_result)
                if ticket_result.status == "escalated":
                    result.escalations.append(
                        f"{ticket_result.ticket_id} escalated after "
                        f"{ticket_result.attempts} attempts: {ticket_result.detail[:500]}"
                    )
        except BudgetExceededError as exc:
            result.status = "budget_exceeded"
            result.escalations.append(str(exc))
            return self._finish(result)

        if any(t.status == "escalated" for t in result.tickets):
            result.status = "escalated"
        return self._finish(result)

    # ---------------------------------------------------------- generic step

    def _produce(
        self,
        *,
        project_id: str,
        kind: str,
        stage: str,
        agent: Agent,
        artifact_id: str,
        artifact_type: str,
        instructions: str,
        artifacts: dict,
        validate: Callable[[dict], list[str]],
        trace_ids: Callable[[dict], list[str]],
        feedback: str | None = None,
    ) -> dict:
        """Run one agent with retries; store the validated output as a draft."""
        for attempt in range(1, self.config.max_retries + 1):
            self._notify(project_id, stage, f"{agent.role} producing {kind} (attempt {attempt})")
            output, defects = None, []
            try:
                output = agent.run(
                    ContextPackage(instructions=instructions, artifacts=artifacts, feedback=feedback),
                    CostTags(project_id=project_id, stage=stage, agent=agent.role),
                )
            except AgentOutputError as exc:
                defects = exc.defects
            if output is not None:
                defects = defects + validate(output)
            if attempt == 1:
                self.tracker.record(kind, not defects)
            if not defects:
                artifact = self.store.put(
                    artifact_id,
                    artifact_type=artifact_type,
                    content=output,
                    created_by=agent.role,
                    project_id=project_id,
                    trace_ids=trace_ids(output),
                    expected_version=self._versions.get(artifact_id),
                )
                self._versions[artifact_id] = artifact.version
                return output
            feedback = "Validation defects:\n" + "\n".join(f"- {d}" for d in defects)
            self._notify(project_id, stage, f"{kind} rejected by validation: {defects}")
        raise EscalationError(
            f"{kind} for {project_id} failed validation {self.config.max_retries} times; "
            f"last defects: {defects}"
        )

    # -------------------------------------------------------------- stages

    def _stage_prd(self, project_id: str, raw_idea: str) -> dict:
        return self._produce(
            project_id=project_id,
            kind="prd",
            stage=STAGE_INTAKE,
            agent=self.analyst,
            artifact_id=f"{project_id}:PRD",
            artifact_type="prd",
            instructions=(
                "Produce a PRD for the raw project idea below. Cover every distinct "
                "capability as its own user story with testable acceptance criteria.\n\n"
                f"Raw idea:\n{raw_idea}"
            ),
            artifacts={},
            validate=validate_prd,
            trace_ids=lambda prd: [s["id"] for s in prd["user_stories"]],
        )

    def _planning_with_gate1(self, project_id: str, prd: dict, constraints: str) -> dict:
        story_ids = {s["id"] for s in prd["user_stories"]}
        gate_feedback: str | None = None

        for round_number in range(1, self.config.max_retries + 1):
            backlog = self._produce(
                project_id=project_id,
                kind="backlog",
                stage=STAGE_SCOPE,
                agent=self.product_owner,
                artifact_id=f"{project_id}:BACKLOG",
                artifact_type="backlog",
                instructions=(
                    "Prioritize the PRD's user stories, choose the MVP scope, and list "
                    "any stories to cut or defer. Rank every story."
                    + (f"\n\nConstraints:\n{constraints}" if constraints else "")
                ),
                artifacts={"prd": prd},
                validate=lambda out: validate_backlog(out, story_ids),
                trace_ids=lambda out: sorted(story_ids),
                feedback=gate_feedback,
            )
            mvp_ids = set(backlog["mvp_story_ids"])
            mvp_stories = [s for s in prd["user_stories"] if s["id"] in mvp_ids]

            wbs = self._produce(
                project_id=project_id,
                kind="wbs",
                stage=STAGE_ESTIMATE,
                agent=self.estimator,
                artifact_id=f"{project_id}:WBS",
                artifact_type="wbs",
                instructions=(
                    "Break the MVP user stories below into a work breakdown structure "
                    "with three-point-style estimates (points plus a low/high confidence "
                    "range) and a risk register."
                ),
                artifacts={
                    "mvp_user_stories": mvp_stories,
                    # KB calibration data plugs in here in Phase 6
                    "kb_calibration": [],
                },
                validate=lambda out: validate_wbs(out, mvp_ids),
                trace_ids=lambda out: sorted(mvp_ids),
            )

            plan = self._produce(
                project_id=project_id,
                kind="sprint_plan",
                stage=STAGE_PLAN,
                agent=self.planner,
                artifact_id=f"{project_id}:PLAN",
                artifact_type="sprint_plan",
                instructions=(
                    "Sequence the WBS items into sprints. Respect the per-sprint capacity, "
                    "declare dependencies between WBS items ('from' depends on 'to'), and "
                    "set milestones."
                ),
                artifacts={
                    "wbs": wbs,
                    "capacity_points_per_sprint": self.config.sprint_capacity_points,
                },
                validate=lambda out: validate_sprint_plan(
                    out, wbs["wbs_items"], self.config.sprint_capacity_points
                ),
                trace_ids=lambda out: [item["id"] for item in wbs["wbs_items"]],
            )

            self._notify(project_id, STAGE_GATE1, "requesting human approval (scope + budget + plan)")
            decision = self.gate.request_approval(
                "Gate 1 — scope, budget & plan",
                self._gate1_digest(project_id, prd, backlog, wbs, plan),
            )
            if decision.approved:
                for suffix in ("PRD", "BACKLOG", "WBS", "PLAN"):
                    self.store.set_status(f"{project_id}:{suffix}", ArtifactStatus.APPROVED)
                self._notify(project_id, STAGE_GATE1, "plan approved")
                return {"backlog": backlog, "wbs": wbs, "plan": plan}
            gate_feedback = (
                f"Human gate rejected the plan (round {round_number}): {decision.comments}"
            )
            self._notify(project_id, STAGE_GATE1, f"plan rejected: {decision.comments}")

        raise EscalationError(
            f"plan for {project_id} not approved within {self.config.max_retries} gate rounds"
        )

    def _architecture_with_gate2(
        self, project_id: str, prd: dict, planning: dict, constraints: str
    ) -> dict:
        mvp_ids = set(planning["backlog"]["mvp_story_ids"])
        mvp_stories = [s for s in prd["user_stories"] if s["id"] in mvp_ids]
        artifact_id = f"{project_id}:ARCH"
        gate_feedback: str | None = None

        for round_number in range(1, self.config.max_retries + 1):
            architecture = self._produce(
                project_id=project_id,
                kind="architecture",
                stage=STAGE_ARCH,
                agent=self.architect,
                artifact_id=artifact_id,
                artifact_type="architecture",
                instructions=(
                    "Design the system for the MVP user stories below: components with "
                    "responsibilities, ADRs with alternatives considered, API contracts, "
                    "and the data model. Every MVP story must be covered by a component."
                    + (f"\n\nConstraints:\n{constraints}" if constraints else "")
                ),
                artifacts={
                    "mvp_user_stories": mvp_stories,
                    "wbs_summary": [
                        {"id": item["id"], "story_id": item["story_id"], "description": item["description"]}
                        for item in planning["wbs"]["wbs_items"]
                    ],
                    # KB ADR retrieval plugs in here in Phase 6
                    "kb_adrs": [],
                },
                validate=lambda out: validate_architecture(out, mvp_ids),
                trace_ids=lambda out: sorted(mvp_ids) + [adr["id"] for adr in out["adrs"]],
                feedback=gate_feedback,
            )
            if not self.config.gate2_enabled:
                self.store.set_status(artifact_id, ArtifactStatus.APPROVED)
                return architecture

            self._notify(project_id, STAGE_GATE2, "requesting human approval (architecture)")
            decision = self.gate2.request_approval(
                "Gate 2 — architecture & design",
                self._gate2_digest(project_id, architecture),
            )
            if decision.approved:
                self.store.set_status(artifact_id, ArtifactStatus.APPROVED)
                self._notify(project_id, STAGE_GATE2, "architecture approved")
                return architecture
            gate_feedback = (
                f"Human gate rejected the architecture (round {round_number}): {decision.comments}"
            )
            self._notify(project_id, STAGE_GATE2, f"architecture rejected: {decision.comments}")

        raise EscalationError(
            f"architecture for {project_id} not approved within {self.config.max_retries} gate rounds"
        )

    def _derive_tickets(
        self, project_id: str, prd: dict, planning: dict, architecture: dict
    ) -> list[dict]:
        """Mechanical derivation: one ticket per WBS item, in sprint order,
        dependency-respecting within each sprint, with the architecture slices
        relevant to the ticket's story attached (context curation)."""
        stories = {s["id"]: s for s in prd["user_stories"]}
        wbs_items = {item["id"]: item for item in planning["wbs"]["wbs_items"]}
        deps = planning["plan"]["dependencies"]

        ordered_wbs_ids: list[str] = []
        for sprint in sorted(planning["plan"]["sprints"], key=lambda s: s["number"]):
            ordered_wbs_ids.extend(topological_order(sprint["wbs_ids"], deps))

        tickets = []
        for index, wbs_id in enumerate(ordered_wbs_ids, start=1):
            item = wbs_items[wbs_id]
            story = stories[item["story_id"]]
            ticket_id = f"TCK-{index:03d}"
            components = [
                c for c in architecture["components"] if item["story_id"] in c["story_ids"]
            ]
            ticket = {
                "ticket_id": ticket_id,
                "wbs_id": wbs_id,
                "story_id": item["story_id"],
                "description": item["description"],
                "story": story["story"],
                "acceptance_criteria": story["acceptance_criteria"],
                "architecture": {
                    "components": components,
                    "api_contracts": architecture.get("api_contracts", []),
                },
            }
            self.store.put(
                f"{project_id}:{ticket_id}",
                artifact_type="ticket",
                content=ticket,
                created_by="orchestrator",
                project_id=project_id,
                trace_ids=[wbs_id, item["story_id"]],
            )
            self.store.set_status(f"{project_id}:{ticket_id}", ArtifactStatus.APPROVED)
            tickets.append(ticket)
        self._notify(
            project_id, STAGE_BUILD, f"derived {len(tickets)} tickets from the sprint plan"
        )
        return tickets

    def _run_ticket(self, project_id: str, workspace: GitWorkspace, ticket: dict) -> TicketResult:
        ticket_id = ticket["ticket_id"]
        branch = f"ticket/{ticket_id}"
        ac_ids = {ac["id"] for ac in ticket["acceptance_criteria"]}
        workspace.start_ticket_branch(branch)
        dev_feedback: str | None = None
        last_failure = ""

        for attempt in range(1, self.config.max_retries + 1):
            self._notify(project_id, STAGE_BUILD, f"{ticket_id} attempt {attempt}")

            # --- Developer
            dev_out, defects = None, []
            try:
                dev_out = self.developer.run(
                    ContextPackage(
                        instructions=(
                            f"Implement ticket {ticket_id}. Deliver production code plus "
                            "unit tests as repo-relative files (tests under tests/). "
                            "Follow the attached architecture slices. "
                            "The full pytest suite of the workspace must pass."
                        ),
                        artifacts={
                            "ticket": ticket,
                            "workspace_files": self._list_workspace(workspace.root),
                        },
                        feedback=dev_feedback,
                    ),
                    CostTags(project_id=project_id, stage=STAGE_BUILD, agent="developer", ticket_id=ticket_id),
                )
            except AgentOutputError as exc:
                defects = exc.defects
            except ContextOverflowError as exc:
                return self._escalate_ticket(project_id, workspace, ticket, branch,
                                             attempt, f"context cap exceeded: {exc}")
            if dev_out is not None:
                defects = defects + validate_dev_output(dev_out, ticket_id)
            if attempt == 1:
                self.tracker.record("code", not defects)
            if defects:
                last_failure = "Validation defects:\n" + "\n".join(f"- {d}" for d in defects)
                dev_feedback = last_failure
                continue

            for file in dev_out["files"]:
                safe_write(workspace.root, file["path"], file["content"])

            unit = self.test_runner.run(workspace.root)
            if not unit.passed:
                last_failure = f"Unit test run failed (exit {unit.exit_code}):\n{unit.summary}"
                dev_feedback = last_failure
                self._notify(project_id, STAGE_BUILD, f"{ticket_id} unit tests failed")
                continue

            # --- QA (separate model instance, deterministic verdict from the test run)
            qa_out, qa_defects = None, []
            try:
                qa_out = self.qa.run(
                    ContextPackage(
                        instructions=(
                            f"Write automated acceptance tests for ticket {ticket_id}. "
                            "Every acceptance criterion must map to at least one test. "
                            "Be adversarial: probe edge cases the developer likely missed. "
                            "Test files go under tests/qa/."
                        ),
                        artifacts={
                            "ticket": {k: v for k, v in ticket.items() if k != "architecture"},
                            "implemented_files": [f["path"] for f in dev_out["files"]],
                            "developer_notes": dev_out.get("notes", ""),
                        },
                    ),
                    CostTags(project_id=project_id, stage=STAGE_QA, agent="qa", ticket_id=ticket_id),
                )
            except AgentOutputError as exc:
                qa_defects = exc.defects
            except ContextOverflowError as exc:
                return self._escalate_ticket(project_id, workspace, ticket, branch,
                                             attempt, f"context cap exceeded: {exc}")
            if qa_out is not None:
                qa_defects = qa_defects + validate_qa_output(qa_out, ticket_id, ac_ids)
            if attempt == 1:
                self.tracker.record("qa_plan", not qa_defects)
            if qa_defects:
                # QA output itself was invalid; burn the attempt without blaming the developer.
                last_failure = "QA output defects:\n" + "\n".join(f"- {d}" for d in qa_defects)
                self._notify(project_id, STAGE_QA, f"{ticket_id} QA output invalid: {qa_defects}")
                continue

            for file in qa_out["test_files"]:
                safe_write(workspace.root, file["path"], file["content"])

            acceptance = self.test_runner.run(workspace.root)
            if acceptance.passed:
                workspace.commit_all(f"{ticket_id}: implement {ticket['story_id']}")
                workspace.merge_ticket_branch(branch)
                self._record_ticket_artifacts(project_id, ticket, dev_out, qa_out, acceptance.summary)
                self._notify(project_id, STAGE_QA, f"{ticket_id} passed QA on attempt {attempt}")
                return TicketResult(
                    ticket_id=ticket_id,
                    story_id=ticket["story_id"],
                    status="passed",
                    attempts=attempt,
                    branch=branch,
                )

            last_failure = (
                f"QA acceptance tests failed (exit {acceptance.exit_code}) — bug report:\n"
                f"{acceptance.summary}"
            )
            dev_feedback = last_failure
            self._notify(project_id, STAGE_QA, f"{ticket_id} acceptance tests failed")

        return self._escalate_ticket(
            project_id, workspace, ticket, branch, self.config.max_retries, last_failure
        )

    def _escalate_ticket(
        self, project_id, workspace, ticket, branch, attempts, detail
    ) -> TicketResult:
        workspace.abandon_ticket_branch(branch)
        self._notify(
            project_id,
            STAGE_BUILD,
            f"{ticket['ticket_id']} ESCALATED to human after {attempts} attempt(s) "
            f"(WIP left on {branch})",
        )
        return TicketResult(
            ticket_id=ticket["ticket_id"],
            story_id=ticket["story_id"],
            status="escalated",
            attempts=attempts,
            branch=branch,
            detail=detail,
        )

    # ------------------------------------------------------------- helpers

    def _record_ticket_artifacts(self, project_id, ticket, dev_out, qa_out, test_summary) -> None:
        ticket_id = ticket["ticket_id"]
        ac_ids = [ac["id"] for ac in ticket["acceptance_criteria"]]
        code_id = f"{project_id}:CODE-{ticket_id}"
        self.store.put(
            code_id,
            artifact_type="code",
            content={
                "ticket_id": ticket_id,
                "files": [f["path"] for f in dev_out["files"]],
                "notes": dev_out.get("notes", ""),
            },
            created_by="developer",
            project_id=project_id,
            trace_ids=[ticket_id, ticket["wbs_id"], ticket["story_id"]],
        )
        self.store.set_status(code_id, ArtifactStatus.APPROVED)
        qa_id = f"{project_id}:QA-{ticket_id}"
        self.store.put(
            qa_id,
            artifact_type="qa_report",
            content={
                "ticket_id": ticket_id,
                "test_plan": qa_out["test_plan"],
                "test_files": [f["path"] for f in qa_out["test_files"]],
                "verdict": "pass",
                "test_output": test_summary[-2000:],
            },
            created_by="qa",
            project_id=project_id,
            trace_ids=[ticket_id, *ac_ids],
        )
        self.store.set_status(qa_id, ArtifactStatus.APPROVED)

    def _init_workspace(self, project_id: str) -> GitWorkspace:
        root = self.workspace_root / project_id
        cls = GitWorkspace if GitWorkspace.available() else NullGitWorkspace
        workspace = cls(root)
        workspace.init()
        (root / "tests").mkdir(parents=True, exist_ok=True)
        (root / "tests" / ".gitkeep").write_text("")
        # A root conftest puts the workspace on sys.path so tests import the code.
        (root / "conftest.py").write_text("")
        (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
        (root / "README.md").write_text(f"# {project_id}\n\nBuilt by the PM system.\n")
        workspace.commit_all("chore: scaffold workspace")
        return workspace

    @staticmethod
    def _list_workspace(root: Path, cap: int = 200) -> list[str]:
        skip = {".git", "__pycache__", ".pytest_cache"}
        files = [
            str(p.relative_to(root))
            for p in sorted(root.rglob("*"))
            if p.is_file() and not (set(p.relative_to(root).parts) & skip)
        ]
        return files[:cap]

    def _gate1_digest(self, project_id, prd, backlog, wbs, plan) -> str:
        mvp = backlog["mvp_story_ids"]
        cuts = "\n".join(
            f"  - {c['story_id']}: {c['reason']}" for c in backlog["cut_list"]
        ) or "  (none)"
        stories = "\n".join(
            f"  - {s['id']}{' [MVP]' if s['id'] in mvp else ''}: {s['story']}"
            for s in prd["user_stories"]
        )
        low = sum(i["confidence"]["low"] for i in wbs["wbs_items"])
        high = sum(i["confidence"]["high"] for i in wbs["wbs_items"])
        return (
            f"Project {project_id} — {prd['title']}\n"
            f"{prd['summary']}\n"
            f"Stories:\n{stories}\n"
            f"Cut/deferred:\n{cuts}\n"
            f"Estimate: {wbs['total_points']} points (range {low}–{high}), "
            f"{len(wbs['risk_register'])} risks, {len(plan['sprints'])} sprint(s)\n"
            f"Spend so far: ${self.ledger.project_spend(project_id):.4f}"
        )

    def _gate2_digest(self, project_id, architecture) -> str:
        components = "\n".join(
            f"  - {c['name']}: {c['responsibility']} (covers {', '.join(c['story_ids'])})"
            for c in architecture["components"]
        )
        adrs = "\n".join(
            f"  - {a['id']} {a['title']}: {a['decision']} "
            f"(alternatives: {', '.join(a['alternatives'])})"
            for a in architecture["adrs"]
        )
        return (
            f"Project {project_id} — architecture\n"
            f"{architecture['overview']}\n"
            f"Components:\n{components}\n"
            f"ADRs:\n{adrs}\n"
            f"Spend so far: ${self.ledger.project_spend(project_id):.4f}"
        )

    def _finish(self, result: ProjectResult) -> ProjectResult:
        result.first_try_validation_rate = self.tracker.first_try_rate
        result.total_cost_usd = self.ledger.project_spend(result.project_id)
        rate = (
            "n/a"
            if result.first_try_validation_rate is None
            else f"{result.first_try_validation_rate:.0%}"
        )
        self._notify(
            result.project_id,
            STAGE_DONE,
            f"project {result.status}; {len(result.tickets)} tickets; "
            f"first-try validation rate {rate}; "
            f"total cost ${result.total_cost_usd:.4f}; "
            f"per stage: {self.ledger.summary(result.project_id)}",
        )
        return result

    def _notify(self, project_id: str, stage: str, message: str) -> None:
        self.notifier.notify(project_id, stage, message)
