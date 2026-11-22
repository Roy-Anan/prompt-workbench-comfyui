import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def auth():
    return {"Authorization": "Bearer test-session-token"}


def bootstrap(client):
    """Exchange the bootstrap token for a session ticket + CSRF cookie."""
    test_client, _ = client
    response = test_client.get("/api/bootstrap", headers=auth())
    assert response.status_code == 200
    csrf = response.json()["csrf"]
    return {"X-Workbench-CSRF": csrf}


def test_health_is_unauthenticated_identity_probe(client):
    test_client, _ = client

    # Health is an identity probe: reachable without a session and exposes the
    # app identifier/version, but no user data.
    public = test_client.get("/api/health")
    assert public.status_code == 200
    body = public.json()
    assert body["ok"] is True
    assert body["app"] == "ComfyUI-Prompt-Workbench"

    # A protected endpoint still rejects a missing/valid session.
    missing = test_client.get("/api/settings")
    assert missing.status_code == 401

    wrong = test_client.get(
        "/api/settings",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert wrong.status_code == 401

    valid = test_client.get("/api/health", headers=auth())
    assert valid.status_code == 200
    assert valid.json()["ok"] is True


def test_bootstrap_issues_ticket_and_csrf(client):
    test_client, _ = client

    response = test_client.get("/api/bootstrap", headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["csrf"]

    cookies = {c.name: c.value for c in test_client.cookies.jar}
    assert "workbench_session" in cookies
    assert "workbench_csrf" in cookies

    # Ticket cookie alone authorizes a same-origin GET.
    authed = test_client.get("/api/health")
    assert authed.status_code == 200


def test_bootstrap_same_origin_issues_ticket_without_token(client):
    test_client, _ = client

    # Same-origin browser fetch omits Origin; ticket is issued directly.
    response = test_client.get("/api/bootstrap")
    assert response.status_code == 200
    cookies = {c.name: c.value for c in test_client.cookies.jar}
    assert "workbench_session" in cookies
    assert "workbench_csrf" in cookies

    authed = test_client.get("/api/health")
    assert authed.status_code == 200


def test_bootstrap_external_origin_requires_token(client):
    test_client, _ = client

    response = test_client.get(
        "/api/bootstrap",
        headers={"Origin": "https://untrusted.example"},
    )
    assert response.status_code == 401

    authed = test_client.get(
        "/api/bootstrap",
        headers={
            "Origin": "https://untrusted.example",
            "Authorization": "Bearer test-session-token",
        },
    )
    assert authed.status_code == 200


def test_post_requires_csrf_header(client):
    test_client, _ = client
    test_client.get("/api/bootstrap", headers=auth())

    # Cookie ticket present but CSRF header missing -> 403.
    missing = test_client.post(
        "/api/settings",
        json={"connections": {"ollama_url": "http://127.0.0.1:11434"}},
    )
    assert missing.status_code == 403

    csrf = bootstrap(client)
    ok = test_client.post(
        "/api/settings",
        headers=csrf,
        json={"connections": {"ollama_url": "http://127.0.0.1:11434"}},
    )
    assert ok.status_code == 200


def test_settings_persists_protected_terms(client):
    test_client, _ = client
    csrf = bootstrap(client)
    ok = test_client.post(
        "/api/settings",
        headers=csrf,
        json={
            "prompt": {
                "protected_terms": ["白发少女", "白发少女", "", "ru"]
            }
        },
    )
    assert ok.status_code == 200
    body = test_client.get("/api/settings", headers=auth()).json()
    assert body["prompt"]["protected_terms"] == ["白发少女", "ru"]


def test_settings_rejects_unknown_prompt_key_and_bad_list(client):
    test_client, _ = client
    csrf = bootstrap(client)

    unknown = test_client.post(
        "/api/settings",
        headers=csrf,
        json={"prompt": {"other": ["x"]}},
    )
    assert unknown.status_code == 400

    bad_list = test_client.post(
        "/api/settings",
        headers=csrf,
        json={"prompt": {"protected_terms": "not-a-list"}},
    )
    assert bad_list.status_code == 400


def test_cors_allows_vite_origin(client):
    test_client, _ = client

    response = test_client.options(
        "/api/health",
        headers={
            "Origin": "http://127.0.0.1:1420",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == (
        "http://127.0.0.1:1420"
    )


def test_cors_rejects_unknown_origin(client):
    test_client, _ = client

    response = test_client.options(
        "/api/health",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_state_change_rejects_untrusted_origin(client):
    test_client, _ = client
    test_client.get("/api/bootstrap", headers=auth())
    csrf = bootstrap(client)

    response = test_client.post(
        "/api/settings",
        headers={**csrf, "Origin": "https://untrusted.example"},
        json={"connections": {"ollama_url": "http://127.0.0.1:11434"}},
    )
    assert response.status_code == 403


def test_settings_create_managed_directories(client):
    test_client, _ = client

    response = test_client.get("/api/settings", headers=auth())
    assert response.status_code == 200

    storage = response.json()["storage"]

    keys = (
        "database", "knowledge", "prompts", "workflows",
        "drafts", "history", "cache", "logs",
        "backups", "exports",
    )

    for key in keys:
        assert Path(storage[key]).is_dir()


def test_sqlite_database_created(client):
    test_client, _ = client

    response = test_client.get("/api/settings", headers=auth())
    assert response.status_code == 200

    database_path = (
        Path(response.json()["storage"]["database"])
        / "workbench.sqlite3"
    )

    assert database_path.is_file()

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        assert {"schema_version", "app_metadata"} <= tables

        version = connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]

        assert version == 1


def test_sqlite_reinitialization_preserves_version(client):
    test_client, main = client

    response = test_client.get("/api/settings", headers=auth())
    assert response.status_code == 200

    settings = response.json()
    database_path = (
        Path(settings["storage"]["database"])
        / "workbench.sqlite3"
    )

    main.initialize_database(settings)
    main.initialize_database(settings)

    with sqlite3.connect(database_path) as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]

    assert version == 1


def test_root_change_is_rejected(client):
    test_client, _ = client

    response = test_client.post(
        "/api/settings",
        headers=auth(),
        json={"storage": {"root": "D:/other-location"}},
    )

    assert response.status_code == 400
    assert "fixed" in response.json()["detail"]


def test_unknown_storage_key_is_rejected(client):
    test_client, _ = client

    response = test_client.post(
        "/api/settings",
        headers=auth(),
        json={"storage": {"unknown": "C:/bad"}},
    )

    assert response.status_code == 400


def test_external_model_paths_can_be_empty(client):
    test_client, _ = client

    response = test_client.post(
        "/api/settings",
        headers=auth(),
        json={
            "storage": {
                "checkpoint": "",
                "lora": "",
                "clip": "",
                "vae": "",
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["storage"]["checkpoint"] == ""
    assert response.json()["storage"]["lora"] == ""
    assert response.json()["storage"]["clip"] == ""
    assert response.json()["storage"]["vae"] == ""


def test_external_model_path_must_exist_when_set(client, tmp_path):
    test_client, _ = client

    response = test_client.post(
        "/api/settings",
        headers=auth(),
        json={
            "storage": {
                "checkpoint": str(tmp_path / "missing-checkpoints")
            }
        },
    )

    assert response.status_code == 400


def test_storage_validation_can_create_directory(client, tmp_path):
    test_client, _ = client
    target = tmp_path / "new-managed-folder"

    response = test_client.post(
        "/api/storage/validate",
        headers=auth(),
        json={
            "path": str(target),
            "create": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["exists"] is True
    assert response.json()["is_directory"] is True
    assert response.json()["writable"] is True


def test_corrupt_settings_are_backed_up(client):
    test_client, main = client

    main.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    main.SETTINGS_FILE.write_text(
        "{broken-json",
        encoding="utf-8",
    )

    response = test_client.get("/api/settings", headers=auth())

    assert response.status_code == 200
    assert response.json()["storage"]["root"] == str(main.DATA_ROOT)

    backups = list(
        main.SETTINGS_FILE.parent.glob("settings.corrupt-*.json")
    )

    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken-json"


def test_services_status_returns_when_services_offline(client):
    test_client, _ = client

    response = test_client.get(
        "/api/services/status",
        headers=auth(),
    )

    assert response.status_code == 200
    assert "ollama" in response.json()
    assert "comfyui" in response.json()


def test_diagnostics_report_is_read_only_and_conservative(client):
    test_client, _ = client

    response = test_client.get("/api/diagnostics", headers=auth())
    assert response.status_code == 200
    body = response.json()

    assert body["app"] == "ComfyUI-Prompt-Workbench"
    assert body["version"]
    assert body["uploaded"] is False
    assert body["database_error"] is None
    assert body["database"]["integrity"] == "ok"
    assert set(body["counts"]) == {
        "rules",
        "history",
        "tasks",
        "model_identities",
        "workflows",
        "profiles",
        "drafts",
    }
    for value in body["counts"].values():
        assert value is None or isinstance(value, int)
    assert "ollama" in body["services"]
    assert "comfyui" in body["services"]
    assert "directories" in body
    assert "external_directories" in body


def test_runtime_logs_endpoint_reads_local_tail(client):
    test_client, main_module = client

    response = test_client.get("/api/logs", headers=auth())
    assert response.status_code == 200
    body = response.json()

    assert body["path"].endswith("workbench.log")
    assert isinstance(body["lines"], list)
    assert body["count"] == len(body["lines"])
    # Lifespan startup lines were written through the rotating file handler.
    assert any("工作台后端启动" in line for line in body["lines"])


def test_runtime_logs_endpoint_requires_session(client):
    test_client, _ = client
    assert test_client.get("/api/logs").status_code == 401


def test_runtime_log_read_tail_bounds_lines(tmp_path):
    import runtime_log

    runtime_log.setup_logging(tmp_path)
    log = runtime_log.logger()
    for index in range(5):
        log.info("tail-line-%d", index)

    tail = runtime_log.read_tail(tmp_path, 2)
    assert len(tail) == 2
    assert "tail-line-3" in tail[0]
    assert "tail-line-4" in tail[1]

    missing = runtime_log.read_tail(tmp_path / "nope", 10)
    assert missing == []


def test_diagnostics_counts_drafts_including_trashed(client):
    test_client, _ = client
    workflow = {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "pos", "clip": ["5", 0]},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "neg", "clip": ["5", 0]},
        },
        "5": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "x.safetensors"},
        },
    }
    imported = test_client.post(
        "/api/workflows/import",
        headers=auth(),
        json={"filename": "w.json", "workflow": workflow},
    )
    assert imported.status_code == 200
    workflow_id = imported.json()["id"]
    confirmed = test_client.post(
        f"/api/workflows/{workflow_id}/confirm",
        headers=auth(),
        json={"positive_node_ids": ["1"], "negative_node_ids": ["2"]},
    )
    assert confirmed.status_code == 200

    saved = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=auth(),
        json={"name": "draft-1", "positive": "pos", "negative": "neg"},
    )
    assert saved.status_code == 200
    trashed_id = saved.json()["id"]

    body = test_client.get("/api/diagnostics", headers=auth()).json()
    assert body["counts"]["workflows"] == 1
    assert body["counts"]["drafts"] == 1

    # Soft-deleted recycle-bin drafts still count toward the total.
    removed = test_client.delete(
        f"/api/workflows/{workflow_id}/drafts/{trashed_id}", headers=auth()
    )
    assert removed.status_code == 200
    body2 = test_client.get("/api/diagnostics", headers=auth()).json()
    assert body2["counts"]["drafts"] == 1


def test_diagnostics_inspects_corrupt_drafts_and_orphan_files(client):
    test_client, main = client
    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}},
    }
    imported = test_client.post(
        "/api/workflows/import",
        headers=auth(),
        json={"filename": "w.json", "workflow": workflow},
    )
    assert imported.status_code == 200
    workflow_id = imported.json()["id"]
    folder = main.DATA_ROOT / "workflows" / workflow_id

    drafts = folder / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "broken.json").write_text("{not json", encoding="utf-8")
    (drafts / "stray.tmp").write_text("x", encoding="utf-8")

    versions = folder / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    (versions / "broken.json").write_text("[", encoding="utf-8")

    (folder / "notes.txt").write_text("x", encoding="utf-8")

    body = test_client.get("/api/diagnostics", headers=auth()).json()
    health = body["storage_health"]

    assert health["corrupt_manifest_count"] == 0
    assert health["corrupt_draft_count"] == 1
    assert any("drafts/broken.json" in p for p in health["corrupt_drafts"])
    assert health["corrupt_version_count"] == 1
    assert any("versions/broken.json" in p for p in health["corrupt_versions"])
    assert health["orphan_file_count"] == 2
    rels = health["orphan_files"]
    assert any("drafts/stray.tmp" in p for p in rels)
    assert any("notes.txt" in p for p in rels)


