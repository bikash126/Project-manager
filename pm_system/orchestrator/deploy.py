"""Deploy/rollback integration (design doc §4).

Only the Release Manager triggers a deploy, and only Ops (with human confirm)
triggers a rollback — credential scoping is enforced by which orchestrator
stage calls which method, not by prompt text.

NullDeployer records deploys/rollbacks in memory (the default). A real
implementation would drive GitHub Releases + a deploy pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Deployment:
    project_id: str
    version: str
    environment: str
    state: str = "deployed"  # deployed | rolled_back
    history: list[str] = field(default_factory=list)


class Deployer(ABC):
    @abstractmethod
    def deploy(self, *, project_id: str, version: str, environment: str, plan: str) -> Deployment:
        ...

    @abstractmethod
    def rollback(self, deployment: Deployment, *, reason: str) -> None:
        ...


class NullDeployer(Deployer):
    def __init__(self):
        self.deployments: list[Deployment] = []

    def deploy(self, *, project_id, version, environment, plan) -> Deployment:
        dep = Deployment(project_id=project_id, version=version, environment=environment)
        dep.history.append(f"deployed {version} to {environment}")
        self.deployments.append(dep)
        return dep

    def rollback(self, deployment: Deployment, *, reason: str) -> None:
        deployment.state = "rolled_back"
        deployment.history.append(f"rolled back: {reason}")
