"""Tests for the three management modules: workflow library, model docs,
injection text. Each test drives the HTTP surface through the shared client
fixture so auth/CSRF wiring is covered too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

HEADERS = {"Authorization": "Bearer test-session-token"}

SAMPLE_API_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 1,
            "steps": 20,
            "cfg": 7,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sample.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad", "clip": ["4", 1]}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "out", "images": ["8", 0]}},
}


@pytest.fixture()
def workflow_id(client, tmp_path):
    c, _main = client
    response = c.post(
        "/api/workflows/import",
        headers=HEADERS,
        json={"filename": "sample.json", "workflow": SAMPLE_API_WORKFLOW},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _write_manifest(root: Path, workflow_id: str, **extra) -> None:
    path = root / "workflows" / workflow_id / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(extra)
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Workflow library
# --------------------------------------------------------------------------- #


def test_library_lists_card_with_defaults(client, workflow_id, tmp_path):
    c, main = client
    response = c.get("/api/workflow-library", headers=HEADERS)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    card = items[0]
    assert card["id"] == workflow_id
    assert card["title"] == "sample.json"  # falls back to the filename
    assert card["draft_count"] == 0
    assert card["archived"] is False


def test_library_meta_update_and_ordering(client, workflow_id):
    c, _main = client
    response = c.post(
        f"/api/workflow-library/{workflow_id}/meta",
        headers=HEADERS,
        json={"title": "人像主流程", "note": "常用", "tags": ["人像", "人像", " XL "]},
    )
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["title"] == "人像主流程"
    assert card["note"] == "常用"
    # Duplicate tags collapse case-insensitively, whitespace trimmed.
    assert card["tags"] == ["人像", "XL"]

    # Pinning floats the card to the front without bumping updated_at twice.
    c.post(
        f"/api/workflow-library/{workflow_id}/meta",
        headers=HEADERS,
        json={"pinned": True},
    )
    assert c.get("/api/workflow-library", headers=HEADERS).json()["items"][0]["pinned"] is True


def test_library_duplicate_copies_manifest_and_optional_drafts(client, workflow_id):
    c, _main = client
    response = c.post(
        f"/api/workflow-library/{workflow_id}/duplicate",
        headers=HEADERS,
        json={"title": "副本流程", "with_drafts": False},
    )
    assert response.status_code == 200, response.text
    copy = response.json()
    assert copy["id"] != workflow_id
    assert copy["title"] == "副本流程"
    # A copy inherits the source's confirmation state (the graph is identical).
    assert copy["format"] == "api"

    items = c.get("/api/workflow-library", headers=HEADERS).json()["items"]
    assert len(items) == 2


def test_library_archive_and_restore(client, workflow_id):
    c, _main = client
    c.post(f"/api/workflow-library/{workflow_id}/archive?archived=true", headers=HEADERS)
    hidden = c.get(
        "/api/workflow-library?include_archived=false", headers=HEADERS
    ).json()["items"]
    assert hidden == []

    c.post(f"/api/workflow-library/{workflow_id}/archive?archived=false", headers=HEADERS)
    assert len(c.get("/api/workflow-library", headers=HEADERS).json()["items"]) == 1


def test_library_delete_removes_drafts_but_keeps_history_table(client, workflow_id, tmp_path):
    c, main = client
    root = main.DATA_ROOT
    drafts = root / "workflows" / workflow_id / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "d1.json").write_text("{}", encoding="utf-8")

    response = c.delete(f"/api/workflow-library/{workflow_id}", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] is True
    assert not (root / "workflows" / workflow_id).exists()
    assert c.get("/api/workflow-library", headers=HEADERS).json()["items"] == []


def test_library_rejects_bad_id(client):
    c, _main = client
    response = c.get("/api/workflow-library/not-a-uuid", headers=HEADERS)
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Model documentation
# --------------------------------------------------------------------------- #


@pytest.fixture()
def profile_root(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    (root / "models").mkdir(parents=True)
    (root / "templates").mkdir(parents=True)
    (root / "core").mkdir(parents=True)
    (root / "personas" / "prompt").mkdir(parents=True)
    (root / "personas" / "writing").mkdir(parents=True)
    (root / "genres").mkdir(parents=True)
    (root / "styles").mkdir(parents=True)
    (root / "modes").mkdir(parents=True)
    (root / "templates" / "MODEL_RULE_TEMPLATE.txt").write_text(
        "[IDENTITY]\nDisplay Name:\nExact Checkpoint Filename:\n\n"
        "[MODEL_ARCHITECTURE]\nBase Architecture:\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKBENCH_PROFILE_ROOT", str(root))
    return root


def test_model_doc_template_is_readable(client, profile_root):
    c, _main = client
    response = c.get("/api/model-docs/template", headers=HEADERS)
    assert response.status_code == 200
    assert "Display Name:" in response.json()["text"]


def test_model_doc_create_fills_template_and_card_fields(client, profile_root):
    c, _main = client
    response = c.post(
        "/api/model-docs",
        headers=HEADERS,
        json={
            "display_name": "GhostMix V2.0",
            "checkpoint_file": "GhostMix_v2.0.safetensors",
            "architecture": "SD1.5",
            "strengths": "半写实插画混合质感",
            "tags": ["写实", "插画"],
            "cover_asset_id": 12,
            "trust_level": "部分核实",
        },
    )
    assert response.status_code == 200, response.text
    entry = response.json()
    assert entry["id"] == "ghostmix-v2-0"
    assert entry["strengths"] == "半写实插画混合质感"
    assert entry["cover_asset_id"] == 12
    assert entry["trust_level"] == "部分核实"
    assert entry["checkpoint_patterns"] == ["GhostMix_v2.0.safetensors"]

    # The rules body was seeded from the template with identity filled in.
    rules = (profile_root / "models" / "ghostmix-v2-0" / "rules.txt").read_text(encoding="utf-8")
    assert "Display Name: GhostMix V2.0" in rules
    assert "Base Architecture: SD1.5" in rules


def test_model_doc_create_rejects_empty_payload(client, profile_root):
    c, _main = client
    response = c.post("/api/model-docs", headers=HEADERS, json={})
    assert response.status_code == 400


def test_model_doc_create_dedupes_folder_id(client, profile_root):
    c, _main = client
    payload = {"display_name": "Same Name", "checkpoint_file": "same.safetensors"}
    first = c.post("/api/model-docs", headers=HEADERS, json=payload).json()
    second = c.post("/api/model-docs", headers=HEADERS, json=payload).json()
    assert first["id"] == "same-name"
    assert second["id"] == "same-name-2"


def test_model_doc_update_meta_and_rules(client, profile_root):
    c, _main = client
    created = c.post(
        "/api/model-docs",
        headers=HEADERS,
        json={"display_name": "M1", "checkpoint_file": "m1.safetensors", "architecture": "SDXL"},
    ).json()
    pid = created["id"]

    response = c.post(
        f"/api/model-docs/{pid}",
        headers=HEADERS,
        json={"display_name": "M1 改名", "strengths": "擅长人像", "rules_text": "[IDENTITY]\n更新后正文\n"},
    )
    assert response.status_code == 200, response.text
    entry = response.json()
    assert entry["display_name"] == "M1 改名"
    assert entry["strengths"] == "擅长人像"
    assert "更新后正文" in entry["rules"]


def test_model_doc_delete_removes_folder_and_versions(client, profile_root):
    c, _main = client
    created = c.post(
        "/api/model-docs",
        headers=HEADERS,
        json={"display_name": "Doomed", "checkpoint_file": "doomed.safetensors"},
    ).json()
    pid = created["id"]
    archive = profile_root / "archive" / pid / "20260101-000000"
    archive.mkdir(parents=True)
    (archive / "rules.txt").write_text("old", encoding="utf-8")

    response = c.delete(f"/api/model-docs/{pid}", headers=HEADERS)
    assert response.status_code == 200
    assert not (profile_root / "models" / pid).exists()
    assert not (profile_root / "archive" / pid).exists()


def test_model_doc_unknown_id_returns_404(client, profile_root):
    c, _main = client
    assert c.get("/api/model-docs/nope", headers=HEADERS).status_code == 404
    assert c.delete("/api/model-docs/nope", headers=HEADERS).status_code == 404


# --------------------------------------------------------------------------- #
# Injection text
# --------------------------------------------------------------------------- #


def test_injection_lists_empty_layers(client, profile_root):
    c, _main = client
    payload = c.get("/api/injection-docs", headers=HEADERS).json()
    assert set(payload["layers"]) == {
        "prompt_persona",
        "writing_persona",
        "genre",
        "style",
        "mode",
    }
    assert payload["core"]["exists"] is False


def test_injection_create_read_update_delete(client, profile_root):
    c, _main = client
    create = c.post(
        "/api/injection-docs/prompt_persona",
        headers=HEADERS,
        json={"name": "提示词助手", "text": "你是提示词助手。"},
    )
    assert create.status_code == 200, create.text

    listing = c.get("/api/injection-docs", headers=HEADERS).json()
    assert listing["layers"]["prompt_persona"][0]["name"] == "提示词助手"
    assert listing["layers"]["prompt_persona"][0]["chars"] > 0

    read = c.get("/api/injection-docs/prompt_persona/提示词助手", headers=HEADERS)
    assert read.status_code == 200
    assert read.json()["text"] == "你是提示词助手。"

    update = c.post(
        "/api/injection-docs/prompt_persona/提示词助手",
        headers=HEADERS,
        json={"text": "改过的正文。"},
    )
    assert update.status_code == 200
    assert (profile_root / "personas" / "prompt" / "提示词助手.txt").read_text(
        encoding="utf-8"
    ) == "改过的正文。"

    delete = c.delete("/api/injection-docs/prompt_persona/提示词助手", headers=HEADERS)
    assert delete.status_code == 200
    assert c.get("/api/injection-docs", headers=HEADERS).json()["layers"]["prompt_persona"] == []


def test_injection_create_rejects_duplicate_and_bad_name(client, profile_root):
    c, _main = client
    c.post(
        "/api/injection-docs/style",
        headers=HEADERS,
        json={"name": "古风白描", "text": "x"},
    )
    dup = c.post(
        "/api/injection-docs/style",
        headers=HEADERS,
        json={"name": "古风白描", "text": "y"},
    )
    assert dup.status_code == 400
    bad = c.post(
        "/api/injection-docs/style",
        headers=HEADERS,
        json={"name": "../escape", "text": "z"},
    )
    assert bad.status_code == 400


def test_injection_hide_toggle_round_trip(client, profile_root):
    c, _main = client
    c.post("/api/injection-docs/genre", headers=HEADERS, json={"name": "武侠", "text": "x"})

    hide = c.post("/api/injection-docs/genre/武侠/hidden?hidden=true", headers=HEADERS)
    assert hide.status_code == 200
    items = c.get("/api/injection-docs", headers=HEADERS).json()["layers"]["genre"]
    assert items[0]["hidden"] is True
    assert items[0]["name"] == "武侠"  # display name drops the hidden prefix
    assert (profile_root / "genres" / "__武侠.txt").is_file()

    show = c.post("/api/injection-docs/genre/武侠/hidden?hidden=false", headers=HEADERS)
    assert show.status_code == 200
    assert (profile_root / "genres" / "武侠.txt").is_file()
    assert not (profile_root / "genres" / "__武侠.txt").exists()


def test_injection_core_read_and_update(client, profile_root):
    c, _main = client
    core = profile_root / "core" / "core_system_prompt.txt"
    core.write_text("原始核心规则", encoding="utf-8")

    read = c.get("/api/injection-docs/core/core", headers=HEADERS)
    assert read.status_code == 200
    assert read.json()["text"] == "原始核心规则"

    update = c.post(
        "/api/injection-docs/core/core",
        headers=HEADERS,
        json={"text": "新的核心规则"},
    )
    assert update.status_code == 200
    assert core.read_text(encoding="utf-8") == "新的核心规则"


def test_injection_unknown_layer_rejected(client, profile_root):
    c, _main = client
    assert c.post(
        "/api/injection-docs/bogus", headers=HEADERS, json={"name": "x", "text": "y"}
    ).status_code == 400