def test_ollama_models_passes_through_discovered_names(client, monkeypatch):
    test_client, main = client

    def fake_fetch(url):
        return {
            "online": True,
            "models": ["comfyui-prompt:v1", "qwen3:latest"],
            "error": None,
        }

    monkeypatch.setattr(main, "fetch_ollama_models", fake_fetch)

    body = test_client.get("/api/ollama/models", headers=auth()).json()
    assert body["online"] is True
    assert body["models"] == ["comfyui-prompt:v1", "qwen3:latest"]


def test_ollama_models_passes_through_offline_state(client, monkeypatch):
    test_client, main = client

    def fake_fetch(url):
        return {"online": False, "models": [], "error": "connection refused"}

    monkeypatch.setattr(main, "fetch_ollama_models", fake_fetch)

    body = test_client.get("/api/ollama/models", headers=auth()).json()
    assert body["online"] is False
    assert body["models"] == []
    assert body["error"] == "connection refused"


def test_fetch_ollama_models_is_conservative_on_unreachable_host(client):
    _, main = client
    result = main.fetch_ollama_models("http://127.0.0.1:1")
    assert result["online"] is False
    assert result["models"] == []
    assert result["error"] is not None
    assert result["model_details"] == {}


def test_fetch_ollama_models_reads_parameter_size(client, monkeypatch):
    _, main = client
    payload = json.dumps(
        {
            "models": [
                {"name": "comfyui-prompt:v1", "details": {"parameter_size": "8.2B"}},
                {"name": "qwen2.5:3b", "details": {"parameter_size": "3.1B"}},
            ]
        }
    ).encode("utf-8")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return payload

    monkeypatch.setattr(main, "urlopen", lambda request, timeout=2.5: _Response())
    result = main.fetch_ollama_models("http://127.0.0.1:11434")
    assert result["online"] is True
    assert result["models"] == ["comfyui-prompt:v1", "qwen2.5:3b"]
    assert result["model_details"] == {
        "comfyui-prompt:v1": "8.2B",
        "qwen2.5:3b": "3.1B",
    }


