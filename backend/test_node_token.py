"""Tests for the local ComfyUI-extension read-only node token."""

from __future__ import annotations

import json

import datadir


def _node_headers(main) -> dict[str, str]:
    return {"Authorization": f"Bearer {main.NODE_TOKEN}"}


def test_node_token_persisted_after_startup(client):
    test_client, main = client

    assert main.NODE_TOKEN
    token_file = datadir.node_token_path(main.DATA_ROOT)
    assert token_file.is_file()
    data = json.loads(token_file.read_text(encoding="utf-8"))
    assert data["token"] == main.NODE_TOKEN
    assert data.get("issued_at")


def test_node_token_allows_read_only_get(client):
    test_client, main = client

    resp = test_client.get("/api/settings", headers=_node_headers(main))
    assert resp.status_code == 200


def test_node_token_rejects_state_changing_request(client):
    test_client, main = client

    resp = test_client.post(
        "/api/settings",
        headers=_node_headers(main),
        json={"storage": {}},
    )
    assert resp.status_code == 403


def test_node_token_allowed_on_release_vram(client, monkeypatch):
    test_client, main = client
    import workflow_api

    payload = {
        "ollama_reachable": True,
        "ollama_stopped": ["m:v1"],
        "ollama_errors": [],
        "ollama_still_resident": [],
        "comfyui_freed": True,
        "comfyui_error": None,
    }
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "release_resources",
        lambda ollama_url, comfyui_url, timeout=5.0: payload,
    )

    resp = test_client.post(
        "/api/system/release-vram", headers=_node_headers(main)
    )
    assert resp.status_code == 200
    assert resp.json() == payload


def test_missing_or_wrong_token_is_rejected(client):
    test_client, main = client

    assert test_client.get("/api/settings").status_code == 401
    wrong = test_client.get(
        "/api/settings",
        headers={"Authorization": "Bearer not-the-node-token"},
    )
    assert wrong.status_code == 401


def test_node_token_reused_across_reload(client):
    test_client, main = client

    first = main.NODE_TOKEN
    second = datadir.load_or_create_node_token(main.DATA_ROOT)
    assert second == first
