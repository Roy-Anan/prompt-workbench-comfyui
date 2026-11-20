"""Tests for the local ComfyUI-extension read-only node token."""

from __future__ import annotations

import json

import datadir
import llm_client
import workflow_api

AUTH = {"Authorization": "Bearer test-session-token"}


def _api_workflow():
    return {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "positive", "clip": ["5", 0]},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "negative", "clip": ["5", 0]},
        },
        "5": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "x.safetensors"},
        },
    }


def _import(test_client, workflow):
    resp = test_client.post(
        "/api/workflows/import",
        headers=AUTH,
        json={"filename": "sample.json", "workflow": workflow},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _fake_chat(called: dict):
    def fake_chat(url, model, messages, timeout=30.0, options=None):
        called["chat"] = True
        return {"ok": True, "content": "candidate", "model": model, "error": None}

    return fake_chat


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


def test_node_token_allowed_on_candidate_generation(client, monkeypatch):
    """The local LLM candidate endpoint is a read-only-derived POST: a node
    token may call it, but the suggestion is never written back to a draft.
    """
    test_client, main = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": False, "error": None},
    )
    called: dict = {}
    monkeypatch.setattr(llm_client, "chat", _fake_chat(called))

    resp = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=_node_headers(main),
        json={"model": "qwen3:latest"},
    )
    assert resp.status_code == 200
    assert called.get("chat") is True
    assert resp.json()["candidates"] == ["candidate"]


def test_node_token_still_rejects_draft_write(client, monkeypatch):
    """Draft persistence stays off-limits to the read-only node token."""
    test_client, main = client
    workflow_id = _import(test_client, _api_workflow())

    resp = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=_node_headers(main),
        json={"name": "x", "positive": "p", "negative": "n"},
    )
    assert resp.status_code == 403


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


def test_comfyui_loopback_origin_allowed_for_health_probe(client):
    test_client, _ = client

    resp = test_client.get(
        "/api/health", headers={"Origin": "http://127.0.0.1:8188"}
    )
    assert resp.status_code == 200
    assert (
        resp.headers.get("access-control-allow-origin")
        == "http://127.0.0.1:8188"
    )


def test_overview_is_public_read_only_counts(client):
    test_client, main = client

    resp = test_client.get("/api/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["version"] == main.APP_VERSION
    counts = body["counts"]
    assert set(counts) == {
        "rules",
        "workflows",
        "drafts",
        "assets",
        "history",
    }
    assert all(isinstance(value, int) and value >= 0 for value in counts.values())


def test_overview_allows_comfyui_loopback_origin(client):
    test_client, _ = client

    resp = test_client.get(
        "/api/overview", headers={"Origin": "http://localhost:8188"}
    )
    assert resp.status_code == 200
    assert (
        resp.headers.get("access-control-allow-origin") == "http://localhost:8188"
    )
