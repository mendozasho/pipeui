import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    """
    TestClient with config file isolated to tmp_path so tests don't touch the
    real pipeui.config.json in the working directory.
    """
    monkeypatch.chdir(tmp_path)

    # Re-import after chdir so CONFIG_PATH resolves to tmp_path
    import importlib
    import pipeui.api.settings as settings_mod
    importlib.reload(settings_mod)

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(settings_mod.router)

    return TestClient(app), settings_mod


class TestGetSettings:
    def test_creates_config_with_defaults_when_missing(self, settings_client, tmp_path):
        client, mod = settings_client
        config_path = tmp_path / "pipeui.config.json"
        assert not config_path.exists()

        res = client.get("/settings")
        assert res.status_code == 200
        assert config_path.exists(), "config file should be created on first GET"

        data = res.json()
        assert data["db_path"] == "pipeui.db"
        assert data["accent"] == "#7c6cf5"
        assert data["density"] == "regular"

    def test_returns_existing_config(self, settings_client, tmp_path):
        client, mod = settings_client
        config_path = tmp_path / "pipeui.config.json"
        config_path.write_text(json.dumps({"db_path": "custom.db", "accent": "#34d399", "density": "compact"}))

        res = client.get("/settings")
        data = res.json()
        assert data["db_path"] == "custom.db"
        assert data["accent"] == "#34d399"
        assert data["density"] == "compact"


class TestPatchSettings:
    def test_partial_update_merges_with_existing(self, settings_client, tmp_path):
        client, mod = settings_client
        # Seed a config
        client.get("/settings")

        res = client.patch("/settings", json={"accent": "#fb7185"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["settings"]["accent"] == "#fb7185"
        # other fields untouched
        assert data["settings"]["db_path"] == "pipeui.db"
        assert data["settings"]["density"] == "regular"

    def test_db_path_change_returns_restart_required(self, settings_client):
        client, mod = settings_client
        client.get("/settings")

        res = client.patch("/settings", json={"db_path": "other.db"})
        data = res.json()
        assert data["restart_required"] is True

    def test_appearance_change_does_not_require_restart(self, settings_client):
        client, mod = settings_client
        client.get("/settings")

        res = client.patch("/settings", json={"accent": "#6366f1", "density": "compact"})
        data = res.json()
        assert data["restart_required"] is False

    def test_persists_across_reads(self, settings_client):
        client, mod = settings_client
        client.get("/settings")

        client.patch("/settings", json={"density": "comfy"})
        res = client.get("/settings")
        assert res.json()["density"] == "comfy"


class TestFunctionsPaths:
    """GET /settings returns functions_paths; PATCH /settings persists list changes."""

    def test_get_returns_empty_list_by_default(self, settings_client):
        # Guarantee: GET /settings always includes functions_paths as a list
        client, mod = settings_client
        res = client.get("/settings")
        assert res.status_code == 200
        data = res.json()
        assert "functions_paths" in data
        assert data["functions_paths"] == []

    def test_patch_sets_functions_paths(self, settings_client):
        # Guarantee: PATCH /settings with functions_paths persists the list
        client, mod = settings_client
        client.get("/settings")

        res = client.patch("/settings", json={"functions_paths": ["/a/b", "/c/d"]})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["settings"]["functions_paths"] == ["/a/b", "/c/d"]

    def test_patch_functions_paths_persists_across_reads(self, settings_client):
        # Guarantee: functions_paths set via PATCH is returned on subsequent GET
        client, mod = settings_client
        client.get("/settings")

        client.patch("/settings", json={"functions_paths": ["/x/y"]})
        res = client.get("/settings")
        assert res.json()["functions_paths"] == ["/x/y"]

    def test_patch_empty_functions_paths_clears_list(self, settings_client):
        # Guarantee: PATCH with an empty list overwrites an existing list
        client, mod = settings_client
        client.get("/settings")
        client.patch("/settings", json={"functions_paths": ["/a/b"]})

        res = client.patch("/settings", json={"functions_paths": []})
        assert res.json()["settings"]["functions_paths"] == []

    def test_patch_omitting_functions_paths_leaves_it_unchanged(self, settings_client):
        # Guarantee: omitting functions_paths from a PATCH does not clear the list
        client, mod = settings_client
        client.get("/settings")
        client.patch("/settings", json={"functions_paths": ["/keep/me"]})

        client.patch("/settings", json={"accent": "#34d399"})
        res = client.get("/settings")
        assert res.json()["functions_paths"] == ["/keep/me"]

    def test_existing_config_without_functions_paths_returns_empty_list(self, settings_client, tmp_path):
        # Guarantee: config files that predate functions_paths default to []
        client, mod = settings_client
        config_path = tmp_path / "pipeui.config.json"
        config_path.write_text(json.dumps({"db_path": "pipeui.db", "accent": "#7c6cf5", "density": "regular"}))

        res = client.get("/settings")
        assert res.json()["functions_paths"] == []
