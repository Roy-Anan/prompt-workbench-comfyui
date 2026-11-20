"""Tests for the local Prompt Profile registry and three-layer composition."""

from __future__ import annotations

import hashlib
import json

import llm_client
import prompt_profiles
import rule_store


FULL_RULES = """[IDENTITY]
Name: Alpha Model
[MODEL_ARCHITECTURE]
Base Architecture: SDXL 1.0
[PROMPT_SYNTAX]
comma-separated tags
[PROMPT_ORDER]
quality tags first
[NEGATIVE_PROMPT]
lowres, bad anatomy
[CONFIDENCE]
verified
[SOURCES]
manual research
ALPHA PROFILE RULES BODY
"""

PARTIAL_RULES = "[IDENTITY]\nName: Beta Model\nBETA PROFILE BODY\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_root(tmp_path, monkeypatch, core: str = "CORE GENERIC RULES"):
    root = tmp_path / "profiles"
    (root / "core").mkdir(parents=True)
    (root / "core" / "core_system_prompt.txt").write_text(core, encoding="utf-8")
    monkeypatch.setenv("WORKBENCH_PROFILE_ROOT", str(root))
    prompt_profiles._cache["manifest_key"] = None
    return root


def _write_profile(
    root,
    pid,
    rules,
    architecture="SDXL 1.0",
    patterns=None,
    enabled=True,
    version="1.0.0",
):
    d = root / "models" / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "rules.txt").write_text(rules, encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {"version": 1, "profiles": []}
    )
    manifest["profiles"] = [e for e in manifest["profiles"] if e.get("id") != pid]
    manifest["profiles"].append(
        {
            "id": pid,
            "display_name": pid.title(),
            "description": "",
            "enabled": enabled,
            "architecture": architecture,
            "checkpoint_patterns": patterns or [],
            "aliases": [],
            "rules_file": f"models/{pid}/rules.txt",
            "version": version,
            "source": "test",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "content_hash": _sha(rules),
            "priority": 10,
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    prompt_profiles._cache["manifest_key"] = None


def test_scan_flags_missing_sections_as_warnings_not_errors(tmp_path, monkeypatch):
    root = _make_root(tmp_path, monkeypatch)
    _write_profile(root, "alpha", FULL_RULES, patterns=["alpha.safetensors"])
    _write_profile(root, "beta", PARTIAL_RULES)

    entries = {e["id"]: e for e in prompt_profiles.scan_profiles(force=True)}
    assert entries["alpha"]["auto_eligible"] is True
    assert entries["alpha"]["errors"] == []
    assert entries["beta"]["auto_eligible"] is True
    assert entries["beta"]["errors"] == []
    assert "MODEL_ARCHITECTURE" in entries["beta"]["section_warnings"]


def test_scan_reports_hash_mismatch_as_fatal_error(tmp_path, monkeypatch):
    root = _make_root(tmp_path, monkeypatch)
    _write_profile(root, "alpha", FULL_RULES, patterns=["alpha.safetensors"])
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["profiles"][0]["content_hash"] = "deadbeef"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    entries = {e["id"]: e for e in prompt_profiles.scan_profiles(force=True)}
    assert entries["alpha"]["auto_eligible"] is False
    assert any("content_hash" in err for err in entries["alpha"]["errors"])


def test_match_exact_then_architecture_then_fallback(tmp_path, monkeypatch):
    root = _make_root(tmp_path, monkeypatch)
    _write_profile(root, "alpha", FULL_RULES, architecture="Pony", patterns=["alpha.safetensors"])
    _write_profile(root, "beta", PARTIAL_RULES, architecture="SDXL 1.0")

    exact = prompt_profiles.match_profile("alpha.safetensors", "SDXL 1.0", "sdxl")
    assert exact["profile_id"] == "alpha"
    assert exact["match_source"] == "exact"

    arch = prompt_profiles.match_profile("unknown.safetensors", "SDXL 1.0", "sdxl")
    assert arch["profile_id"] == "beta"
    assert arch["match_source"] == "architecture"

    fallback = prompt_profiles.match_profile("unknown.safetensors", "SD1.5", "sd15")
    assert fallback["profile_id"] is None
    assert fallback["match_source"] == "fallback"
    assert fallback["fallback_reason"]


def test_manual_missing_disabled_and_incompatible(tmp_path, monkeypatch):
    root = _make_root(tmp_path, monkeypatch)
    _write_profile(root, "alpha", FULL_RULES, patterns=["alpha.safetensors"])
    _write_profile(root, "beta", PARTIAL_RULES, enabled=False)

    missing = prompt_profiles.match_profile("x.safetensors", "SDXL 1.0", None, manual="nope")
    assert missing["profile_id"] is None
    assert "不存在" in missing["warning"]

    disabled = prompt_profiles.match_profile("x.safetensors", "SDXL 1.0", None, manual="beta")
    assert disabled["profile_id"] is None
    assert "停用" in disabled["warning"]

    incompatible = prompt_profiles.match_profile("x.safetensors", "SD1.5", "sd15", manual="alpha")
    assert incompatible["profile_id"] == "alpha"
    assert incompatible["match_source"] == "manual"
    assert "不兼容" in incompatible["warning"]


def test_compose_system_prompt_orders_core_profile_workflow():
    text = prompt_profiles.compose_system_prompt("core", "profile", "workflow")
    assert text.index("【第一层 · 通用核心规则】") < text.index("【第二层 · 模型专用规则】")
    assert text.index("【第二层 · 模型专用规则】") < text.index("【第三层 · 工作流动态规则】")
    assert "core" in text and "profile" in text and "workflow" in text


def test_publish_archives_previous_version(tmp_path, monkeypatch):
    root = _make_root(tmp_path, monkeypatch)
    staging = root / "staging" / "alpha"
    staging.mkdir(parents=True)
    (staging / "rules.txt").write_text("STAGING RULES", encoding="utf-8")
    (staging / "profile.json").write_text(
        json.dumps(
            {
                "display_name": "Alpha",
                "architecture": "SDXL 1.0",
                "checkpoint_patterns": ["alpha.safetensors"],
            }
        ),
        encoding="utf-8",
    )

    first = prompt_profiles.publish_from_staging("alpha")
    assert first["version"] == "0.0.1"
    assert (root / "models" / "alpha" / "rules.txt").is_file()
    assert not staging.exists()

    # Publish a second revision; the live one must be archived first.
    staging2 = root / "staging" / "alpha"
    staging2.mkdir(parents=True)
    (staging2 / "rules.txt").write_text("SECOND REVISION", encoding="utf-8")
    (staging2 / "profile.json").write_text(
        json.dumps({"version": "1.1.0"}), encoding="utf-8"
    )
    second = prompt_profiles.publish_from_staging("alpha")
    assert second["version"] == "1.1.0"
    assert second["archived_previous"] is True

    versions = prompt_profiles.list_versions("alpha")
    assert len(versions) == 1
    assert versions[0]["content_hash"] == _sha("STAGING RULES")


def test_rollback_restores_archived_rules(tmp_path, monkeypatch):
    root = _make_root(tmp_path, monkeypatch)
    staging = root / "staging" / "alpha"
    staging.mkdir(parents=True)
    (staging / "rules.txt").write_text("FIRST", encoding="utf-8")
    (staging / "profile.json").write_text(
        json.dumps({"architecture": "SDXL 1.0"}), encoding="utf-8"
    )
    prompt_profiles.publish_from_staging("alpha")

    staging2 = root / "staging" / "alpha"
    staging2.mkdir(parents=True)
    (staging2 / "rules.txt").write_text("SECOND", encoding="utf-8")
    prompt_profiles.publish_from_staging("alpha")

    versions = prompt_profiles.list_versions("alpha")
    stamp = versions[0]["stamp"]
    restored = prompt_profiles.rollback("alpha", stamp)
    assert restored["restored"] == stamp
    assert (root / "models" / "alpha" / "rules.txt").read_text(encoding="utf-8") == "FIRST"


def test_delete_requires_confirmation(tmp_path, monkeypatch):
    root = _make_root(tmp_path, monkeypatch)
    _write_profile(root, "alpha", FULL_RULES)

    try:
        prompt_profiles.delete_profile("alpha", confirm=False)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "confirm" in str(exc)

    result = prompt_profiles.delete_profile("alpha", confirm=True)
    assert result["id"] == "alpha"
    assert prompt_profiles.get_profile("alpha") is None


# ---- API integration -------------------------------------------------------


def _seed_context_rules(database_path):
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = __import__("sqlite3").connect(database_path, timeout=10)
    rule_store.ensure_schema(connection)
    rule_store.upsert_rule(
        connection, "checkpoint", "alpha.safetensors",
        "ARCHITECTURE::Base Architecture", "SDXL 1.0",
        provenance="author", confidence="authoritative",
    )
    connection.close()


def _import_sample(client, main, workflow):
    response = client.post(
        "/api/workflows/import",
        headers={"Authorization": "Bearer test-session-token"},
        json={"filename": "sample.json", "workflow": workflow},
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_profile_api_list_detail_and_publish(client, tmp_path, monkeypatch):
    test_client, main = client
    root = _make_root(tmp_path, monkeypatch)
    _write_profile(root, "alpha", FULL_RULES, patterns=["alpha.safetensors"])

    response = test_client.get(
        "/api/prompt-profiles", headers={"Authorization": "Bearer test-session-token"}
    )
    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()["items"]}
    assert items["alpha"]["architecture"] == "SDXL 1.0"
    assert items["alpha"]["auto_eligible"] is True

    detail = test_client.get(
        "/api/prompt-profiles/alpha",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert detail.status_code == 200
    assert "ALPHA PROFILE RULES BODY" in detail.json()["rules"]

    missing = test_client.get(
        "/api/prompt-profiles/nope",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert missing.status_code == 404


def test_candidate_injects_core_profile_and_workflow_layers(client, tmp_path, monkeypatch):
    test_client, main = client
    root = _make_root(tmp_path, monkeypatch)
    _write_profile(root, "alpha", FULL_RULES, patterns=["alpha.safetensors"])

    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)
    workflow = {
        "5": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "alpha.safetensors"},
        }
    }
    workflow_id = _import_sample(test_client, main, workflow)

    captured: dict = {}

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        captured["messages"] = messages
        return {"ok": True, "content": "candidate text", "model": model, "error": None}

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers={"Authorization": "Bearer test-session-token"},
        json={"model": "test-model"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_profile_id"] == "alpha"
    assert body["profile_match_source"] == "exact"
    assert body["system_prompt_layers"] == {
        "core": True,
        "profile": True,
        "workflow": True,
    }

    system_text = captured["messages"][0]["content"]
    assert "【第一层 · 通用核心规则】" in system_text
    assert "【第二层 · 模型专用规则】" in system_text
    assert "ALPHA PROFILE RULES BODY" in system_text
    assert "【第三层 · 工作流动态规则】" in system_text


def test_candidate_falls_back_when_no_profile_matches(client, tmp_path, monkeypatch):
    test_client, main = client
    root = _make_root(tmp_path, monkeypatch)
    _write_profile(root, "alpha", FULL_RULES, patterns=["alpha.safetensors"])

    workflow = {
        "5": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "ghost.safetensors"},
        }
    }
    workflow_id = _import_sample(test_client, main, workflow)

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        return {"ok": True, "content": "candidate text", "model": model, "error": None}

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers={"Authorization": "Bearer test-session-token"},
        json={"model": "test-model"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_profile_id"] is None
    assert body["fallback_reason"]
    assert body["system_prompt_layers"]["profile"] is False
    assert body["system_prompt_layers"]["core"] is True
