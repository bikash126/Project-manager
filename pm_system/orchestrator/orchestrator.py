"""PM Orchestrator — owns the project state machine (design doc §1, §3).

Phase 1 state machine:

    intake (Analyst -> PRD, validated)
      -> [HUMAN GATE 1: scope]          rejection -> revision task to Analyst
      -> ticket derivation (mechanical, one ticket per user story)
      -> per-ticket loop, retry cap 3:
           Developer -> sandboxed unit tests -> QA -> sandboxed acceptance tests
           any failure -> revision task with the specific defects/bug report
           cap reached -> ticket flagged, human notified, branch left as WIP
      -> done (status digest with cost summary)

The orchestrator never writes code, designs, or docs itself; it curates
context packages, validates outputs, enforces gates/retries/budgets, and
emits a status digest on every stage transition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pm_system.agents.analyst import AnalystAgent
from pm_system.agents.base import ContextPackage
from pm_system.agents.developer import DeveloperAgent
from pm_system.agents.qa import QAAgent
from pm_system.artifacts.store import Artifact, ArtifactStatus, ArtifactStore
from pm_system.config import OrchestratorConfig
from pm_system.costs.ledger import CostLedger
from pm_system.errors import AgentOutputError, BudgetExceededError, EscalationError
from pm_system.gates.gate import HumanGate
from pm_system.llm.client import CostTags
from pm_system.notify.notifier import ConsoleNotifier, Notifier
from pm_system.orchestrator.git_workspace import GitWorkspace, NullGitWorkspace
from pm_system.orchestrator.validation import (
    safe_write,
    validate_dev_output,
    validate_prd,
    validate_qa_output,
)
from pm_system.sandbox.runner import Sandbox, TestRunner

STAGE_INTAKE = "intake"
STAGE_GATE1 = "gate1"
STAGE_BUILD = "build"
STAGE_QA = "qa"
STAGE_DONE = "done"


class ValidationTracker:
    """Tracks the Phase 1 exit-criterion metric:
    share of artifacts that pass schema/content validation on the first try.
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
        developer: DeveloperAgent,
        qa: QAAgent,
        gate: HumanGate,
        sandbox: Sandbox,
        workspace_root: Path,
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
        self.developer = developer
        self.qa = qa
        self.gate = gate
        self.workspace_root = Path(workspace_root)
        self.notifier = notifier or ConsoleNotifier()
        self.config = config or OrchestratorConfig()
        self.test_runner = TestRunner(
            sandbox, python=test_python, timeout=self.config.sandbox_timeout
        )
        self.tracker = ValidationTracker()

    # ------------------------------------------------------------------ api

    def run_project(self, project_id: str, raw_idea: str) -> ProjectResult:
        result = ProjectResult(project_id=project_id, status="completed")
        try:
            prd_artifact = self._intake_with_gate(project_id, raw_idea)
        except EscalationError as exc:
            result.status = "escalated"
            result.escalations.append(str(exc))
            return self._finish(result)
        except BudgetExceededError as exc:
            result.status = "budget_exceeded"
            result.escalations.append(str(exc))
            return self._finish(result)

        result.prd_artifact_id = prd_artifact.artifact_id
        tickets = self._derive_tickets(project_id, prd_artifact)
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

    # -------------------------------------------------------------- stages

    def _intake_with_gate(self, project_id: str, raw_idea: str) -> Artifact:
        prd_id = f"{project_id}:PRD"
        feedback: str | None = None
        latest_version = 0

        for attempt in range(1, self.config.max_retries + 1):
            self._notify(project_id, STAGE_INTAKE, f"analyst drafting PRD (attempt {attempt})")
            prd, defects = None, []
            try:
                prd = self.analyst.run(
                    ContextPackage(
                        instructions=(
                            "Produce a PRD for the raw project idea below. Cover every "
                            "distinct capability as its own user story with testable "
                            "acceptance criteria.\n\n"
                            f"Raw idea:\n{raw_idea}"
                        ),
                        feedback=feedback,
                    ),
                    CostTags(project_id=project_id, stage=STAGE_INTAKE, agent="analyst"),
                )
            except AgentOutputError as exc:
                defects = exc.defects
            if prd is not None:
                defects = defects + validate_prd(prd)
            if attempt == 1:
                self.tracker.record("prd", not defects)
            if defects:
                feedback = "Validation defects:\n" + "\n".join(f"- {d}" for d in defects)
                self._notify(project_id, STAGE_INTAKE, f"PRD rejected by validation: {defects}")
                continue

            story_ids = [s["id"] for s in prd["user_stories"]]
            artifact = self.store.put(
                prd_id,
                artifact_type="prd",
                content=prd,
                created_by="analyst",
                project_id=project_id,
                trace_ids=story_ids,
                expected_version=latest_version or None,
            )
            latest_version = artifact.version
            self._notify(project_id, STAGE_GATE1, f"requesting human approval for PRD v{artifact.version}")
            decision = self.gate.request_approval(
                "Gate 1 — scope", self._gate1_digest(project_id, artifact)
            )
            if decision.approved:
                approved = self.store.set_status(prd_id, ArtifactStatus.APPROVED)
                self._notify(project_id, STAGE_GATE1, f"PRD v{approved.version} approved")
                return approved
            feedback = f"Human gate rejected the PRD: {decision.comments}"
            self._notify(project_id, STAGE_GATE1, f"PRD v{artifact.version} rejected: {decision.comments}")

        raise EscalationError(
            f"PRD for {project_id} not approved within {self.config.max_retries} attempts"
        )

    def _derive_tickets(self, project_id: str, prd_artifact: Artifact) -> list[dict]:
        """Mechanical derivation: one ticket per user story, trace IDs attached."""
        prd = self.store.get_for_consumption(prd_artifact.artifact_id).content
        tickets = []
        for index, story in enumerate(prd["user_stories"], start=1):
            ticket_id = f"TCK-{index:03d}"
            ticket = {
                "ticket_id": ticket_id,
                "story_id": story["id"],
                "description": story["story"],
                "acceptance_criteria": story["acceptance_criteria"],
            }
            self.store.put(
                f"{project_id}:{ticket_id}",
                artifact_type="ticket",
                content=ticket,
                created_by="orchestrator",
                project_id=project_id,
                trace_ids=[story["id"]],
            )
            self.store.set_status(f"{project_id}:{ticket_id}", ArtifactStatus.APPROVED)
            tickets.append(ticket)
        self._notify(project_id, STAGE_BUILD, f"derived {len(tickets)} tickets from PRD")
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
                            "ticket": ticket,
                            "implemented_files": [f["path"] for f in dev_out["files"]],
                            "developer_notes": dev_out.get("notes", ""),
                        },
                    ),
                    CostTags(project_id=project_id, stage=STAGE_QA, agent="qa", ticket_id=ticket_id),
                )
            except AgentOutputError as exc:
                qa_defects = exc.defects
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

        workspace.abandon_ticket_branch(branch)
        self._notify(
            project_id,
            STAGE_BUILD,
            f"{ticket_id} ESCALATED to human after {self.config.max_retries} attempts "
            f"(WIP left on {branch})",
        )
        return TicketResult(
            ticket_id=ticket_id,
            story_id=ticket["story_id"],
            status="escalated",
            attempts=self.config.max_retries,
            branch=branch,
            detail=last_failure,
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
            trace_ids=[ticket_id, ticket["story_id"]],
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

    def _gate1_digest(self, project_id: str, artifact: Artifact) -> str:
        prd = artifact.content
        n_stories = len(prd["user_stories"])
        n_criteria = sum(len(s["acceptance_criteria"]) for s in prd["user_stories"])
        stories = "\n".join(f"  - {s['id']}: {s['story']}" for s in prd["user_stories"])
        return (
            f"Project {project_id} — PRD v{artifact.version}: {prd['title']}\n"
            f"{prd['summary']}\n"
            f"{n_stories} user stories, {n_criteria} acceptance criteria:\n{stories}\n"
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