def test_comfyui_status_passes_through(client, monkeypatch):
    test_client, main = client
    snapshot = {
        "online": True,
        "error": None,
        "version": "0.30.2",
        "pytorch_version": "2.13.0+cu130",
        "ram_total": 16905289728,
        "ram_free": 7933489152,
        "devices": [
            {
                "index": 0,
                "name": "cuda:0 RTX 4060 Laptop",
                "type": "cuda",
                "vram_total": 8585216000,
                "vram_free": 7451181056,
            }
        ],
    }

    monkeypatch.setattr(main, "fetch_comfyui_status", lambda url: snapshot)

    body = test_client.get("/api/comfyui/status", headers=auth()).json()
    assert body == snapshot


def test_comfyui_status_passes_through_offline(client, monkeypatch):
    test_client, main = client

    def fake_fetch(url):
        return {
            "online": False,
            "error": "connection refused",
            "version": None,
            "pytorch_version": None,
            "ram_total": None,
            "ram_free": None,
            "devices": [],
        }

    monkeypatch.setattr(main, "fetch_comfyui_status", fake_fetch)

    body = test_client.get("/api/comfyui/status", headers=auth()).json()
    assert body["online"] is False
    assert body["devices"] == []
    assert body["error"] == "connection refused"


def test_fetch_comfyui_status_is_conservative_on_unreachable_host(client):
    _, main = client
    result = main.fetch_comfyui_status("http://127.0.0.1:1")
    assert result["online"] is False
    assert result["devices"] == []
    assert result["error"] is not None


