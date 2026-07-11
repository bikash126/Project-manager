"""Status digests emitted on every stage transition (design doc §1)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    @abstractmethod
    def notify(self, project_id: str, stage: str, message: str) -> None:
        ...


class NullNotifier(Notifier):
    def notify(self, project_id: str, stage: str, message: str) -> None:
        pass


class ConsoleNotifier(Notifier):
    def notify(self, project_id: str, stage: str, message: str) -> None:
        print(f"[{project_id}] {stage}: {message}")


class SlackNotifier(Notifier):
    def __init__(self, *, token: str, channel: str):
        try:
            from slack_sdk import WebClient
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "SlackNotifier requires the 'slack_sdk' package: pip install pm-system[slack]"
            ) from exc
        self._client = WebClient(token=token)
        self.channel = channel

    def notify(self, project_id: str, stage: str, message: str) -> None:
        self._client.chat_postMessage(
            channel=self.channel, text=f"*{project_id}* · `{stage}`\n{message}"
        )
