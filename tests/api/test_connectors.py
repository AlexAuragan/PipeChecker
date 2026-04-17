"""
Tests for /api/v1/connectors endpoints.

Uses a fake in-memory Manager so nothing touches disk or SSH.
Fixture YAML files live in tests/fixtures/ and are copied to a
tmp_path by the root conftest.py (isolated_config).
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from src.api.api import app
from src.api import utils
from src.classes.connectors import Manager, Proxmox, Caddy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SAMPLE_CADDYFILE = str(FIXTURES_DIR / "Caddyfile")


def _make_manager(*connectors) -> Manager:
    """Build an in-memory Manager pre-loaded with given connectors."""
    m = Manager(autoload=False)
    for c in connectors:
        m.add(c)
    return m


def _proxmox(name="proxmox", ssh=None):
    return Proxmox(
        name=name,
        config_ssh=ssh or ["root@192.168.1.9", "root@192.168.1.10"],
    )


def _caddy(name="caddy", path=None):
    return Caddy(
        name=name,
        config_path=path or [SAMPLE_CADDYFILE],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_save(monkeypatch):
    """Prevent all test runs from writing to disk."""
    monkeypatch.setattr(
        "src.api.routers.connectors.save_manager", lambda m: None
    )


@pytest.fixture()
def client_empty(api_key):
    manager = _make_manager()
    app.dependency_overrides[utils.get_manager] = lambda: manager
    with TestClient(app, headers={"X-API-Key": api_key}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def client_with_proxmox(api_key):
    manager = _make_manager(_proxmox())
    app.dependency_overrides[utils.get_manager] = lambda: manager
    with TestClient(app, headers={"X-API-Key": api_key}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def client_with_both(api_key):
    manager = _make_manager(_proxmox(), _caddy())
    app.dependency_overrides[utils.get_manager] = lambda: manager
    with TestClient(app, headers={"X-API-Key": api_key}) as c:
        yield c
    app.dependency_overrides.clear()


PREFIX = "/api/v1/connectors"


# ---------------------------------------------------------------------------
# GET /connectors — list all
# ---------------------------------------------------------------------------

class TestListConnectors:
    def test_empty(self, client_empty):
        r = client_empty.get(PREFIX)
        print(r.json())
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_all(self, client_with_both):
        r = client_with_both.get(PREFIX)
        assert r.status_code == 200
        names = {c["name"] for c in r.json()}
        assert names == {"proxmox", "caddy"}

    def test_response_shape(self, client_with_proxmox):
        r = client_with_proxmox.get(PREFIX)
        item = r.json()[0]
        assert set(item.keys()) == {
            "name", "type", "config_path", "config_url", "config_ssh",
        }
        assert item["name"] == "proxmox"
        assert item["type"] == "Proxmox"
        assert item["config_ssh"] == [
            "root@192.168.1.9", "root@192.168.1.10",
        ]


# ---------------------------------------------------------------------------
# GET /connectors/{name} — single connector
# ---------------------------------------------------------------------------

class TestGetConnector:
    def test_found(self, client_with_proxmox):
        r = client_with_proxmox.get(f"{PREFIX}/proxmox")
        assert r.status_code == 200
        assert r.json()["name"] == "proxmox"

    def test_not_found(self, client_empty):
        r = client_empty.get(f"{PREFIX}/nonexistent")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /connectors — create
# ---------------------------------------------------------------------------

class TestCreateConnector:
    def test_create_proxmox(self, client_empty):
        body = {
            "name": "pve-new",
            "type": "Proxmox",
            "config_ssh": ["root@10.0.0.1"],
        }
        r = client_empty.post(PREFIX, json=body)
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "pve-new"
        assert data["type"] == "Proxmox"
        assert data["config_ssh"] == ["root@10.0.0.1"]

    def test_create_caddy(self, client_empty):
        body = {
            "name": "my-caddy",
            "type": "Caddy",
            "config_path": [SAMPLE_CADDYFILE],
        }
        r = client_empty.post(PREFIX, json=body)
        assert r.status_code == 201
        assert r.json()["type"] == "Caddy"

    def test_conflict(self, client_with_proxmox):
        body = {
            "name": "proxmox",
            "type": "Proxmox",
            "config_ssh": ["root@10.0.0.1"],
        }
        r = client_with_proxmox.post(PREFIX, json=body)
        assert r.status_code == 409

    def test_missing_type(self, client_empty):
        body = {"name": "broken"}
        r = client_empty.post(PREFIX, json=body)
        assert r.status_code == 422

    def test_invalid_type(self, client_empty):
        body = {"name": "broken", "type": "Docker"}
        r = client_empty.post(PREFIX, json=body)
        assert r.status_code == 422

    def test_appears_in_list_after_create(self, client_empty):
        body = {
            "name": "fresh",
            "type": "Proxmox",
            "config_ssh": ["root@10.0.0.1"],
        }
        client_empty.post(PREFIX, json=body)
        r = client_empty.get(PREFIX)
        assert any(c["name"] == "fresh" for c in r.json())


# ---------------------------------------------------------------------------
# PUT /connectors/{name} — full replace
# ---------------------------------------------------------------------------

class TestReplaceConnector:
    def test_replace(self, client_with_proxmox):
        body = {
            "name": "proxmox",
            "type": "Proxmox",
            "config_ssh": ["root@10.0.0.99"],
        }
        r = client_with_proxmox.put(f"{PREFIX}/proxmox", json=body)
        assert r.status_code == 200
        assert r.json()["config_ssh"] == ["root@10.0.0.99"]

    def test_replace_not_found(self, client_empty):
        body = {
            "name": "ghost",
            "type": "Proxmox",
            "config_ssh": ["root@10.0.0.1"],
        }
        r = client_empty.put(f"{PREFIX}/ghost", json=body)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /connectors/{name} — partial update
# ---------------------------------------------------------------------------

class TestPatchConnector:
    def test_patch_ssh_only(self, client_with_proxmox):
        body = {"config_ssh": ["root@10.0.0.50"]}
        r = client_with_proxmox.patch(f"{PREFIX}/proxmox", json=body)
        assert r.status_code == 200
        data = r.json()
        assert data["config_ssh"] == ["root@10.0.0.50"]
        assert data["type"] == "Proxmox"

    def test_patch_empty_body(self, client_with_proxmox):
        r = client_with_proxmox.patch(f"{PREFIX}/proxmox", json={})
        assert r.status_code == 200
        assert r.json()["config_ssh"] == [
            "root@192.168.1.9", "root@192.168.1.10",
        ]

    def test_patch_not_found(self, client_empty):
        r = client_empty.patch(
            f"{PREFIX}/ghost", json={"config_ssh": ["root@1.2.3.4"]}
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /connectors/{name}
# ---------------------------------------------------------------------------

class TestDeleteConnector:
    def test_delete(self, client_with_proxmox):
        r = client_with_proxmox.delete(f"{PREFIX}/proxmox")
        assert r.status_code == 204
        r = client_with_proxmox.get(f"{PREFIX}/proxmox")
        assert r.status_code == 404

    def test_delete_not_found(self, client_empty):
        r = client_empty.delete(f"{PREFIX}/nonexistent")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /connectors/{name}/targets
# POST /connectors/{name}/discover
# ---------------------------------------------------------------------------

class TestTargets:
    """These endpoints SSH into machines, so we mock load_targets."""

    def test_list_targets(self, client_with_proxmox):
        fake = MagicMock()
        fake.id = "100"
        fake.config = {"ip": "192.168.1.100", "name": "ct-100"}

        with patch.object(
            Proxmox, "targets",
            new_callable=lambda: property(lambda self: [fake]),
        ):
            r = client_with_proxmox.get(f"{PREFIX}/proxmox/targets")
            assert r.status_code == 200
            data = r.json()
            assert len(data) == 1
            assert data[0]["id"] == "100"
            assert data[0]["conf"]["ip"] == "192.168.1.100"

    def test_discover(self, client_with_proxmox):
        fake = MagicMock()
        fake.id = "200"
        fake.config = {"ip": "192.168.1.200", "name": "ct-200"}

        with patch.object(Proxmox, "load_targets", return_value=None):
            with patch.object(
                Proxmox, "targets",
                new_callable=lambda: property(lambda self: [fake]),
            ):
                r = client_with_proxmox.post(f"{PREFIX}/proxmox/discover")
                assert r.status_code == 200
                assert r.json()[0]["id"] == "200"

    def test_targets_connector_not_found(self, client_empty):
        r = client_empty.get(f"{PREFIX}/ghost/targets")
        assert r.status_code == 404