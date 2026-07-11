"""PM Orchestrator — owns the project state machine (design doc §1, §3).

Phase 4 state machine:

    intake (Analyst -> PRD, validated)
    -> scope (Product Owner -> backlog / MVP / cut list)
    -> estimate (Estimator -> WBS + risk register)
    -> plan (Planner -> sprint plan + dependency graph)
      -> [HUMAN GATE 1: scope + budget + plan]
    -> architecture (Architect -> design doc, ADRs, contracts, data model)
      -> [HUMAN GATE 2: design]   (config.gate2_enabled)
    -> per-ticket loop (tickets from the sprint plan), bounded retry cap 3:
         Developer -> unit tests -> Security scan (blocking, pre-review)
                   -> Review (approve/block) -> QA (acceptance tests)
         cap reached -> escalate to human, PR closed, WIP left on the branch
    -> ship path (only if every ticket passed; config.enable_ship):
         integration tests + license check
         -> Data Engineer (migrations, if the design has a data model)
         -> DevOps (CI/CD + IaC)
         -> Release Manager (version, changelog, deploy + rollback plan)
           -> [HUMAN GATE 3: release]  (config.gate3_enabled)
           -> deploy (Release Manager only) -> Docs
    -> done (digest: PR pass rate, security findings, human interventions,
             shipped version, cost)

Security runs before Review (exit criterion: findings caught pre-review) and
its verdict is block/pass (D7). Each ticket is a PR. A broken build (any
escalated ticket) is never shipped.

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
from pm_system.agents.data_engineer import DataEngineerAgent
from pm_system.agents.developer import DeveloperAgent
from pm_system.agents.devops import DevOpsAgent
from pm_system.agents.doc_writer import DocWriterAgent
from pm_system.agents.estimator import EstimatorAgent
from pm_system.agents.planner import PlannerAgent
from pm_system.agents.product_owner import ProductOwnerAgent
from pm_system.agents.qa import QAAgent
from pm_system.agents.release_manager import ReleaseManagerAgent
from pm_system.agents.reviewer import ReviewerAgent
from pm_system.agents.security import SecurityAgent
from pm_system.artifacts.store import Artifact, ArtifactStatus, ArtifactStore
from pm_system.config import OrchestratorConfig
from pm_system.costs.ledger import CostLedger
from pm_system.errors import (
    AgentOutputError,
    BudgetExceededError,
    ContextOverflowError,
    EscalationError,
)
from pm_system.gates.gate import GateDecision, HumanGate
from pm_system.llm.client import CostTags
from pm_system.notify.notifier import ConsoleNotifier, Notifier
from pm_system.orchestrator.deploy import Deployer, NullDeployer
from pm_system.orchestrator.git_workspace import GitWorkspace, NullGitWorkspace
from pm_system.orchestrator.pr import NullPRPublisher, PRPublisher, Verdict
from pm_system.orchestrator.validation import (
    safe_write,
    topological_order,
    validate_architecture,
    validate_backlog,
    validate_data_engineering,
    validate_dev_output,
    validate_devops,
    validate_docs,
    validate_prd,
    validate_qa_output,
    validate_release,
    validate_review,
    validate_security_triage,
    validate_sprint_plan,
    validate_wbs,
)
from pm_system.sandbox.runner import Sandbox, TestRunner
from pm_system.security.licenses import LicenseChecker
from pm_system.security.scanners import SecurityScanSuite, Severity

STAGE_INTAKE = "intake"
STAGE_SCOPE = "scope"
STAGE_ESTIMATE = "estimate"
STAGE_PLAN = "plan"
STAGE_GATE1 = "gate1"
STAGE_ARCH = "architecture"
STAGE_GATE2 = "gate2"
STAGE_BUILD = "build"
STAGE_SECURITY = "security"
STAGE_REVIEW = "review"
STAGE_QA = "qa"
STAGE_INTEGRATION = "integration"
STAGE_DATA = "data_engineering"
STAGE_DEVOPS = "devops"
STAGE_GATE3 = "gate3"
STAGE_RELEASE = "release"
STAGE_DOCS = "docs"
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
    pr_number: int | None = None
    security_findings: int = 0  # findings surfaced across all attempts
    security_blocks: int = 0  # attempts blocked by a confirmed finding
    review_blocks: int = 0  # attempts blocked by the Reviewer
    qa_failures: int = 0  # attempts failed at QA acceptance tests


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
    # Phase 3 metric: share of PRs (tickets) passing Review+QA within the cap.
    pr_pass_rate: float | None = None
    security_findings_total: int = 0
    security_blocks_total: int = 0
    # Phase 4 ship path
    shipped: bool = False
    release_version: str | None = None
    # Phase 4 metric: how many times a human had to engage (gates + escalations).
    human_interventions: int = 0


DEFAULT_STANDARDS_PATH = Path(__file__).resolve().parents[2] / "standards" / "coding_standards.md"


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
        reviewer: ReviewerAgent | None = None,
        security: SecurityAgent | None = None,
        data_engineer: DataEngineerAgent | None = None,
        devops: DevOpsAgent | None = None,
        release_manager: ReleaseManagerAgent | None = None,
        doc_writer: DocWriterAgent | None = None,
        scan_suite: SecurityScanSuite | None = None,
        license_checker: LicenseChecker | None = None,
        pr_publisher: PRPublisher | None = None,
        deployer: Deployer | None = None,
        gate2: HumanGate | None = None,
        gate3: HumanGate | None = None,
        notifier: Notifier | None = None,
        config: OrchestratorConfig | None = None,
        test_python: str = "python",
        standards_path: Path | None = None,
    ):
        self.config = config or OrchestratorConfig()
        # D3: QA, Reviewer, and Security must each be a separate model instance
        # from the Developer — fresh context, adversarial framing.
        for role, agent in (("QA", qa), ("Reviewer", reviewer), ("Security", security)):
            if agent is None:
                continue
            if developer.llm is agent.llm or developer.llm.client is agent.llm.client:
                raise ValueError(
                    f"design decision D3: {role} must run on a separate model "
                    "instance from the Developer (pass distinct LLM clients)"
                )
        if self.config.enable_review and reviewer is None:
            raise ValueError("enable_review is set but no reviewer agent was provided")
        if self.config.enable_security and security is None:
            raise ValueError("enable_security is set but no security agent was provided")
        if self.config.enable_ship:
            missing = [
                name
                for name, agent in (
                    ("devops", devops),
                    ("release_manager", release_manager),
                    ("doc_writer", doc_writer),
                )
                if agent is None
            ]
            if missing:
                raise ValueError(
                    f"enable_ship is set but these agents were not provided: {', '.join(missing)}"
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
        self.reviewer = reviewer
        self.security = security
        self.data_engineer = data_engineer
        self.devops = devops
        self.release_manager = release_manager
        self.doc_writer = doc_writer
        self.scan_suite = scan_suite or SecurityScanSuite()
        self.license_checker = license_checker or LicenseChecker()
        self.pr_publisher = pr_publisher or NullPRPublisher()
        self.deployer = deployer or NullDeployer()
        self.gate = gate
        self.gate2 = gate2 or gate
        self.gate3 = gate3 or gate
        self.workspace_root = Path(workspace_root)
        self.notifier = notifier or ConsoleNotifier()
        self.test_runner = TestRunner(
            sandbox, python=test_python, timeout=self.config.sandbox_timeout
        )
        self.blocking_severity = Severity.parse(self.config.blocking_severity, Severity.HIGH)
        path = standards_path or DEFAULT_STANDARDS_PATH
        self.standards = path.read_text() if path.exists() else ""
        self.tracker = ValidationTracker()
        self._versions: dict[str, int] = {}
        self._interventions = 0

    # ------------------------------------------------------------------ api

    def run_project(self, project_id: str, raw_idea: str, constraints: str = "") -> ProjectResult:
        result = ProjectResult(project_id=project_id, status="completed")
        self._versions = {}
        self._interventions = 0
        try:
            prd = self._stage_prd(project_id, raw_idea)
            result.prd_artifact_id = f"{project_id}:PRD"
            planning = self._planning_with_gate1(project_id, prd, constraints)
            architecture = self._architecture_with_gate2(project_id, prd, planning, constraints)

            tickets = self._derive_tickets(project_id, prd, planning, architecture)
            workspace = self._init_workspace(project_id)
            result.workspace = workspace.root

            for ticket in tickets:
                ticket_result = self._run_ticket(project_id, workspace, ticket)
                result.tickets.append(ticket_result)
                if ticket_result.status == "escalated":
                    result.escalations.append(
                        f"{ticket_result.ticket_id} escalated after "
                        f"{ticket_result.attempts} attempts: {ticket_result.detail[:500]}"
                    )

            if any(t.status == "escalated" for t in result.tickets):
                # A broken build is not shippable — skip the ship path.
                result.status = "escalated"
            elif self.config.enable_ship:
                self._ship(project_id, prd, architecture, workspace, result)
        except EscalationError as exc:
            result.status = "escalated"
            result.escalations.append(str(exc))
        except ContextOverflowError as exc:
            result.status = "escalated"
            result.escalations.append(f"context cap exceeded: {exc}")
        except BudgetExceededError as exc:
            result.status = "budget_exceeded"
            result.escalations.append(str(exc))
        return self._finish(result)

    def _request_gate(self, gate: HumanGate, name: str, digest: str) -> GateDecision:
        """Every gate interaction is a human intervention (metric, design §9)."""
        self._interventions += 1
        return gate.request_approval(name, digest)

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
            decision = self._request_gate(
                self.gate,
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
            decision = self._request_gate(
                self.gate2,
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
        pr = self.pr_publisher.open_pr(
            ticket_id=ticket_id,
            branch=branch,
            title=f"{ticket_id}: {ticket['story']}",
            body=f"Implements {ticket['story_id']} / {ticket['wbs_id']}.\n\n{ticket['description']}",
        )
        result = TicketResult(
            ticket_id=ticket_id,
            story_id=ticket["story_id"],
            status="escalated",
            attempts=0,
            branch=branch,
            pr_number=pr.number,
        )
        dev_feedback: str | None = None
        last_failure = ""

        for attempt in range(1, self.config.max_retries + 1):
            result.attempts = attempt
            passed_review: dict | None = None
            self._notify(project_id, STAGE_BUILD, f"{ticket_id} attempt {attempt} (PR #{pr.number})")

            # --- Developer
            dev_out, defects = None, []
            try:
                dev_out = self.developer.run(
                    ContextPackage(
                        instructions=(
                            f"Implement ticket {ticket_id}. Deliver production code plus "
                            "unit tests as repo-relative files (tests under tests/). "
                            "Follow the attached architecture slices and the coding "
                            "standards. Never hardcode secrets. "
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
                return self._escalate_ticket(project_id, workspace, pr, result,
                                             f"context cap exceeded: {exc}")
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

            diff = self._diff(dev_out)

            # --- Security scan (deterministic detection + agent triage), pre-review
            if self.config.enable_security:
                try:
                    blocked, security_report, n_findings = self._security_step(
                        project_id, ticket, workspace, diff, pr
                    )
                except ContextOverflowError as exc:
                    return self._escalate_ticket(project_id, workspace, pr, result,
                                                 f"context cap exceeded: {exc}")
                result.security_findings += n_findings
                if blocked:
                    result.security_blocks += 1
                    last_failure = security_report
                    dev_feedback = security_report
                    self._notify(project_id, STAGE_SECURITY, f"{ticket_id} blocked by security")
                    continue

            # --- Review (separate model instance, adversarial), pre-QA
            if self.config.enable_review:
                try:
                    review = self._review_step(project_id, ticket_id, diff, pr, attempt)
                except ContextOverflowError as exc:
                    return self._escalate_ticket(project_id, workspace, pr, result,
                                                 f"context cap exceeded: {exc}")
                if review is None:  # invalid review output; burn attempt
                    last_failure = "Reviewer output was invalid"
                    continue
                if review["verdict"] == "block":
                    result.review_blocks += 1
                    last_failure = self._format_review(review)
                    dev_feedback = last_failure
                    self._notify(project_id, STAGE_REVIEW, f"{ticket_id} blocked by review")
                    continue
                passed_review = review

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
                return self._escalate_ticket(project_id, workspace, pr, result,
                                             f"context cap exceeded: {exc}")
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
            if not acceptance.passed:
                result.qa_failures += 1
                last_failure = (
                    f"QA acceptance tests failed (exit {acceptance.exit_code}) — bug report:\n"
                    f"{acceptance.summary}"
                )
                dev_feedback = last_failure
                self.pr_publisher.post_verdict(
                    pr, Verdict("qa", "fail", acceptance.summary[-500:])
                )
                self._notify(project_id, STAGE_QA, f"{ticket_id} acceptance tests failed")
                continue

            # --- all gates green
            workspace.commit_all(f"{ticket_id}: implement {ticket['story_id']}")
            workspace.merge_ticket_branch(branch)
            self.pr_publisher.post_verdict(pr, Verdict("qa", "pass"))
            self.pr_publisher.merge_pr(pr)
            self._record_ticket_artifacts(
                project_id, ticket, dev_out, qa_out, acceptance.summary,
                review=passed_review,
            )
            result.status = "passed"
            self._notify(
                project_id, STAGE_QA,
                f"{ticket_id} passed Review+QA on attempt {attempt}; PR #{pr.number} merged",
            )
            return result

        return self._escalate_ticket(project_id, workspace, pr, result, last_failure)

    # ---------------------------------------------------------- ship path

    def _ship(self, project_id, prd, architecture, workspace, result: ProjectResult) -> None:
        """Integration -> data migrations -> DevOps -> Gate 3 -> release -> docs.

        Only reached when every ticket passed. Raises EscalationError if a ship
        step cannot be satisfied (caught by run_project).
        """
        # --- Integration QA + contract tests + license check
        self._notify(project_id, STAGE_INTEGRATION, "running integration tests + license check")
        integration = self.test_runner.run(workspace.root)
        if not integration.passed:
            raise EscalationError(
                f"integration tests failed after all tickets merged:\n{integration.summary[-1000:]}"
            )
        license_report = self.license_checker.check(workspace.root)
        if not license_report.clean:
            raise EscalationError(
                "license check blocked the release:\n" + license_report.render()
            )
        self._record(project_id, f"{project_id}:INTEGRATION", "integration", {
            "tests": "passed",
            "license_findings": [f.package for f in license_report.findings],
        }, created_by="orchestrator", trace_ids=[])

        # --- Data Engineer (only if the architecture defines a data model)
        if self.data_engineer is not None and architecture.get("data_model"):
            migrations = self._produce(
                project_id=project_id,
                kind="migrations",
                stage=STAGE_DATA,
                agent=self.data_engineer,
                artifact_id=f"{project_id}:MIGRATIONS",
                artifact_type="migrations",
                instructions=(
                    "Produce reversible migrations (up/down) for the data model below, "
                    "plus optional seed data, and declare backward compatibility."
                ),
                artifacts={"data_model": architecture["data_model"]},
                validate=validate_data_engineering,
                trace_ids=lambda out: [m["id"] for m in out["migrations"]],
            )
            for migration in migrations["migrations"]:
                safe_write(
                    workspace.root,
                    f"migrations/{migration['id']}.sql",
                    f"-- {migration['description']}\n-- up\n{migration['up']}\n"
                    f"-- down\n{migration['down']}\n",
                )
            self.store.set_status(f"{project_id}:MIGRATIONS", ArtifactStatus.APPROVED)

        # --- DevOps: CI/CD + IaC
        devops = self._produce(
            project_id=project_id,
            kind="devops",
            stage=STAGE_DEVOPS,
            agent=self.devops,
            artifact_id=f"{project_id}:DEVOPS",
            artifact_type="devops",
            instructions=(
                "Produce the CI/CD pipeline and any IaC for this project, plus the "
                "environment list. Pipeline must at least install deps and run the tests."
            ),
            artifacts={"architecture_overview": architecture["overview"],
                       "components": architecture["components"]},
            validate=validate_devops,
            trace_ids=lambda out: [],
        )
        for file in devops.get("pipeline_files", []) + devops.get("iac_files", []):
            safe_write(workspace.root, file["path"], file["content"])
        self.store.set_status(f"{project_id}:DEVOPS", ArtifactStatus.APPROVED)

        # --- Release Manager: version, changelog, deploy + rollback plan
        story_summary = [{"id": s["id"], "story": s["story"]} for s in prd["user_stories"]]
        release = self._release_with_gate3(project_id, prd, story_summary, result)

        # --- Docs
        docs = self._produce(
            project_id=project_id,
            kind="docs",
            stage=STAGE_DOCS,
            agent=self.doc_writer,
            artifact_id=f"{project_id}:DOCS",
            artifact_type="docs",
            instructions=(
                "Write user-facing docs for the shipped project: a README plus any "
                "API/usage docs. Base them on the PRD and the shipped code."
            ),
            artifacts={
                "prd": {"title": prd["title"], "summary": prd["summary"], "user_stories": story_summary},
                "code_files": self._list_workspace(workspace.root),
                "version": release["version"],
            },
            validate=validate_docs,
            trace_ids=lambda out: [],
        )
        for doc in docs["docs"]:
            safe_write(workspace.root, doc["path"], doc["content"])
        self.store.set_status(f"{project_id}:DOCS", ArtifactStatus.APPROVED)

        if isinstance(workspace, GitWorkspace):
            workspace.commit_all(f"ship: release {release['version']}")
        result.shipped = True
        result.release_version = release["version"]
        self._notify(project_id, STAGE_DONE, f"shipped {release['version']}")

    def _release_with_gate3(self, project_id, prd, story_summary, result) -> dict:
        artifact_id = f"{project_id}:RELEASE"
        gate_feedback: str | None = None
        for round_number in range(1, self.config.max_retries + 1):
            release = self._produce(
                project_id=project_id,
                kind="release",
                stage=STAGE_RELEASE,
                agent=self.release_manager,
                artifact_id=artifact_id,
                artifact_type="release",
                instructions=(
                    "Produce the release: a semver version (this is the first release), "
                    "a changelog, a deploy plan, and a rollback plan."
                ),
                artifacts={"project": prd["title"], "stories": story_summary},
                validate=validate_release,
                trace_ids=lambda out: [],
                feedback=gate_feedback,
            )
            if not self.config.gate3_enabled:
                break
            self._notify(project_id, STAGE_GATE3, "requesting human approval (release)")
            decision = self._request_gate(
                self.gate3, "Gate 3 — release", self._gate3_digest(project_id, release)
            )
            if decision.approved:
                break
            gate_feedback = f"Human gate rejected the release (round {round_number}): {decision.comments}"
            self._notify(project_id, STAGE_GATE3, f"release rejected: {decision.comments}")
        else:
            raise EscalationError(
                f"release for {project_id} not approved within {self.config.max_retries} gate rounds"
            )

        self.store.set_status(artifact_id, ArtifactStatus.APPROVED)
        # Only the Release Manager triggers a deploy (credential scoping).
        deployment = self.deployer.deploy(
            project_id=project_id,
            version=release["version"],
            environment=self.config.deploy_environment,
            plan=release["deploy_plan"],
        )
        self._last_deployment = deployment
        safe_write(
            self.workspace_root / project_id,
            "CHANGELOG.md",
            self._render_changelog(release),
        )
        self._notify(project_id, STAGE_RELEASE, f"deployed {release['version']}")
        return release

    @staticmethod
    def _render_changelog(release: dict) -> str:
        lines = [f"# Changelog\n\n## {release['version']}\n"]
        for entry in release["changelog"]:
            lines.append(f"- **{entry['type']}**: {entry['description']}")
        return "\n".join(lines) + "\n"

    def _gate3_digest(self, project_id, release) -> str:
        changes = "\n".join(f"  - {e['type']}: {e['description']}" for e in release["changelog"])
        return (
            f"Project {project_id} — release {release['version']}\n"
            f"Changelog:\n{changes}\n"
            f"Deploy plan: {release['deploy_plan']}\n"
            f"Rollback plan: {release['rollback_plan']}\n"
            f"Spend so far: ${self.ledger.project_spend(project_id):.4f}"
        )

    def _record(self, project_id, artifact_id, artifact_type, content, *, created_by, trace_ids):
        self.store.put(
            artifact_id,
            artifact_type=artifact_type,
            content=content,
            created_by=created_by,
            project_id=project_id,
            trace_ids=trace_ids,
        )
        self.store.set_status(artifact_id, ArtifactStatus.APPROVED)

    def _security_step(self, project_id, ticket, workspace, diff, pr):
        """Run scanners, triage findings, return (blocked, report, n_findings).

        Zero findings -> pass without invoking the model (cost control).
        A confirmed finding at/above the blocking severity blocks (D7).
        """
        ticket_id = ticket["ticket_id"]
        scan = self.scan_suite.scan(workspace.root, self.test_runner.sandbox,
                                    timeout=self.config.sandbox_timeout)
        if not scan.has_findings:
            self.pr_publisher.post_verdict(pr, Verdict("security", "pass", "no findings"))
            return False, "", 0

        ids = {f"FND-{i:03d}": finding for i, finding in enumerate(scan.findings, start=1)}
        rendered = "\n".join(f.render(fid) for fid, f in ids.items())
        triage = self.security.run(
            ContextPackage(
                instructions=(
                    f"Triage the security scanner findings for ticket {ticket_id}. "
                    "For each finding, decide 'confirmed' or 'false_positive'; a "
                    "false positive needs a written reason. Add threat-model notes."
                ),
                artifacts={"findings": rendered, "diff": diff},
            ),
            CostTags(project_id=project_id, stage=STAGE_SECURITY, agent="security", ticket_id=ticket_id),
        )
        defects = validate_security_triage(triage, set(ids))
        if defects:
            report = "Security triage was invalid:\n" + "\n".join(f"- {d}" for d in defects)
            self.pr_publisher.post_verdict(pr, Verdict("security", "block", report))
            return True, report, len(ids)

        confirmed = {
            t["finding_id"] for t in triage["triage"] if t["status"] == "confirmed"
        }
        blocking = [
            (fid, ids[fid])
            for fid in confirmed
            if ids[fid].severity >= self.blocking_severity
        ]
        if blocking:
            lines = [ids[fid].render(fid) for fid, _ in sorted(blocking)]
            report = (
                "Security scan blocked this PR — fix these confirmed findings:\n"
                + "\n".join(lines)
            )
            self.pr_publisher.post_verdict(pr, Verdict("security", "block", report))
            return True, report, len(ids)

        self.pr_publisher.post_verdict(
            pr, Verdict("security", "pass", f"{len(ids)} finding(s), none blocking")
        )
        return False, "", len(ids)

    def _review_step(self, project_id, ticket_id, diff, pr, attempt):
        try:
            review = self.reviewer.run(
                ContextPackage(
                    instructions=(
                        f"Review the diff for ticket {ticket_id} against the coding "
                        "standards. Be adversarial — you did not write this code. "
                        "Approve only if it meets the bar; block with major/blocker "
                        "comments otherwise."
                    ),
                    artifacts={"diff": diff, "coding_standards": self.standards},
                ),
                CostTags(project_id=project_id, stage=STAGE_REVIEW, agent="reviewer", ticket_id=ticket_id),
            )
        except AgentOutputError:
            if attempt == 1:
                self.tracker.record("review", False)
            return None
        defects = validate_review(review)
        if attempt == 1:
            self.tracker.record("review", not defects)
        if defects:
            return None
        self.pr_publisher.post_verdict(
            pr, Verdict("reviewer", review["verdict"], self._format_review(review))
        )
        return review

    @staticmethod
    def _diff(dev_out: dict) -> list[dict]:
        """The review/scan surface: the files this ticket's developer produced."""
        return [{"path": f["path"], "content": f["content"]} for f in dev_out["files"]]

    @staticmethod
    def _format_review(review: dict) -> str:
        if not review["comments"]:
            return f"Review verdict: {review['verdict']}"
        lines = [f"Review verdict: {review['verdict']}. Comments:"]
        for c in review["comments"]:
            lines.append(f"- [{c['severity']}] {c['path']}: {c['comment']}")
        return "\n".join(lines)

    def _escalate_ticket(self, project_id, workspace, pr, result: TicketResult, detail) -> TicketResult:
        workspace.abandon_ticket_branch(result.branch)
        self.pr_publisher.close_pr(pr, f"escalated after {result.attempts} attempt(s)")
        result.status = "escalated"
        result.detail = detail
        self._interventions += 1  # a human now owns this ticket
        self._notify(
            project_id,
            STAGE_BUILD,
            f"{result.ticket_id} ESCALATED to human after {result.attempts} attempt(s) "
            f"(PR #{pr.number} closed, WIP left on {result.branch})",
        )
        return result

    # ------------------------------------------------------------- helpers

    def _record_ticket_artifacts(
        self, project_id, ticket, dev_out, qa_out, test_summary, review=None
    ) -> None:
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
        if review is not None:
            review_id = f"{project_id}:REVIEW-{ticket_id}"
            self.store.put(
                review_id,
                artifact_type="review",
                content={"ticket_id": ticket_id, **review},
                created_by="reviewer",
                project_id=project_id,
                trace_ids=[ticket_id, ticket["story_id"]],
            )
            self.store.set_status(review_id, ArtifactStatus.APPROVED)
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
        result.security_findings_total = sum(t.security_findings for t in result.tickets)
        result.security_blocks_total = sum(t.security_blocks for t in result.tickets)
        result.human_interventions = self._interventions
        if result.tickets:
            passed = sum(1 for t in result.tickets if t.status == "passed")
            result.pr_pass_rate = passed / len(result.tickets)
        rate = (
            "n/a"
            if result.first_try_validation_rate is None
            else f"{result.first_try_validation_rate:.0%}"
        )
        pr_rate = "n/a" if result.pr_pass_rate is None else f"{result.pr_pass_rate:.0%}"
        shipped = f"shipped {result.release_version}" if result.shipped else "not shipped"
        self._notify(
            result.project_id,
            STAGE_DONE,
            f"project {result.status}; {shipped}; {len(result.tickets)} tickets; "
            f"PR pass rate (Review+QA within {self.config.max_retries}) {pr_rate}; "
            f"security findings {result.security_findings_total} "
            f"({result.security_blocks_total} blocking); "
            f"human interventions {result.human_interventions}; "
            f"first-try validation rate {rate}; "
            f"total cost ${result.total_cost_usd:.4f}; "
            f"per stage: {self.ledger.summary(result.project_id)}",
        )
        return result

    def _notify(self, project_id: str, stage: str, message: str) -> None:
        self.notifier.notify(project_id, stage, message)
