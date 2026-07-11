"""Branch-per-ticket git workspace for the project being built.

Wraps the git CLI. When git isn't available, NullGitWorkspace keeps the
pipeline functional (files only, no history) — fine for tests, not for
real runs.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class GitWorkspace:
    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._run("init")
        self._run("config", "user.name", "pm-orchestrator")
        self._run("config", "user.email", "pm-orchestrator@localhost")
        self._run("checkout", "-B", "main")

    def start_ticket_branch(self, name: str) -> None:
        self._run("checkout", "main")
        self._run("checkout", "-B", name)

    def commit_all(self, message: str) -> bool:
        self._run("add", "-A")
        status = self._run("status", "--porcelain")
        if not status.strip():
            return False
        self._run("commit", "-m", message)
        return True

    def merge_ticket_branch(self, name: str) -> None:
        self._run("checkout", "main")
        self._run("merge", "--no-ff", "-m", f"merge {name}", name)

    def abandon_ticket_branch(self, name: str) -> None:
        """Leave the WIP on its branch and restore main in the working tree."""
        self.commit_all(f"wip: {name} (escalated to human)")
        self._run("checkout", "main")

    def log_oneline(self) -> str:
        return self._run("log", "--oneline", "--all")

    def _run(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
        return proc.stdout


class NullGitWorkspace(GitWorkspace):
    """No-op stand-in when git is unavailable."""

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def start_ticket_branch(self, name: str) -> None:
        pass

    def commit_all(self, message: str) -> bool:
        return False

    def merge_ticket_branch(self, name: str) -> None:
        pass

    def abandon_ticket_branch(self, name: str) -> None:
        pass

    def log_oneline(self) -> str:
        return ""
