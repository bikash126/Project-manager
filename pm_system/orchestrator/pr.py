"""Pull-request integration (design doc §4: real GitHub PRs).

Branch-per-ticket PRs carry the Security and Review verdicts; the orchestrator
opens a PR when a ticket enters the build loop, posts each stage's verdict, and
merges it when Review+QA pass (or closes it on escalation).

- NullPRPublisher: in-memory, the default — keeps the pipeline runnable
  offline and in tests, and records the same verdict trail.
- GitHubPRPublisher: talks to the GitHub REST API over stdlib urllib with a
  token. It is code-complete but requires a real remote + token, so it is not
  exercised in the offline test suite (only its request building is).
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Verdict:
    source: str  # "security" | "reviewer" | "qa"
    decision: str  # "pass"/"block"/"approve"/...
    detail: str = ""


@dataclass
class PullRequest:
    number: int
    ticket_id: str
    branch: str
    title: str
    url: str | None = None
    state: str = "open"  # open | merged | closed
    verdicts: list[Verdict] = field(default_factory=list)


class PRPublisher:
    def open_pr(self, *, ticket_id: str, branch: str, title: str, body: str) -> PullRequest:
        raise NotImplementedError

    def post_verdict(self, pr: PullRequest, verdict: Verdict) -> None:
        raise NotImplementedError

    def merge_pr(self, pr: PullRequest) -> None:
        raise NotImplementedError

    def close_pr(self, pr: PullRequest, reason: str) -> None:
        raise NotImplementedError


class NullPRPublisher(PRPublisher):
    """Records PRs and verdicts in memory; no network."""

    def __init__(self):
        self.prs: list[PullRequest] = []
        self._next = 1

    def open_pr(self, *, ticket_id, branch, title, body) -> PullRequest:
        pr = PullRequest(number=self._next, ticket_id=ticket_id, branch=branch, title=title)
        self._next += 1
        self.prs.append(pr)
        return pr

    def post_verdict(self, pr: PullRequest, verdict: Verdict) -> None:
        pr.verdicts.append(verdict)

    def merge_pr(self, pr: PullRequest) -> None:
        pr.state = "merged"

    def close_pr(self, pr: PullRequest, reason: str) -> None:
        pr.state = "closed"
        pr.verdicts.append(Verdict(source="orchestrator", decision="closed", detail=reason))


class GitHubPRPublisher(PRPublisher):
    """GitHub REST API implementation (requires the branch to be pushed).

    Assumes the ticket branches already exist on the remote (the orchestrator's
    GitWorkspace would need a push step wired in for a live run).
    """

    def __init__(self, *, owner: str, repo: str, token: str, base: str = "main",
                 api_root: str = "https://api.github.com"):
        self.owner = owner
        self.repo = repo
        self.token = token
        self.base = base
        self.api_root = api_root

    # -- request building is factored out so it can be unit-tested offline --

    def _open_payload(self, *, branch: str, title: str, body: str) -> dict:
        return {"title": title, "head": branch, "base": self.base, "body": body}

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.api_root}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.github+json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:  # pragma: no cover - network
            return json.loads(response.read() or "{}")

    def open_pr(self, *, ticket_id, branch, title, body) -> PullRequest:  # pragma: no cover - network
        data = self._request(
            "POST", f"/repos/{self.owner}/{self.repo}/pulls",
            self._open_payload(branch=branch, title=title, body=body),
        )
        return PullRequest(
            number=data["number"], ticket_id=ticket_id, branch=branch,
            title=title, url=data.get("html_url"),
        )

    def post_verdict(self, pr, verdict) -> None:  # pragma: no cover - network
        pr.verdicts.append(verdict)
        self._request(
            "POST", f"/repos/{self.owner}/{self.repo}/issues/{pr.number}/comments",
            {"body": f"**{verdict.source}**: {verdict.decision}\n\n{verdict.detail}"},
        )

    def merge_pr(self, pr) -> None:  # pragma: no cover - network
        self._request("PUT", f"/repos/{self.owner}/{self.repo}/pulls/{pr.number}/merge", {})
        pr.state = "merged"

    def close_pr(self, pr, reason) -> None:  # pragma: no cover - network
        self._request(
            "POST", f"/repos/{self.owner}/{self.repo}/issues/{pr.number}/comments",
            {"body": f"Closing: {reason}"},
        )
        self._request(
            "PATCH", f"/repos/{self.owner}/{self.repo}/pulls/{pr.number}", {"state": "closed"}
        )
        pr.state = "closed"
