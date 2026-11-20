"""Tests for the standalone local vision description endpoint."""

from __future__ import annotations

import base64

import llm_client
import vision_api

AUTH = {"Authorization": "Bearer test-session-token"}

# 1x1 transparent PNG.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

_MODELS = {
    "vl-model": {"family": "qwen3vl", "families": ["qwen3vl"]},
    "text-model": {"family": "qwen3", "families": ["qwen3"]},
}


def _fake_chat(called: dict):
    def fake_chat(url, model, messages, timeout=30.0, options=None):
        called["chat"] = True
        called["messages"] = messages
        return {"ok": True, "content": "一只猫坐在窗边。", "model": model, "error": None}

    return fake_chat


def _patch_env(monkeypatch, models=_MODELS, show=None):
    monkeypatch.setattr(vision_api, "_local_models", lambda: dict(models))
    default_show = {"projector_info": {"arch": "mmproj"}}
    monkeypatch.setattr(
        vision_api,
        "_show_model",
        lambda url, name: dict(default_show if show is None else show),
    )
    monkeypatch.setattr(
        vision_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": False, "error": None},
    )


def test_describe_with_vision_model(client, monkeypatch):
    test_client, _main = client
    _patch_env(monkeypatch)
    called: dict = {}
    monkeypatch.setattr(llm_client, "chat", _fake_chat(called))

    resp = test_client.post(
        "/api/vision/describe",
        headers=AUTH,
        json={
            "model": "vl-model",
            "prompt": "描述主体",
            "images": [_PNG_B64],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "一只猫坐在窗边。"
    assert body["image_count"] == 1
    assert called["chat"] is True
    assert called["messages"][0]["images"] == [_PNG_B64]


def test_reject_family_vl_without_projector(client, monkeypatch):
    # Family tag says VL, but no projector layer is reported by /api/show.
    test_client, _main = client
    _patch_env(monkeypatch, show={})

    resp = test_client.post(
        "/api/vision/describe",
        headers=AUTH,
        json={"model": "vl-model", "images": [_PNG_B64]},
    )
    assert resp.status_code == 400
    assert "投影器" in resp.json()["detail"]


def test_reject_non_vision_model(client, monkeypatch):
    test_client, _main = client
    _patch_env(monkeypatch, show={})

    resp = test_client.post(
        "/api/vision/describe",
        headers=AUTH,
        json={"model": "text-model", "images": [_PNG_B64]},
    )
    assert resp.status_code == 400
    assert "视觉" in resp.json()["detail"]


def test_reject_unknown_model(client, monkeypatch):
    test_client, _main = client
    _patch_env(monkeypatch)

    resp = test_client.post(
        "/api/vision/describe",
        headers=AUTH,
        json={"model": "ghost-model", "images": [_PNG_B64]},
    )
    assert resp.status_code == 400


def test_reject_non_image_payload(client, monkeypatch):
    test_client, _main = client
    _patch_env(monkeypatch)
    garbage = base64.b64encode(b"not an image").decode("ascii")

    resp = test_client.post(
        "/api/vision/describe",
        headers=AUTH,
        json={"model": "vl-model", "images": [garbage]},
    )
    assert resp.status_code == 400