def test_fetch_comfyui_status_parses_devices_conservatively(client, monkeypatch):
    _, main = client
    payload = {
        "system": {
            "comfyui_version": "0.30.2",
            "ram_total": 16905289728,
            "ram_free": "not-a-number",
        },
        "devices": [
            {
                "name": "cuda:0 RTX 4060 Laptop",
                "type": "cuda",
                "index": 0,
                "vram_total": 8585216000,
                "vram_free": 7451181056,
            },
            "not-a-dict",
            {"vram_total": -5, "vram_free": None},
        ],
    }

    class _Resp:
        status = 200

        def read(self):
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(main, "urlopen", lambda req, timeout=2.5: _Resp())

    result = main.fetch_comfyui_status("http://127.0.0.1:8188")
    assert result["online"] is True
    assert result["version"] == "0.30.2"
    assert result["ram_total"] == 16905289728
    assert result["ram_free"] is None
    assert len(result["devices"]) == 2
    assert result["devices"][0]["vram_free"] == 7451181056
    assert result["devices"][1]["vram_total"] is None
    assert result["devices"][1]["vram_free"] is None


def test_comfyui_queue_passes_through(client, monkeypatch):
    test_client, main = client
    snapshot = {
        "online": True,
        "error": None,
        "running": ["p-running"],
        "pending": ["p-pending"],
        "history": ["p-history"],
    }

    monkeypatch.setattr(main, "fetch_comfyui_queue", lambda url: snapshot)

    body = test_client.get("/api/comfyui/queue", headers=auth()).json()
    assert body == snapshot


