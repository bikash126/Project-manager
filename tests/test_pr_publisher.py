"""PR publisher: the in-memory default and GitHub request building (offline)."""

from pm_system.orchestrator.pr import GitHubPRPublisher, NullPRPublisher, Verdict


def test_null_publisher_lifecycle():
    pub = NullPRPublisher()
    pr = pub.open_pr(ticket_id="TCK-001", branch="ticket/TCK-001", title="t", body="b")
    assert pr.number == 1 and pr.state == "open"
    pub.post_verdict(pr, Verdict("security", "pass"))
    pub.post_verdict(pr, Verdict("reviewer", "approve"))
    pub.merge_pr(pr)
    assert pr.state == "merged"
    assert [v.source for v in pr.verdicts] == ["security", "reviewer"]

    pr2 = pub.open_pr(ticket_id="TCK-002", branch="ticket/TCK-002", title="t2", body="b2")
    assert pr2.number == 2  # sequential


def test_null_publisher_close_records_reason():
    pub = NullPRPublisher()
    pr = pub.open_pr(ticket_id="TCK-001", branch="b", title="t", body="b")
    pub.close_pr(pr, "escalated after 3 attempts")
    assert pr.state == "closed"
    assert any("escalated" in v.detail for v in pr.verdicts)


def test_github_open_payload():
    pub = GitHubPRPublisher(owner="acme", repo="widget", token="tok", base="main")
    payload = pub._open_payload(branch="ticket/TCK-001", title="add widget", body="closes US-001")
    assert payload == {
        "title": "add widget",
        "head": "ticket/TCK-001",
        "base": "main",
        "body": "closes US-001",
    }
