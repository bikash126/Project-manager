"""Golden eval suites for the Phase 2 & 3 agents.

One golden PRD (a small URL-shortener project) drives cases for the Product
Owner, Estimator, Planner, and Architect (Phase 2); Phase 3 adds Reviewer and
Security cases. Checks reuse the orchestrator's handoff-verification functions
plus per-role plausibility bounds, so the suites express "expected shape"
without brittle exact matching.
"""

from __future__ import annotations

from pm_system.agents.base import ContextPackage
from pm_system.evals.harness import EvalCase
from pm_system.orchestrator.validation import (
    validate_architecture,
    validate_backlog,
    validate_review,
    validate_security_triage,
    validate_sprint_plan,
    validate_wbs,
)

GOLDEN_PRD = {
    "title": "Tiny URL Shortener",
    "summary": (
        "A small web service that shortens URLs, redirects visitors, and shows "
        "per-link click counts. Single-node, no accounts."
    ),
    "user_stories": [
        {
            "id": "US-001",
            "story": "As a visitor, I can submit a long URL and receive a short link.",
            "acceptance_criteria": [
                {
                    "id": "AC-001",
                    "given": "a valid http(s) URL",
                    "when": "I submit it",
                    "then": "I receive a short code of at most 8 characters",
                },
                {
                    "id": "AC-002",
                    "given": "an invalid URL such as 'notaurl'",
                    "when": "I submit it",
                    "then": "I get a 400 error with a message",
                },
            ],
        },
        {
            "id": "US-002",
            "story": "As a visitor, I am redirected when I open a short link.",
            "acceptance_criteria": [
                {
                    "id": "AC-003",
                    "given": "an existing short code",
                    "when": "I open it",
                    "then": "I am redirected (301) to the original URL",
                },
                {
                    "id": "AC-004",
                    "given": "an unknown short code",
                    "when": "I open it",
                    "then": "I get a 404",
                },
            ],
        },
        {
            "id": "US-003",
            "story": "As a link owner, I can see how many times my link was clicked.",
            "acceptance_criteria": [
                {
                    "id": "AC-005",
                    "given": "a short link that was opened 3 times",
                    "when": "I request its stats",
                    "then": "the click count is 3",
                },
            ],
        },
    ],
}

GOLDEN_STORY_IDS = {s["id"] for s in GOLDEN_PRD["user_stories"]}

# A golden WBS used as the Planner's input (MVP = US-001, US-002).
GOLDEN_WBS = {
    "wbs_items": [
        {
            "id": "WBS-001",
            "story_id": "US-001",
            "description": "URL validation + short-code generation + persistence",
            "estimate_points": 3,
            "confidence": {"low": 2, "high": 5},
        },
        {
            "id": "WBS-002",
            "story_id": "US-002",
            "description": "Redirect endpoint with 404 handling",
            "estimate_points": 2,
            "confidence": {"low": 1, "high": 3},
        },
    ],
    "risk_register": [
        {
            "id": "RISK-001",
            "description": "Short-code collisions under load",
            "likelihood": "low",
            "impact": "medium",
            "mitigation": "retry on collision; codes drawn from 62^8 space",
        }
    ],
    "total_points": 5,
}

GOLDEN_MVP_IDS = {"US-001", "US-002"}
GOLDEN_CAPACITY = 10


def product_owner_cases() -> list[EvalCase]:
    return [
        EvalCase(
            case_id="po-golden-prd",
            context=ContextPackage(
                instructions=(
                    "Prioritize the PRD's user stories, choose the MVP scope, and list "
                    "any stories to cut or defer. Rank every story.\n\n"
                    "Constraints:\nMVP budget covers roughly two-thirds of the scope; "
                    "stats/analytics are explicitly nice-to-have."
                ),
                artifacts={"prd": GOLDEN_PRD},
            ),
            checks=[
                ("handoff", lambda out: validate_backlog(out, GOLDEN_STORY_IDS)),
                (
                    "core-flow-in-mvp",
                    lambda out: []
                    if {"US-001", "US-002"} <= set(out["mvp_story_ids"])
                    else ["shorten (US-001) and redirect (US-002) must both be in the MVP"],
                ),
                (
                    "something-was-scoped-out",
                    lambda out: []
                    if len(out["mvp_story_ids"]) < len(GOLDEN_STORY_IDS) or out["cut_list"]
                    else ["with a two-thirds budget, not everything can be MVP"],
                ),
            ],
        )
    ]


def estimator_cases() -> list[EvalCase]:
    mvp_stories = [s for s in GOLDEN_PRD["user_stories"] if s["id"] in GOLDEN_MVP_IDS]
    return [
        EvalCase(
            case_id="estimator-golden-mvp",
            context=ContextPackage(
                instructions=(
                    "Break the MVP user stories below into a work breakdown structure "
                    "with three-point-style estimates (points plus a low/high confidence "
                    "range) and a risk register."
                ),
                artifacts={"mvp_user_stories": mvp_stories, "kb_calibration": []},
            ),
            checks=[
                ("handoff", lambda out: validate_wbs(out, GOLDEN_MVP_IDS)),
                (
                    "plausible-size",
                    lambda out: []
                    if 2 <= out["total_points"] <= 40
                    else [f"total {out['total_points']} points is implausible for a 2-story MVP"],
                ),
                (
                    "risks-identified",
                    lambda out: [] if out["risk_register"] else ["no risks identified"],
                ),
            ],
        )
    ]


