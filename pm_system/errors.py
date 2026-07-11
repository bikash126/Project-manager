"""Shared exception types for the PM system."""


class PMSystemError(Exception):
    """Base class for all PM-system errors."""


class ArtifactNotFoundError(PMSystemError):
    pass


class ArtifactConflictError(PMSystemError):
    """Optimistic-lock violation: the artifact changed under the writer.

    Per design decision D8, write conflicts are never silently resolved —
    they escalate to the orchestrator.
    """


class StaleArtifactError(PMSystemError):
    """An agent tried to consume an artifact that is stale or superseded."""


class InvalidTransitionError(PMSystemError):
    pass


class BudgetExceededError(PMSystemError):
    """A per-stage or per-project cost cap was hit; hard stop."""


class EscalationError(PMSystemError):
    """A retry cap was exhausted; a human has to take over."""


class UnsafePathError(PMSystemError):
    pass


class AgentOutputError(PMSystemError):
    """Agent output failed JSON parsing or schema validation."""

    def __init__(self, defects):
        self.defects = list(defects)
        super().__init__("; ".join(self.defects))