def test_comfyui_queue_passes_through_offline(client, monkeypatch):
    test_client, main = client

    def fake_fetch(url):
        return {
            "online": False,
            "error": "connection refused",
            "running": [],
            "pending": [],
            "history": [],
        }

    monkeypatch.setattr(main, "fetch_comfyui_queue", fake_fetch)

    body = test_client.get("/api/comfyui/queue", headers=auth()).json()
    assert body["online"] is False
    assert body["running"] == []
    assert body["history"] == []
    assert body["error"] == "connection refused"


def test_fetch_comfyui_queue_is_conservative_on_unreachable_host(client):
    _, main = client
    result = main.fetch_comfyui_queue("http://127.0.0.1:1")
    assert result["online"] is False
    assert result["running"] == []
    assert result["pending"] == []
    assert result["history"] == []
    assert result["error"] is not None


def test_fetch_comfyui_queue_parses_ids_conservatively(client, monkeypatch):
    _, main = client
    queue_payload = {
        "queue_running": [["p-running", 1, {}, {}, {}]],
        "queue_pending": [["p-pending", 2, {}, {}, {}], "not-a-list"],
    }
    history_payload = {"p-history": {"outputs": {}}}

    def fake_get_json(url, path):
        if path == "/queue":
            return queue_payload, None
        return history_payload, None

    monkeypatch.setattr(main, "_get_json", fake_get_json)

    result = main.fetch_comfyui_queue("http://127.0.0.1:8188")
    assert result["online"] is True
    assert result["running"] == ["p-running"]
    assert result["pending"] == ["p-pending"]
    assert result["history"] == ["p-history"]


def test_unknown_api_returns_json_not_html(client):
    test_client, _ = client

    response = test_client.get("/api/does-not-exist", headers=auth())
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_spa_fallback_serves_index_when_dist_present(client, tmp_path, monkeypatch):
    test_client, main = client

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>workbench</html>", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    # SPA shell and assets are served without an API session token.
    response = test_client.get("/some/deep/route")
    assert response.status_code == 200
    assert "workbench" in response.text


