"""Multi-project concurrency (design doc §7, Phase 7).

Runs several projects at once, each on its own Orchestrator instance (so the
per-run state never collides), while they share the thread-safe artifact store,
cost ledger, and Knowledge Base. A ProjectJob pairs an orchestrator with its
inputs; run_projects executes them on a thread pool and returns the results
keyed by project id.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


@dataclass
class ProjectJob:
    orchestrator: object  # Orchestrator
    project_id: str
    raw_idea: str
    constraints: str = ""


def run_projects(jobs: list[ProjectJob], *, max_workers: int | None = None) -> dict:
    """Run every job concurrently; return {project_id: ProjectResult}."""
    results: dict[str, object] = {}
    workers = max_workers or min(len(jobs), 8) or 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                job.orchestrator.run_project, job.project_id, job.raw_idea, job.constraints
            ): job
            for job in jobs
        }
        for future, job in futures.items():
            results[job.project_id] = future.result()
    return results
