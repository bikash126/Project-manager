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


@dataclass(frozen=True)
class EgressPolicy:
    """Network egress policy for sandboxed tasks (design doc §4).

    - "none": no network at all (default).
    - "full": unrestricted bridge networking — dev/debug only.
    - "allowlist": task containers run on an internal Docker network whose
      only way out is a sidecar proxy that enforces `allowed_hosts`
      (see egress_proxy.py). Direct egress is impossible because internal
      networks have no external route.
    """

    mode: str = "none"  # none | full | allowlist
    allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def none(cls) -> "EgressPolicy":
        return cls(mode="none")

    @classmethod
    def full(cls) -> "EgressPolicy":
        return cls(mode="full")

    @classmethod
    def allowlist(cls, hosts: list[str] | tuple[str, ...]) -> "EgressPolicy":
        if not hosts:
            raise ValueError("allowlist mode needs at least one host; use EgressPolicy.none()")
        return cls(mode="allowlist", allowed_hosts=tuple(hosts))


INTERNAL_NETWORK = "pm-sandbox-internal"
PROXY_PORT = 8888
_PROXY_SCRIPT = Path(__file__).resolve().parent / "egress_proxy.py"


class DockerSandbox(Sandbox):
    def __init__(
        self,
        image: str = "pm-sandbox:latest",
        *,
        egress: EgressPolicy | None = None,
        memory: str = "1g",
        cpus: float = 1.0,
    ):
        self.image = image
        self.egress = egress or EgressPolicy.none()
        self.memory = memory
        self.cpus = cpus

    def _proxy_name(self) -> str:
        digest = uuid.uuid5(uuid.NAMESPACE_URL, ",".join(self.egress.allowed_hosts)).hex[:8]
        return f"pm-egress-proxy-{digest}"

    def _run_args(self, command: list[str], workdir: Path, env: dict[str, str], name: str) -> list[str]:
        docker_cmd = [
            "docker", "run", "--rm",
            "--name", name,
            "--memory", self.memory,
            f"--cpus={self.cpus}",
            "-v", f"{Path(workdir).resolve()}:/workspace",
            "-w", "/workspace",
        ]
        merged_env = dict(env)
        if self.egress.mode == "none":
            docker_cmd += ["--network", "none"]
        elif self.egress.mode == "full":
            docker_cmd += ["--network", "bridge"]
        else:
            proxy_url = f"http://{self._proxy_name()}:{PROXY_PORT}"
            docker_cmd += ["--network", INTERNAL_NETWORK]
            merged_env.update(
                HTTP_PROXY=proxy_url, HTTPS_PROXY=proxy_url,
                http_proxy=proxy_url, https_proxy=proxy_url,
            )
        for key, value in merged_env.items():
            docker_cmd += ["-e", f"{key}={value}"]
        docker_cmd.append(self.image)
        docker_cmd += command
        return docker_cmd

    def _ensure_allowlist_infra(self) -> None:
        """Create the internal network and the allowlisting proxy sidecar."""
        inspect = subprocess.run(
            ["docker", "network", "inspect", INTERNAL_NETWORK], capture_output=True
        )
        if inspect.returncode != 0:
            subprocess.run(
                ["docker", "network", "create", "--internal", INTERNAL_NETWORK],
                capture_output=True, check=True,
            )
        proxy = self._proxy_name()
        running = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", proxy], capture_output=True, text=True
        )
        if running.returncode == 0 and running.stdout.strip() == "true":
            return
        subprocess.run(["docker", "rm", "-f", proxy], capture_output=True)
        subprocess.run(
            [
                "docker", "run", "-d", "--name", proxy,
                "--network", INTERNAL_NETWORK,
                "-v", f"{_PROXY_SCRIPT}:/egress_proxy.py:ro",
                "-e", f"ALLOWED_HOSTS={','.join(self.egress.allowed_hosts)}",
                "python:3.12-slim", "python", "/egress_proxy.py",
            ],
            capture_output=True, check=True,
        )
        # Second leg: the bridge network gives the proxy (and only the proxy)
        # a route out of the internal network.
        subprocess.run(
            ["docker", "network", "connect", "bridge", proxy], capture_output=True, check=True
        )

    def run(self, command, *, workdir, timeout=300, env=None) -> SandboxResult:
        if self.egress.mode == "allowlist":
            self._ensure_allowlist_infra()
        name = f"pm-sandbox-{uuid.uuid4().hex[:12]}"
        docker_cmd = self._run_args(command, Path(workdir), env or {}, name)

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
