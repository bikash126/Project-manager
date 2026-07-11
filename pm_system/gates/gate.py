"""Human gates (design doc §3, D4).

Phase 1 ships one gate — Gate 1 (scope) — delivered via Slack: the
orchestrator posts a digest and a human replies "approve" or
"reject <reason>" in the thread. Rejection reasons are routed back to the
responsible agent as a revision task.

ConsoleGate and AutoApproveGate exist for local runs and tests.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class GateDecision:
    approved: bool
    comments: str = ""


class HumanGate(ABC):
    @abstractmethod
    def request_approval(self, gate_name: str, digest: str) -> GateDecision:
        ...


class AutoApproveGate(HumanGate):
    def request_approval(self, gate_name: str, digest: str) -> GateDecision:
        return GateDecision(approved=True, comments="auto-approved")


class ConsoleGate(HumanGate):
    def request_approval(self, gate_name: str, digest: str) -> GateDecision:
        print(f"\n=== {gate_name} ===\n{digest}\n")
        answer = input("approve? [y/N, anything else is treated as a rejection reason]: ").strip()
        if answer.lower() in ("y", "yes", "approve"):
            return GateDecision(approved=True)
        return GateDecision(approved=False, comments=answer or "rejected at console gate")


class SlackGate(HumanGate):
    """Posts the digest to a channel and polls the thread for a decision.

    A threaded reply starting with "approve" approves; one starting with
    "reject" rejects, with the rest of the message as the reason.
    """

    def __init__(self, *, token: str, channel: str, timeout_s: int = 3600, poll_s: int = 15):
        try:
            from slack_sdk import WebClient
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "SlackGate requires the 'slack_sdk' package: pip install pm-system[slack]"
            ) from exc
        self._client = WebClient(token=token)
        self.channel = channel
        self.timeout_s = timeout_s
        self.poll_s = poll_s

    def request_approval(self, gate_name: str, digest: str) -> GateDecision:
        message = (
            f":vertical_traffic_light: *{gate_name}*\n{digest}\n\n"
            "Reply in this thread with `approve` or `reject <reason>`."
        )
        posted = self._client.chat_postMessage(channel=self.channel, text=message)
        thread_ts = posted["ts"]
        deadline = time.monotonic() + self.timeout_s

        while time.monotonic() < deadline:
            replies = self._client.conversations_replies(channel=self.channel, ts=thread_ts)
            for msg in replies.get("messages", [])[1:]:
                text = (msg.get("text") or "").strip()
                lowered = text.lower()
                if lowered.startswith("approve"):
                    return GateDecision(approved=True, comments=text)
                if lowered.startswith("reject"):
                    reason = text[len("reject"):].strip(" :-") or "rejected via Slack"
                    return GateDecision(approved=False, comments=reason)
            time.sleep(self.poll_s)

        return GateDecision(approved=False, comments=f"gate timed out after {self.timeout_s}s")
