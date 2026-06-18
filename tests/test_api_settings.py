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

    def test_patch_sets_functions_paths(self, settings_client, tmp_path):
        # Guarantee: PATCH /settings with functions_paths persists the list
        client, mod = settings_client
        client.get("/settings")
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        res = client.patch("/settings", json={"functions_paths": [str(dir_a), str(dir_b)]})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["settings"]["functions_paths"] == [str(dir_a), str(dir_b)]

    def test_patch_functions_paths_persists_across_reads(self, settings_client, tmp_path):
        # Guarantee: functions_paths set via PATCH is returned on subsequent GET
        client, mod = settings_client
        client.get("/settings")
        dir_x = tmp_path / "x"
        dir_x.mkdir()

        client.patch("/settings", json={"functions_paths": [str(dir_x)]})
        res = client.get("/settings")
        assert res.json()["functions_paths"] == [str(dir_x)]

    def test_patch_empty_functions_paths_clears_list(self, settings_client, tmp_path):
        # Guarantee: PATCH with an empty list overwrites an existing list
        client, mod = settings_client
        client.get("/settings")
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        client.patch("/settings", json={"functions_paths": [str(dir_a)]})

        res = client.patch("/settings", json={"functions_paths": []})
        assert res.json()["settings"]["functions_paths"] == []

    def test_patch_omitting_functions_paths_leaves_it_unchanged(self, settings_client, tmp_path):
        # Guarantee: omitting functions_paths from a PATCH does not clear the list
        client, mod = settings_client
        client.get("/settings")
        keep_dir = tmp_path / "keep"
        keep_dir.mkdir()
        client.patch("/settings", json={"functions_paths": [str(keep_dir)]})

        client.patch("/settings", json={"accent": "#34d399"})
        res = client.get("/settings")
        assert res.json()["functions_paths"] == [str(keep_dir)]

    def test_existing_config_without_functions_paths_returns_empty_list(self, settings_client, tmp_path):
        # Guarantee: config files that predate functions_paths default to []
        client, mod = settings_client
        config_path = tmp_path / "pipeui.config.json"
        config_path.write_text(json.dumps({"db_path": "pipeui.db", "accent": "#7c6cf5", "density": "regular"}))

        res = client.get("/settings")
        assert res.json()["functions_paths"] == []


class TestFunctionsPathsValidation:
    """PATCH /settings rejects non-existent / non-directory paths before saving."""

    def test_invalid_path_returns_422_with_bad_entry(self, settings_client, tmp_path):
        # Guarantee: PATCH with a non-existent path returns 422, invalid_paths lists the bad entry
        client, mod = settings_client
        client.get("/settings")  # initialise config

        res = client.patch("/settings", json={"functions_paths": ["sdfsd"]})
        assert res.status_code == 422
        data = res.json()
        assert data["ok"] is False
        assert "sdfsd" in data["invalid_paths"]

    def test_invalid_path_leaves_settings_file_unchanged(self, settings_client, tmp_path):
        # Guarantee: settings file is not modified when any path fails validation
        client, mod = settings_client
        client.get("/settings")  # creates config with defaults
        config_path = tmp_path / "pipeui.config.json"
        original_content = config_path.read_text()

        client.patch("/settings", json={"functions_paths": ["/does/not/exist/xyz"]})
        assert config_path.read_text() == original_content

    def test_file_path_not_directory_returns_422(self, settings_client, tmp_path):
        # Guarantee: a path that exists but is a file (not a directory) is rejected
        client, mod = settings_client
        client.get("/settings")
        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("hello")

        res = client.patch("/settings", json={"functions_paths": [str(file_path)]})
        assert res.status_code == 422
        data = res.json()
        assert str(file_path) in data["invalid_paths"]

    def test_valid_directory_path_returns_200_and_saves(self, settings_client, tmp_path):
        # Guarantee: PATCH with an existing directory path succeeds and is persisted
        client, mod = settings_client
        client.get("/settings")
        valid_dir = tmp_path / "myfunctions"
        valid_dir.mkdir()

        res = client.patch("/settings", json={"functions_paths": [str(valid_dir)]})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert str(valid_dir) in data["settings"]["functions_paths"]

        # Persisted
        check = client.get("/settings")
        assert str(valid_dir) in check.json()["functions_paths"]

    def test_empty_functions_paths_saves_without_error(self, settings_client, tmp_path):
        # Guarantee: empty list bypasses validation and saves successfully
        client, mod = settings_client
        client.get("/settings")

        res = client.patch("/settings", json={"functions_paths": []})
        assert res.status_code == 200
        assert res.json()["ok"] is True
