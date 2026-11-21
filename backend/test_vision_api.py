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


def test_want_tags_splits_trailing_tag_line(client, monkeypatch):
    test_client, _main = client
    _patch_env(monkeypatch)
    called: dict = {}

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        called["messages"] = messages
        return {
            "ok": True,
            "content": "一只猫坐在窗边。\n标签: 猫, 窗边, 室内",
            "model": model,
            "error": None,
        }

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    resp = test_client.post(
        "/api/vision/describe",
        headers=AUTH,
        json={
            "model": "vl-model",
            "prompt": "描述主体",
            "images": [_PNG_B64],
            "want_tags": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "一只猫坐在窗边。"
    assert body["tags"] == ["猫", "窗边", "室内"]
    assert vision_api.TAG_INSTRUCTION in called["messages"][0]["content"]


def test_tags_off_by_default_keeps_text_verbatim(client, monkeypatch):
    test_client, _main = client
    _patch_env(monkeypatch)
    called: dict = {}

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        called["messages"] = messages
        return {
            "ok": True,
            "content": "描述正文\n标签: 猫, 窗边",
            "model": model,
            "error": None,
        }

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    resp = test_client.post(
        "/api/vision/describe",
        headers=AUTH,
        json={"model": "vl-model", "images": [_PNG_B64]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tags"] == []
    assert "标签: 猫, 窗边" in body["description"]
    assert vision_api.TAG_INSTRUCTION not in called["messages"][0]["content"]


def test_split_tags_tolerates_missing_or_malformed_line():
    assert vision_api._split_tags("只有描述")[1] == []
    assert vision_api._split_tags("描述\n标签：")[1] == []
    body, tags = vision_api._split_tags("描述\nTags: cat, \"窗边\"")
    assert body == "描述"
    assert tags == ["cat", "窗边"]
    # Too many tags are capped, over-long ones dropped.
    _, many = vision_api._split_tags(
        "描述\n标签: " + ", ".join(f"t{i}" for i in range(12))
    )
    assert len(many) == vision_api.MAX_TAGS
    _, long_tags = vision_api._split_tags("描述\n标签: " + "长" * 30)
    assert long_tags == []
