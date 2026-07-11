import pytest

from pm_system.artifacts.store import ArtifactStatus, ArtifactStore
from pm_system.errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    InvalidTransitionError,
    StaleArtifactError,
)


@pytest.fixture
def store():
    return ArtifactStore()


def _put(store, artifact_id="p1:PRD", expected_version=None, content=None):
    return store.put(
        artifact_id,
        artifact_type="prd",
        content=content or {"title": "x"},
        created_by="analyst",
        project_id="p1",
        trace_ids=["US-001"],
        expected_version=expected_version,
    )


def test_put_and_get(store):
    artifact = _put(store)
    assert artifact.version == 1
    assert artifact.status == ArtifactStatus.DRAFT
    assert store.get("p1:PRD").content == {"title": "x"}
    assert store.get("p1:PRD").trace_ids == ("US-001",)


def test_new_version_supersedes_previous(store):
    _put(store)
    v2 = _put(store, expected_version=1, content={"title": "y"})
    assert v2.version == 2
    assert store.get("p1:PRD", version=1).status == ArtifactStatus.SUPERSEDED
    history = store.history("p1:PRD")
    assert [a.version for a in history] == [1, 2]


def test_optimistic_lock_conflict(store):
    _put(store)
    _put(store, expected_version=1)
    with pytest.raises(ArtifactConflictError):
        _put(store, expected_version=1)  # stale writer


def test_update_without_expected_version_is_refused(store):
    _put(store)
    with pytest.raises(ArtifactConflictError):
        _put(store)


def test_status_lifecycle(store):
    _put(store)
    assert store.set_status("p1:PRD", ArtifactStatus.APPROVED).status == ArtifactStatus.APPROVED
    assert store.mark_stale("p1:PRD").status == ArtifactStatus.STALE
    assert (
        store.set_status("p1:PRD", ArtifactStatus.SUPERSEDED).status
        == ArtifactStatus.SUPERSEDED
    )


def test_invalid_transitions(store):
    _put(store)
    with pytest.raises(InvalidTransitionError):
        store.mark_stale("p1:PRD")  # draft -> stale not allowed
    store.set_status("p1:PRD", ArtifactStatus.APPROVED)
    with pytest.raises(InvalidTransitionError):
        store.set_status("p1:PRD", ArtifactStatus.DRAFT)


def test_agents_refuse_stale_inputs(store):
    _put(store)
    store.set_status("p1:PRD", ArtifactStatus.APPROVED)
    assert store.get_for_consumption("p1:PRD").version == 1
    store.mark_stale("p1:PRD")
    with pytest.raises(StaleArtifactError):
        store.get_for_consumption("p1:PRD")


def test_missing_artifact(store):
    with pytest.raises(ArtifactNotFoundError):
        store.get("nope")


def test_list_project_returns_latest_versions(store):
    _put(store)
    _put(store, expected_version=1)
    _put(store, artifact_id="p1:TCK-001")
    artifacts = store.list_project("p1")
    assert [(a.artifact_id, a.version) for a in artifacts] == [
        ("p1:PRD", 2),
        ("p1:TCK-001", 1),
    ]
