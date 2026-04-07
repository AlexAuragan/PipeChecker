"""
Tests for /api/v1/pipelines endpoints.

The root conftest.py `isolated_config` fixture redirects all storage paths
to a tmp_path tree and seeds it with fixtures/pipelines/*.yaml, so every
test reads/writes to a throwaway directory — no real save/ folder is touched.

Fixture pipeline file (fk.yaml) contains two pipelines:
  - "curl"        : one step  (curl-installed)
  - "File-keeper" : one step  (fk-installed)
"""

import pytest
from fastapi.testclient import TestClient

from src.api.api import app

PREFIX = "/api/v1/pipelines"


# ---------------------------------------------------------------------------
# Shared client
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(step_id="new-step", exec_cmd="which bash",
          check_method="stdout_not_empty", if_failed=None, requires=None):
    body = {"id": step_id, "exec": exec_cmd, "check_method": check_method, "if_failed": if_failed}
    if requires:
        body["requires"] = requires
    return body


def _pipeline(name="my-pipe", steps=None, connectors=None, runner="proxmox_ct"):
    return {
        "name": name,
        "pipeline": steps or [_step()],
        "connectors": connectors or [],
        "runner": runner,
    }


# ---------------------------------------------------------------------------
# GET /pipelines — list all
# ---------------------------------------------------------------------------

class TestListPipelines:
    def test_returns_list(self, client):
        r = client.get(PREFIX)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_fixture_pipelines_present(self, client):
        r = client.get(PREFIX)
        names = {p["name"] for p in r.json()}
        assert {"curl", "File-keeper"}.issubset(names)

    def test_response_shape(self, client):
        r = client.get(PREFIX)
        item = r.json()[0]
        assert "name" in item
        assert "pipeline" in item
        assert isinstance(item["pipeline"], list)


# ---------------------------------------------------------------------------
# GET /pipelines/{name} — single pipeline
# ---------------------------------------------------------------------------

class TestGetPipeline:
    def test_found(self, client):
        r = client.get(f"{PREFIX}/curl")
        assert r.status_code == 200
        assert r.json()["name"] == "curl"

    def test_not_found(self, client):
        r = client.get(f"{PREFIX}/nonexistent")
        assert r.status_code == 404

    def test_response_has_steps(self, client):
        r = client.get(f"{PREFIX}/curl")
        assert len(r.json()["pipeline"]) == 1
        step = r.json()["pipeline"][0]
        assert step["id"] == "curl-installed"


# ---------------------------------------------------------------------------
# POST /pipelines — create
# ---------------------------------------------------------------------------

class TestCreatePipeline:
    def test_create(self, client):
        body = _pipeline("fresh-pipe")
        r = client.post(PREFIX, json=body)
        assert r.status_code == 201
        assert r.json()["name"] == "fresh-pipe"

    def test_create_appears_in_list(self, client):
        client.post(PREFIX, json=_pipeline("listed-pipe"))
        r = client.get(PREFIX)
        names = {p["name"] for p in r.json()}
        assert "listed-pipe" in names

    def test_conflict(self, client):
        r = client.post(PREFIX, json=_pipeline("curl"))
        assert r.status_code == 409

    def test_empty_steps_rejected(self, client):
        body = {"name": "bad", "pipeline": []}
        r = client.post(PREFIX, json=body)
        assert r.status_code == 422

    def test_missing_name_rejected(self, client):
        body = {"pipeline": [_step()]}
        r = client.post(PREFIX, json=body)
        assert r.status_code == 422

    def test_duplicate_step_ids_rejected(self, client):
        body = {
            "name": "dupe-pipe",
            "pipeline": [_step("same-id"), _step("same-id")],
        }
        r = client.post(PREFIX, json=body)
        assert r.status_code == 422

    def test_unknown_requires_rejected(self, client):
        body = {
            "name": "bad-requires",
            "pipeline": [_step(requires=["ghost-step"])],
        }
        r = client.post(PREFIX, json=body)
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# PUT /pipelines/{name} — full replace
# ---------------------------------------------------------------------------

class TestReplacePipeline:
    def test_replace(self, client):
        new_step = _step("replaced-step", exec_cmd="which python3")
        body = {"name": "curl", "pipeline": [new_step], "connectors": ["proxmox"], "runner": "proxmox_ct"}
        r = client.put(f"{PREFIX}/curl", json=body)
        print(r.content)
        assert r.status_code == 200
        step_ids = [s["id"] for s in r.json()["pipeline"]]
        assert step_ids == ["replaced-step"]
        assert "curl-installed" not in step_ids

    def test_replace_not_found(self, client):
        r = client.put(f"{PREFIX}/ghost", json=_pipeline("ghost"))
        assert r.status_code == 404

    def test_replace_name_mismatch_rejected(self, client):
        body = _pipeline("wrong-name")
        r = client.put(f"{PREFIX}/curl", json=body)
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /pipelines/{name}/steps — list steps
# ---------------------------------------------------------------------------

