"""
Tests for /api/v1/jobs endpoints.

Background tasks in TestClient execute synchronously before the HTTP response
is returned, so a job started via POST is already completed by the time the
202 lands. Tests that need a pending/failed/cancelled job insert one directly
via _insert_job() to bypass that constraint.

run.run_pipeline is patched to a no-op for every test — no SSH, no targets,
empty results list.
"""
import pytest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from src.api.api import app
from src.api import utils
from src.classes.connectors import Manager
from src.core.database import Job, JobStatus

PREFIX = "/api/v1/jobs"
PIPELINE_NAME = "curl"  # present in tests/fixtures/pipelines/fk.yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fake_run_pipeline(pipeline, manager, on_result=None, should_stop=None):
    """No-op replacement for run_pipeline — no SSH, no targets, empty results."""
    return []


@pytest.fixture()
def db_engine(monkeypatch):
    # StaticPool keeps a single connection alive so the in-memory DB persists
    # across all Session() calls made during a test.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    import src.core.jobs as jobs_module
    import src.core.database as db_module
    monkeypatch.setattr(jobs_module, "engine", engine)
    monkeypatch.setattr(db_module, "engine", engine)
    return engine


@pytest.fixture()
def client(db_engine):
    manager = Manager(autoload=False)
    app.dependency_overrides[utils.get_manager] = lambda: manager
    with patch("src.core.run.run_pipeline", side_effect=_fake_run_pipeline):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clear_cancelled():
    import src.core.jobs as jobs_module
    jobs_module._cancelled.clear()
    yield
    jobs_module._cancelled.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_job(db_engine, pipeline_name=PIPELINE_NAME, status=JobStatus.pending) -> UUID:
    """Insert a job directly into the DB, bypassing the API."""
    with Session(db_engine) as session:
        job = Job(pipeline_name=pipeline_name, status=status)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


def _start_job(client, name=PIPELINE_NAME) -> UUID:
    r = client.post(f"{PREFIX}/{name}")
    assert r.status_code == 202
    return UUID(r.json()["job_id"])


# ---------------------------------------------------------------------------
# POST /jobs/{name} — start a job
# ---------------------------------------------------------------------------

class TestStartJob:
    def test_returns_202_and_uuid(self, client):
        r = client.post(f"{PREFIX}/{PIPELINE_NAME}")
        assert r.status_code == 202
        UUID(r.json()["job_id"])  # raises if not a valid UUID

    def test_unknown_pipeline_returns_404(self, client):
        r = client.post(f"{PREFIX}/nonexistent")
        assert r.status_code == 404

    def test_job_appears_in_list(self, client):
        job_id = _start_job(client)
        ids = [j["id"] for j in client.get(f"{PREFIX}/").json()]
        assert str(job_id) in ids

    def test_job_status_is_completed(self, client):
        job_id = _start_job(client)
        r = client.get(f"{PREFIX}/{job_id}")
        assert r.json()["status"] == "completed"

    def test_completed_job_results_empty_when_no_targets(self, client):
        job_id = _start_job(client)
        r = client.get(f"{PREFIX}/{job_id}")
        assert r.json()["results"] == []


# ---------------------------------------------------------------------------
# GET /jobs/{job_id} — single job
# ---------------------------------------------------------------------------

class TestGetJob:
    def test_found(self, client):
        job_id = _start_job(client)
        assert client.get(f"{PREFIX}/{job_id}").status_code == 200

    def test_not_found(self, client):
        assert client.get(f"{PREFIX}/{uuid4()}").status_code == 404

    def test_response_shape(self, client):
        job_id = _start_job(client)
        body = client.get(f"{PREFIX}/{job_id}").json()
        assert body["id"] == str(job_id)
        assert body["pipeline_name"] == PIPELINE_NAME
        assert "status" in body
        assert "created_at" in body
        assert isinstance(body["results"], list)


# ---------------------------------------------------------------------------
# GET /jobs/ — list all jobs
# ---------------------------------------------------------------------------