# ---- Data directory and migration ----------------------------------------

def test_default_data_root_uses_project_userdata(monkeypatch):
    monkeypatch.delenv("WORKBENCH_DATA_ROOT", raising=False)
    sys.modules.pop("datadir", None)
    import datadir

    root = datadir.default_data_root()
    assert root.name == "userdata"
    assert root.parent == datadir.repo_root()


def test_managed_subdirs_are_created(client):
    test_client, main = client

    response = test_client.get("/api/settings", headers=auth())
    storage = response.json()["storage"]
    for key in main.MANAGED_KEYS:
        assert Path(storage[key]).is_dir()


def _make_sqlite(db_file: Path) -> None:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO app_metadata VALUES ('source', 'legacy')")


def test_migration_copies_legacy_data_without_overwriting(tmp_path, monkeypatch):
    legacy = tmp_path / "AppData" / "Local" / "ComfyUI-Prompt-Workbench"
    db = legacy / "database" / "workbench.sqlite3"
    _make_sqlite(db)
    (legacy / "settings").mkdir(parents=True)
    (legacy / "settings" / "settings.json").write_text(
        '{"version": 2}', encoding="utf-8"
    )

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("WORKBENCH_DATA_ROOT", str(tmp_path / "userdata"))
    sys.modules.pop("datadir", None)
    sys.modules.pop("main", None)
    import datadir

    target = datadir.default_data_root()
    report = datadir.prepare_root(target)

    assert report["action"] == "migrated"
    assert report["database_integrity"] is True
    copied_db = target / "database" / "workbench.sqlite3"
    assert copied_db.is_file()
    with sqlite3.connect(copied_db) as conn:
        value = conn.execute("SELECT value FROM app_metadata WHERE key='source'").fetchone()[0]
    assert value == "legacy"
    # Old directory and its files are kept.
    assert db.is_file()
    # A backup of the legacy root exists under the new backups dir.
    backups = list((target / "backups").glob("legacy-migration-*"))
    assert backups


def test_migration_does_not_overwrite_target_with_data(tmp_path, monkeypatch):
    legacy = tmp_path / "AppData" / "Local" / "ComfyUI-Prompt-Workbench"
    _make_sqlite(legacy / "database" / "workbench.sqlite3")

    target = tmp_path / "userdata"
    target_db = target / "database" / "workbench.sqlite3"
    target_db.parent.mkdir(parents=True, exist_ok=True)
    target_db.write_text("existing target", encoding="utf-8")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("WORKBENCH_DATA_ROOT", str(target))
    sys.modules.pop("datadir", None)
    import datadir

    report = datadir.prepare_root(target)
    assert report["action"] == "skipped-conflict"
    assert target_db.read_text(encoding="utf-8") == "existing target"


def test_migration_absent_legacy_root_initializes_new(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "missing-local"))
    monkeypatch.setenv("WORKBENCH_DATA_ROOT", str(tmp_path / "userdata"))
    sys.modules.pop("datadir", None)
    import datadir

    target = datadir.default_data_root()
    report = datadir.prepare_root(target)
    assert report["action"] == "none"
    assert target.is_dir()


# ---- Legacy settings migration -------------------------------------------

def _legacy_settings(legacy_root: Path, body: str) -> None:
    legacy_root.mkdir(parents=True, exist_ok=True)
    (legacy_root / "settings.json").write_text(body, encoding="utf-8")