class TestListSteps:
    def test_list_steps(self, client):
        r = client.get(f"{PREFIX}/curl/steps")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert r.json()[0]["id"] == "curl-installed"

    def test_pipeline_not_found(self, client):
        r = client.get(f"{PREFIX}/ghost/steps")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /pipelines/{name}/steps — add step
# ---------------------------------------------------------------------------

class TestAddStep:
    def test_add_step(self, client):
        new = _step("extra-step", exec_cmd="which git")
        r = client.post(f"{PREFIX}/curl/steps", json=new)
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()["pipeline"]]
        assert "curl-installed" in ids
        assert "extra-step" in ids

    def test_add_step_with_valid_requires(self, client):
        new = _step("depends-on-curl", requires=["curl-installed"])
        r = client.post(f"{PREFIX}/curl/steps", json=new)
        assert r.status_code == 200

    def test_add_duplicate_id_rejected(self, client):
        r = client.post(f"{PREFIX}/curl/steps", json=_step("curl-installed"))
        print(r.content)
        assert r.status_code == 422

    def test_add_step_unknown_requires_rejected(self, client):
        new = _step("broken", requires=["nonexistent"])
        r = client.post(f"{PREFIX}/curl/steps", json=new)
        assert r.status_code == 422

    def test_pipeline_not_found(self, client):
        r = client.post(f"{PREFIX}/ghost/steps", json=_step())
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /pipelines/{name}/steps/{step_id} — edit step
# ---------------------------------------------------------------------------

class TestEditStep:
    def test_edit_exec(self, client):
        patch = {"exec": "which curl2"}
        r = client.patch(f"{PREFIX}/curl/steps/curl-installed", json=patch)
        assert r.status_code == 200
        step = next(s for s in r.json()["pipeline"] if s["id"] == "curl-installed")
        assert step["exec"] == "which curl2"

    def test_edit_empty_patch_is_noop(self, client):
        r = client.patch(f"{PREFIX}/curl/steps/curl-installed", json={})
        assert r.status_code == 200
        step = next(s for s in r.json()["pipeline"] if s["id"] == "curl-installed")
        assert step["exec"] == "which curl"

    def test_edit_step_not_found(self, client):
        r = client.patch(f"{PREFIX}/curl/steps/ghost", json={"exec": "ls"})
        assert r.status_code == 404

    def test_edit_pipeline_not_found(self, client):
        r = client.patch(f"{PREFIX}/ghost/steps/any", json={"exec": "ls"})
        assert r.status_code == 404

    def test_edit_requires_to_unknown_rejected(self, client):
        patch = {"requires": ["nonexistent"]}
        r = client.patch(f"{PREFIX}/curl/steps/curl-installed", json=patch)
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /pipelines/{name}/steps/{step_id} — remove step
# ---------------------------------------------------------------------------

class TestRemoveStep:
    def test_remove_step(self, client):
        # File-keeper has only fk-installed, add a second step first so the
        # pipeline stays valid (min_length=1) after removal
        client.post(f"{PREFIX}/File-keeper/steps", json=_step("extra"))
        r = client.delete(f"{PREFIX}/File-keeper/steps/extra")
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()["pipeline"]]
        assert "extra" not in ids
        assert "fk-installed" in ids

    def test_remove_step_not_found(self, client):
        r = client.delete(f"{PREFIX}/curl/steps/ghost")
        assert r.status_code == 404

    def test_remove_pipeline_not_found(self, client):
        r = client.delete(f"{PREFIX}/ghost/steps/any")
        assert r.status_code == 404

    def test_remove_last_step_rejected(self, client):
        # Pipeline must have at least one step
        r = client.delete(f"{PREFIX}/curl/steps/curl-installed")
        assert r.status_code == 422

    def test_remove_required_step_rejected(self, client):
        # Add a step that depends on curl-installed, then try to delete curl-installed
        client.post(f"{PREFIX}/curl/steps", json=_step("needs-curl", requires=["curl-installed"]))
        r = client.delete(f"{PREFIX}/curl/steps/curl-installed")
        assert r.status_code == 409
        detail = r.json()["detail"]
        # Detail should be a list of readable messages, not raw pydantic noise
        assert isinstance(detail, list)
        assert any("curl-installed" in msg for msg in detail)
