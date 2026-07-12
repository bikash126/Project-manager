"""Branch-per-ticket git workspace for the project being built.

Each ticket runs in its own git **worktree** (an isolated working directory on
its own branch), so tickets in the same dependency wave can be built in
parallel without colliding (Phase 7). Integration is serialized: branches merge
into `main` one at a time, and merge conflicts are surfaced for a resolver.

When git isn't available, NullGitWorkspace keeps the pipeline functional
(files only, single shared dir, no isolation) — fine for tests, not real runs.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass
class MergeResult:
    ok: bool
    conflicted_files: list[str] = field(default_factory=list)


class Worktree:
    """An isolated working directory checked out on a ticket branch."""

    def __init__(self, repo: "GitWorkspace", root: Path, branch: str):
        self._repo = repo
        self.root = Path(root)
        self.branch = branch

    def commit_all(self, message: str) -> bool:
        self._git("add", "-A")
        if not self._git("status", "--porcelain").strip():
            return False
        self._git("commit", "-m", message)
        return True

    def _git(self, *args: str) -> str:
        return self._repo._run_in(self.root, *args)


class GitWorkspace:
    main_branch = "main"

    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    @property
    def isolates(self) -> bool:
        return True

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._run("init")
        self._run("config", "user.name", "pm-orchestrator")
        self._run("config", "user.email", "pm-orchestrator@localhost")
        self._run("checkout", "-B", self.main_branch)

    def commit_all(self, message: str) -> bool:
        self._run("add", "-A")
        if not self._run("status", "--porcelain").strip():
            return False
        self._run("commit", "-m", message)
        return True

    # -- worktree lifecycle (Phase 7 parallel dev) -----------------------

    def create_worktree(self, branch: str) -> Worktree:
        path = self.root / ".worktrees" / branch.replace("/", "_")
        # Clear any stale worktree/branch checkout, then create fresh from main.
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=self.root, capture_output=True, text=True,
        )
        self._run("worktree", "prune")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._run("worktree", "add", "-f", "-B", branch, str(path), self.main_branch)
        return Worktree(self, path, branch)

    def remove_worktree(self, wt: Worktree) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt.root)],
            cwd=self.root, capture_output=True, text=True,
        )
        self._run("worktree", "prune")

    def merge(self, branch: str) -> MergeResult:
        """Merge a ticket branch into main. Detects conflicts without raising."""
        self._run("checkout", self.main_branch)
        proc = subprocess.run(
            ["git", "merge", "--no-ff", "-m", f"merge {branch}", branch],
            cwd=self.root, capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return MergeResult(ok=True)
        conflicted = self._run("diff", "--name-only", "--diff-filter=U").split()
        return MergeResult(ok=False, conflicted_files=conflicted)

    def complete_merge(self, message: str) -> None:
        self._run("add", "-A")
        self._run("commit", "-m", message)

    def abort_merge(self) -> None:
        subprocess.run(
            ["git", "merge", "--abort"], cwd=self.root, capture_output=True, text=True
        )

    def abandon(self, branch: str) -> None:
        """Leave an escalated ticket's branch for a human; keep main clean."""
        self._run("checkout", self.main_branch)

    def log_oneline(self) -> str:
        return self._run("log", "--oneline", "--all")

    def _run(self, *args: str) -> str:
        return self._run_in(self.root, *args)

    @staticmethod
    def _run_in(cwd: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
        return proc.stdout


class NullGitWorkspace(GitWorkspace):
    """No-op stand-in when git is unavailable (single shared dir, no isolation)."""

    @property
    def isolates(self) -> bool:
        return False

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def commit_all(self, message: str) -> bool:
        return False

    def create_worktree(self, branch: str) -> Worktree:
        return Worktree(self, self.root, branch)

    def remove_worktree(self, wt: Worktree) -> None:
        pass

    def merge(self, branch: str) -> MergeResult:
        return MergeResult(ok=True)

    def complete_merge(self, message: str) -> None:
        pass

    def abort_merge(self) -> None:
        pass

    def abandon(self, branch: str) -> None:
        pass

    def log_oneline(self) -> str:
        return ""


class _NullWorktree(Worktree):
    def commit_all(self, message: str) -> bool:
        return False


# NullGitWorkspace worktrees must not shell out to git.
def _null_create_worktree(self, branch: str) -> Worktree:  # pragma: no cover - trivial
    return _NullWorktree(self, self.root, branch)


NullGitWorkspace.create_worktree = _null_create_worktree