def test_legacy_settings_migrate_preserves_connections_and_external_paths(
    tmp_path, monkeypatch
):
    legacy = tmp_path / "AppData" / "Local" / "ComfyUI-Prompt-Workbench"
    _legacy_settings(
        legacy,
        json.dumps({
            "version": 2,
            "storage": {
                "root": str(legacy),
                "checkpoint": "D:/models/ckpt",
                "lora": "D:/models/lora",
            },
            "connections": {
                "ollama_url": "http://127.0.0.1:11434",
                "comfyui_url": "http://127.0.0.1:8188",
            },
            "theme": "dark",
        }),
    )

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("WORKBENCH_DATA_ROOT", str(tmp_path / "userdata"))
    sys.modules.pop("datadir", None)
    import datadir

    target = datadir.default_data_root()
    report = datadir.prepare_root(target)
    settings_report = report["settings_migration"]

    assert settings_report["action"] == "migrated"
    assert settings_report["backup"]
    saved = datadir.load_settings_file(target)
    assert saved["connections"]["ollama_url"] == "http://127.0.0.1:11434"
    assert saved["connections"]["comfyui_url"] == "http://127.0.0.1:8188"
    assert saved["storage"]["checkpoint"] == "D:/models/ckpt"
    assert saved["storage"]["lora"] == "D:/models/lora"
    # Managed paths point at the new root, not AppData.
    assert str(target) in saved["storage"]["database"]
    assert str(legacy) not in saved["storage"]["database"]
    # Unknown safe key preserved, not dropped.
    assert saved["_legacy_extra"]["theme"] == "dark"
    # Old file kept.
    assert (legacy / "settings.json").is_file()


def test_legacy_settings_merge_keeps_existing_user_values(tmp_path, monkeypatch):
    legacy = tmp_path / "AppData" / "Local" / "ComfyUI-Prompt-Workbench"
    _legacy_settings(
        legacy,
        json.dumps({
            "version": 2,
            "connections": {"ollama_url": "http://10.0.0.5:11434"},
            "storage": {"checkpoint": "D:/old/ckpt"},
        }),
    )

    target = tmp_path / "userdata"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("WORKBENCH_DATA_ROOT", str(target))
    sys.modules.pop("datadir", None)
    import datadir

    # Pre-existing user settings win on conflict.
    existing = datadir.default_settings(target)
    existing["connections"]["ollama_url"] = "http://127.0.0.1:11434"
    existing["storage"]["checkpoint"] = "D:/user/ckpt"
    datadir.save_settings_file(target, existing)

    report = datadir.prepare_root(target)
    settings_report = report["settings_migration"]
    saved = datadir.load_settings_file(target)

    assert saved["connections"]["ollama_url"] == "http://127.0.0.1:11434"
    assert saved["storage"]["checkpoint"] == "D:/user/ckpt"
    assert any("ollama_url" in c for c in settings_report["conflicts"])
    assert any("checkpoint" in c for c in settings_report["conflicts"])


def test_legacy_settings_corrupt_is_left_touch(tmp_path, monkeypatch):
    legacy = tmp_path / "AppData" / "Local" / "ComfyUI-Prompt-Workbench"
    _legacy_settings(legacy, "{broken-json")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("WORKBENCH_DATA_ROOT", str(tmp_path / "userdata"))
    sys.modules.pop("datadir", None)
    import datadir

    target = datadir.default_data_root()
    report = datadir.prepare_root(target)
    assert report["settings_migration"]["action"] == "failed-corrupt"
    assert (legacy / "settings.json").is_file()


def test_concurrent_settings_load_does_not_race_legacy_rewrite(tmp_path, monkeypatch):
    """Concurrent first-load requests must not replace settings.json at once.

    The first-load fan-out calls load_settings() from several worker threads.
    Preparation (including the one-time legacy settings rewrite) runs once per
    process under a lock; without it, concurrent ``tmp.replace`` calls hit a
    Windows PermissionError and surface as a 500.
    """
    legacy = tmp_path / "AppData" / "Local" / "ComfyUI-Prompt-Workbench"
    _legacy_settings(
        legacy,
        json.dumps({
            "version": 2,
            "connections": {"ollama_url": "http://127.0.0.1:11434"},
        }),
    )

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("WORKBENCH_DATA_ROOT", str(tmp_path / "userdata"))
    sys.modules.pop("datadir", None)
    sys.modules.pop("main", None)
    import datadir  # noqa: F401  (reloaded alongside main below)
    import main

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: main.load_settings(), range(16)))

    assert len(results) == 16
    for settings in results:
        assert settings["connections"]["ollama_url"] == "http://127.0.0.1:11434"

    target = main.DATA_ROOT
    saved = datadir.load_settings_file(target)
    assert saved["connections"]["ollama_url"] == "http://127.0.0.1:11434"
    assert main._root_prepared is True