def planner_cases() -> list[EvalCase]:
    return [
        EvalCase(
            case_id="planner-golden-wbs",
            context=ContextPackage(
                instructions=(
                    "Sequence the WBS items into sprints. Respect the per-sprint capacity, "
                    "declare dependencies between WBS items ('from' depends on 'to'), and "
                    "set milestones."
                ),
                artifacts={
                    "wbs": GOLDEN_WBS,
                    "capacity_points_per_sprint": GOLDEN_CAPACITY,
                },
            ),
            checks=[
                (
                    "handoff",
                    lambda out: validate_sprint_plan(
                        out, GOLDEN_WBS["wbs_items"], GOLDEN_CAPACITY
                    ),
                ),
                (
                    "redirect-depends-on-shorten",
                    lambda out: []
                    if {"from": "WBS-002", "to": "WBS-001"} in out["dependencies"]
                    else ["the redirect endpoint (WBS-002) depends on stored codes (WBS-001)"],
                ),
            ],
        )
    ]


def architect_cases() -> list[EvalCase]:
    mvp_stories = [s for s in GOLDEN_PRD["user_stories"] if s["id"] in GOLDEN_MVP_IDS]
    return [
        EvalCase(
            case_id="architect-golden-mvp",
            context=ContextPackage(
                instructions=(
                    "Design the system for the MVP user stories below: components with "
                    "responsibilities, ADRs with alternatives considered, API contracts, "
                    "and the data model. Every MVP story must be covered by a component."
                ),
                artifacts={"mvp_user_stories": mvp_stories, "kb_adrs": []},
            ),
            checks=[
                ("handoff", lambda out: validate_architecture(out, GOLDEN_MVP_IDS)),
                (
                    "storage-decision-made",
                    lambda out: []
                    if any(
                        "stor" in (adr["title"] + adr["decision"]).lower()
                        or "database" in (adr["title"] + adr["decision"]).lower()
                        or "persist" in (adr["title"] + adr["decision"]).lower()
                        for adr in out["adrs"]
                    )
                    else ["no ADR records how short links are persisted"],
                ),
                (
                    "contracts-exist",
                    lambda out: []
                    if out.get("api_contracts")
                    else ["a web service design needs at least one API contract"],
                ),
            ],
        )
    ]


def reviewer_cases() -> list[EvalCase]:
    insecure_diff = [
        {
            "path": "shortener/store.py",
            "content": (
                'API_KEY = "AKIAIOSFODNN7EXAMPLE1"\n\n'
                "def save(code, url):\n"
                "    # no validation of url\n"
                "    DB[code] = url\n"
            ),
        }
    ]
    clean_diff = [
        {
            "path": "shortener/codes.py",
            "content": (
                "import secrets\n\n"
                "def new_code(n: int = 7) -> str:\n"
                '    return secrets.token_urlsafe(n)[:n]\n'
            ),
        }
    ]
    standards = "Never hardcode secrets. Validate external input."
    return [
        EvalCase(
            case_id="reviewer-blocks-insecure",
            context=ContextPackage(
                instructions="Review this diff against the standards. Approve or block.",
                artifacts={"diff": insecure_diff, "coding_standards": standards},
            ),
            checks=[
                ("handoff", validate_review),
                (
                    "blocks-hardcoded-secret",
                    lambda out: []
                    if out["verdict"] == "block"
                    else ["a diff with a hardcoded AWS key must be blocked"],
                ),
            ],
        ),
        EvalCase(
            case_id="reviewer-approves-clean",
            context=ContextPackage(
                instructions="Review this diff against the standards. Approve or block.",
                artifacts={"diff": clean_diff, "coding_standards": standards},
            ),
            checks=[
                ("handoff", validate_review),
                (
                    "approves-clean-code",
                    lambda out: []
                    if out["verdict"] == "approve"
                    else ["clean, standards-compliant code should be approved"],
                ),
            ],
        ),
    ]


def security_cases() -> list[EvalCase]:
    findings = (
        "FND-001 [CRITICAL] regex-secrets:aws-access-key store.py:1 — hardcoded AWS key\n"
        "FND-002 [LOW] semgrep:style tests/test_store.py:10 — assert on constant"
    )
    return [
        EvalCase(
            case_id="security-confirms-real-secret",
            context=ContextPackage(
                instructions="Triage these findings. Confirmed or false_positive per finding.",
                artifacts={"findings": findings, "diff": []},
            ),
            checks=[
                ("handoff", lambda out: validate_security_triage(out, {"FND-001", "FND-002"})),
                (
                    "confirms-the-secret",
                    lambda out: []
                    if any(
                        t["finding_id"] == "FND-001" and t["status"] == "confirmed"
                        for t in out["triage"]
                    )
                    else ["a real hardcoded AWS key must be confirmed, not dismissed"],
                ),
            ],
        )
    ]


def cases_for(role: str) -> list[EvalCase]:
    suites = {
        "product_owner": product_owner_cases,
        "estimator": estimator_cases,
        "planner": planner_cases,
        "architect": architect_cases,
        "reviewer": reviewer_cases,
        "security": security_cases,
    }
    if role not in suites:
        raise KeyError(f"no golden suite for role {role!r}; have {sorted(suites)}")
    return suites[role]()