class TestListJobs:
    def test_empty_initially(self, client):
        r = client.get(f"{PREFIX}/")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_all_started_jobs(self, client):
        _start_job(client)
        _start_job(client)
        assert len(client.get(f"{PREFIX}/").json()) == 2

    def test_summary_shape(self, client):
        _start_job(client)
        item = client.get(f"{PREFIX}/").json()[0]
        assert "id" in item
        assert "pipeline_name" in item
        assert "status" in item
        assert "created_at" in item
        assert "results" not in item  # summary only, no step detail

    def test_sorted_newest_first(self, client):
        id1 = str(_start_job(client))
        id2 = str(_start_job(client))
        ids = [j["id"] for j in client.get(f"{PREFIX}/").json()]
        assert ids.index(id2) < ids.index(id1)


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/cancel
# ---------------------------------------------------------------------------

class TestCancelJob:
    def test_cancel_pending_job(self, client, db_engine):
        job_id = _insert_job(db_engine, status=JobStatus.pending)
        assert client.post(f"{PREFIX}/{job_id}/cancel").status_code == 204

    def test_cancel_sets_status_to_cancelled(self, client, db_engine):
        job_id = _insert_job(db_engine, status=JobStatus.pending)
        client.post(f"{PREFIX}/{job_id}/cancel")
        with Session(db_engine) as session:
            assert session.get(Job, job_id).status == JobStatus.cancelled

    def test_cancel_completed_job_returns_409(self, client):
        job_id = _start_job(client)  # ends as completed
        assert client.post(f"{PREFIX}/{job_id}/cancel").status_code == 409

    def test_cancel_already_cancelled_returns_409(self, client, db_engine):
        job_id = _insert_job(db_engine, status=JobStatus.cancelled)
        assert client.post(f"{PREFIX}/{job_id}/cancel").status_code == 409

    def test_cancel_failed_job_returns_409(self, client, db_engine):
        job_id = _insert_job(db_engine, status=JobStatus.failed)
        assert client.post(f"{PREFIX}/{job_id}/cancel").status_code == 409

    def test_cancel_nonexistent_returns_409(self, client):
        assert client.post(f"{PREFIX}/{uuid4()}/cancel").status_code == 409


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/retry
# ---------------------------------------------------------------------------

class TestRetryJob:
    def test_retry_failed_job_returns_202(self, client, db_engine):
        job_id = _insert_job(db_engine, status=JobStatus.failed)
        assert client.post(f"{PREFIX}/{job_id}/retry").status_code == 202

    def test_retry_cancelled_job_returns_202(self, client, db_engine):
        job_id = _insert_job(db_engine, status=JobStatus.cancelled)
        assert client.post(f"{PREFIX}/{job_id}/retry").status_code == 202

    def test_retry_returns_new_job_id(self, client, db_engine):
        job_id = _insert_job(db_engine, status=JobStatus.failed)
        new_id = UUID(client.post(f"{PREFIX}/{job_id}/retry").json()["job_id"])
        assert new_id != job_id

    def test_retry_completed_job_returns_409(self, client):
        job_id = _start_job(client)  # ends as completed
        assert client.post(f"{PREFIX}/{job_id}/retry").status_code == 409

    def test_retry_pending_job_returns_409(self, client, db_engine):
        job_id = _insert_job(db_engine, status=JobStatus.pending)
        assert client.post(f"{PREFIX}/{job_id}/retry").status_code == 409

    def test_retry_nonexistent_returns_409(self, client):
        assert client.post(f"{PREFIX}/{uuid4()}/retry").status_code == 409

    def test_retry_new_job_appears_in_list(self, client, db_engine):
        job_id = _insert_job(db_engine, status=JobStatus.failed)
        new_id = client.post(f"{PREFIX}/{job_id}/retry").json()["job_id"]
        ids = [j["id"] for j in client.get(f"{PREFIX}/").json()]
        assert new_id in ids

    def test_retry_new_job_has_same_pipeline(self, client, db_engine):
        job_id = _insert_job(db_engine, status=JobStatus.failed)
        new_id = client.post(f"{PREFIX}/{job_id}/retry").json()["job_id"]
        body = client.get(f"{PREFIX}/{new_id}").json()
        assert body["pipeline_name"] == PIPELINE_NAME