# ---- Static path security -------------------------------------------------

def test_static_path_traversal_is_rejected(client, tmp_path, monkeypatch):
    _, main = client
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>workbench</html>", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    # The resolver must reject any path that escapes dist after normalization.
    assert main._resolve_within_dist("../secret.txt") is None
    assert main._resolve_within_dist("../../secret.txt") is None


def test_static_url_encoded_traversal_is_rejected(client, tmp_path, monkeypatch):
    _, main = client
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>workbench</html>", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    # URL-decoded traversal segments resolve outside dist and are rejected.
    assert main._resolve_within_dist("%2e%2e%2fsecret.txt") is None
    assert main._resolve_within_dist("..%2fsecret.txt") is None


def test_static_adjacent_prefix_collision_is_rejected(client, tmp_path, monkeypatch):
    _, main = client
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>workbench</html>", encoding="utf-8")
    sibling = tmp_path / "dist-other"
    sibling.mkdir()
    (sibling / "leak.txt").write_text("sibling-secret", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    # A sibling directory sharing the "dist" prefix must not be reachable.
    assert main._resolve_within_dist("../dist-other/leak.txt") is None


def test_static_normal_asset_resolves_inside_dist(client, tmp_path, monkeypatch):
    _, main = client
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    resolved = main._resolve_within_dist("assets/app.js")
    assert resolved is not None
    assert resolved.is_file()


# ---- Loopback host validation --------------------------------------------

def test_loopback_host_accepts_local_names():
    sys.modules.pop("main", None)
    import main

    for host in ("127.0.0.1", "localhost", "::1"):
        assert main.is_loopback_host(host) is True


def test_loopback_host_rejects_public_and_lan():
    sys.modules.pop("main", None)
    import main

    for host in ("0.0.0.0", "192.168.1.10", "10.0.0.5", "8.8.8.8"):
        assert main.is_loopback_host(host) is False


# ---- Identity on-demand hash endpoint ------------------------------------

def _seed_lora_identity(settings: dict, tmp_path: Path, name: str, content: bytes):
    import model_identity

    db_path = Path(settings["storage"]["database"]) / "workbench.sqlite3"
    lora_dir = tmp_path / "lora"
    lora_dir.mkdir(exist_ok=True)
    (lora_dir / name).write_bytes(content)
    with model_identity.connect(db_path) as conn:
        model_identity.ensure_schema(conn)
        model_identity.index_directory(conn, "lora", str(lora_dir))


def test_identity_hash_computes_for_recorded_lora(client, tmp_path):
    test_client, _ = client
    settings = test_client.get("/api/settings", headers=auth()).json()
    _seed_lora_identity(settings, tmp_path, "probe.safetensors", b"lora-bytes")

    csrf = bootstrap(client)
    response = test_client.post(
        "/api/models/identity/hash",
        headers=csrf,
        json={"category": "lora", "name": "probe.safetensors"},
    )
    assert response.status_code == 200
    identity = response.json()["identity"]
    assert identity["known"] is True
    assert identity["hashed"] is True
    assert identity["sha256"] is not None


def test_identity_hash_rejects_checkpoint_category(client):
    test_client, _ = client
    csrf = bootstrap(client)
    response = test_client.post(
        "/api/models/identity/hash",
        headers=csrf,
        json={"category": "checkpoint", "name": "pony.safetensors"},
    )
    assert response.status_code == 400


def test_identity_hash_unknown_name_returns_404(client, tmp_path):
    test_client, _ = client
    settings = test_client.get("/api/settings", headers=auth()).json()
    _seed_lora_identity(settings, tmp_path, "probe.safetensors", b"bytes")

    csrf = bootstrap(client)
    response = test_client.post(
        "/api/models/identity/hash",
        headers=csrf,
        json={"category": "lora", "name": "missing.safetensors"},
    )
    assert response.status_code == 404
