"""Sandbox platform (design doc §1/§4).

DockerSandbox is the real thing: docker-per-task, no network egress by
default, memory/CPU caps, workspace bind-mounted at /workspace, no other
host access. Build the image from sandbox/Dockerfile so pytest is available
inside (the default network=none blocks pip installs at run time).

LocalSandbox is a development fallback for machines without Docker — plain
subprocesses with a timeout and a scrubbed environment. It provides NO
isolation and must not be used with untrusted code in production.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


class Sandbox(ABC):
    @abstractmethod
    def run(
        self,
        command: list[str],
        *,
        workdir: Path,
        timeout: int = 300,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        ...


class DockerSandbox(Sandbox):
    def __init__(
        self,
        image: str = "pm-sandbox:latest",
        *,
        network: str = "none",
        memory: str = "1g",
        cpus: float = 1.0,
    ):
        self.image = image
        self.network = network
        self.memory = memory
        self.cpus = cpus

    def run(self, command, *, workdir, timeout=300, env=None) -> SandboxResult:
        name = f"pm-sandbox-{uuid.uuid4().hex[:12]}"
        docker_cmd = [
            "docker", "run", "--rm",
            "--name", name,
            "--network", self.network,
            "--memory", self.memory,
            f"--cpus={self.cpus}",
            "-v", f"{Path(workdir).resolve()}:/workspace",
            "-w", "/workspace",
        ]
        for key, value in (env or {}).items():
            docker_cmd += ["-e", f"{key}={value}"]
        docker_cmd.append(self.image)
        docker_cmd += command

        start = time.monotonic()
        try:
            proc = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            return SandboxResult(
                exit_code=124,
                stdout=(exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr=f"timed out after {timeout}s",
                duration_s=time.monotonic() - start,
                timed_out=True,
            )
        return SandboxResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_s=time.monotonic() - start,
        )


class LocalSandbox(Sandbox):
    """Unisolated fallback for development environments without Docker."""

    def run(self, command, *, workdir, timeout=300, env=None) -> SandboxResult:
        base_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        base_env.update(env or {})
        start = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                cwd=workdir,
                env=base_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            def _text(stream):
                if stream is None:
                    return ""
                return stream.decode() if isinstance(stream, bytes) else stream

            return SandboxResult(
                exit_code=124,
                stdout=_text(exc.stdout),
                stderr=_text(exc.stderr) + f"\ntimed out after {timeout}s",
                duration_s=time.monotonic() - start,
                timed_out=True,
            )
        return SandboxResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_s=time.monotonic() - start,
        )


def _docker_daemon_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def default_sandbox() -> Sandbox:
    """Docker when a daemon responds (unless PM_SANDBOX=local), local fallback otherwise."""
    if os.environ.get("PM_SANDBOX") != "local" and _docker_daemon_available():
        return DockerSandbox()
    return LocalSandbox()


@dataclass(frozen=True)
class TestReport:
    passed: bool
    exit_code: int
    summary: str


class TestRunner:
    """Runs the workspace's pytest suite inside a sandbox."""

    def __init__(self, sandbox: Sandbox, *, python: str = "python", timeout: int = 300):
        self.sandbox = sandbox
        self.python = python
        self.timeout = timeout

    def run(self, workdir: Path) -> TestReport:
        result = self.sandbox.run(
            [self.python, "-m", "pytest", "-q", "--color=no", "-p", "no:cacheprovider"],
            workdir=workdir,
            timeout=self.timeout,
        )
        summary = result.output[-4000:]
        if result.timed_out:
            summary = f"test run timed out\n{summary}"
        return TestReport(passed=result.exit_code == 0, exit_code=result.exit_code, summary=summary)
