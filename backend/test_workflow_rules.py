import sqlite3

import llm_client
import rule_store
import workflow_api


def _seed_context_rules(database_path):
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    rule_store.ensure_schema(connection)
    rule_store.upsert_rule(
        connection, "checkpoint", "sample_checkpoint.safetensors",
        "ARCHITECTURE::Base Architecture", "SD1.5（哈希一致）",
        provenance="verified_mirror", confidence="partial",
    )
    rule_store.upsert_rule(
        connection, "checkpoint", "sample_checkpoint.safetensors",
        "GENERATION SETTINGS::Steps", "20",
        provenance="author", confidence="authoritative",
        structured={"kind": "value", "value": 20},
    )
    rule_store.upsert_rule(
        connection, "architecture", "SD1.5",
        "ARCHITECTURE::Model Type", "Checkpoint",
        provenance="author", confidence="partial",
    )
    rule_store.upsert_rule(connection, "global", None, "GENERATION SETTINGS::Scheduler", "Karras")
    # Unrelated checkpoint must not leak into this context.
    rule_store.upsert_rule(
        connection, "checkpoint", "other_checkpoint.safetensors",
        "GENERATION SETTINGS::Steps", "30",
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


def test_workflow_rules_context_matches_checkpoint_and_inherits(client):
    test_client, main = client
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)

    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sample_checkpoint.safetensors"}},
        "6": {"class_type": "KSampler", "inputs": {"positive": ["1", 0], "negative": ["2", 0]}},
    }
    workflow_id = _import_sample(test_client, main, workflow)

    response = test_client.get(
        f"/api/workflows/{workflow_id}/rules",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "registered"
    assert body["checkpoint"] == "sample_checkpoint.safetensors"
    assert body["architecture"] == "SD1.5"

    cp_keys = {item["rule_key"] for item in body["checkpoint_rules"]}
    assert "GENERATION SETTINGS::Steps" in cp_keys
    assert "ARCHITECTURE::Base Architecture" in cp_keys

    arch_keys = {item["rule_key"] for item in body["architecture_rules"]}
    assert "ARCHITECTURE::Model Type" in arch_keys

    global_keys = {item["rule_key"] for item in body["global_rules"]}
    assert "GENERATION SETTINGS::Scheduler" in global_keys

    # The Base Architecture fact was redirected to the architecture level on
    # import, so the checkpoint record itself may not carry it; the join must
    # still resolve the architecture scope from the architecture bucket.
    assert body["architecture"] == "SD1.5"

    # Unrelated checkpoint rule must be excluded.
    all_keys = cp_keys | arch_keys | global_keys
    assert "other_checkpoint.safetensors" not in str(body)


def test_workflow_rules_unregistered_for_unknown_checkpoint(client):
    test_client, main = client
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)

    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ghost.safetensors"}},
    }
    workflow_id = _import_sample(test_client, main, workflow)

    response = test_client.get(
        f"/api/workflows/{workflow_id}/rules",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unregistered"
    assert body["checkpoint"] == "ghost.safetensors"
    assert body["checkpoint_rules"] == []
    assert body["architecture"] is None
    assert body.get("architecture_family") is None


def test_workflow_rule_prompt_payload_for_registered_workflow(client):
    test_client, main = client
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)

    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sample_checkpoint.safetensors"}},
    }
    workflow_id = _import_sample(test_client, main, workflow)

    response = test_client.get(
        f"/api/workflows/{workflow_id}/rules/prompt",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "registered"
    assert body["checkpoint"] == "sample_checkpoint.safetensors"
    field_keys = {item["key"] for item in body["fields"]}
    assert "GENERATION SETTINGS::Steps" in field_keys
    assert any(f["source_level"] == "global" for f in body["fields"])
    assert "checkpoint 规则优先" in body["instruction"]


def test_workflow_rule_compare_against_sampler_actuals(client):
    test_client, main = client
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)

    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sample_checkpoint.safetensors"}},
        "6": {
            "class_type": "KSampler",
            "inputs": {"steps": 20, "cfg": 7.0, "positive": ["1", 0], "negative": ["2", 0]},
        },
        "7": {"class_type": "EmptyLatentImage", "inputs": {"width": 704, "height": 1024, "batch_size": 1}},
        "8": {"class_type": "CLIPSetLastLayer", "inputs": {"clip": ["3", 0], "stop_at_clip_layer": -2}},
    }
    workflow_id = _import_sample(test_client, main, workflow)

    response = test_client.get(
        f"/api/workflows/{workflow_id}/rules/compare",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "registered"
    assert body["actuals"]["steps"] == 20
    assert body["actuals"]["width"] == 704
    assert body["actuals"]["height"] == 1024
    assert body["actuals"]["clip_skip"] == 2
    step_entry = next(c for c in body["comparisons"] if c["key"] == "GENERATION SETTINGS::Steps")
    assert step_entry["status"] in {"match", "out_of_range", "unknown"}
    assert "counts" in body


def test_workflow_rule_compare_lists_multiple_sampler_groups(client):
    test_client, main = client
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)

    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sample_checkpoint.safetensors"}},
        "6": {"class_type": "KSampler", "inputs": {"steps": 20, "positive": ["1", 0], "negative": ["2", 0]}},
        "9": {"class_type": "KSampler", "inputs": {"steps": 30, "positive": ["1", 0], "negative": ["2", 0]}},
    }
    workflow_id = _import_sample(test_client, main, workflow)

    response = test_client.get(
        f"/api/workflows/{workflow_id}/rules/compare",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert response.status_code == 200
    body = response.json()
    actuals = body["actuals"]
    assert actuals["sampler_group_count"] == 2
    assert actuals["steps"] == 20
    assert [g["params"].get("steps") for g in actuals["sampler_groups"]] == [20, 30]

    groups = body["sampler_group_comparisons"]
    assert len(groups) == 2
    step_by_group = []
    for group in groups:
        step_entry = next(c for c in group["comparisons"] if c["key"] == "GENERATION SETTINGS::Steps")
        step_by_group.append(step_entry["status"])
    assert step_by_group == ["match", "out_of_range"]
    # Per-group counts stay independent; the second group's mismatch must not
    # leak into the first group's verdict.
    assert groups[0]["counts"].get("match") == 1
    assert groups[1]["counts"].get("out_of_range") == 1


def test_workflow_rule_compare_unregistered_without_checkpoint(client):
    test_client, main = client
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)

    workflow = {"6": {"class_type": "KSampler", "inputs": {"steps": 20}}}
    workflow_id = _import_sample(test_client, main, workflow)

    response = test_client.get(
        f"/api/workflows/{workflow_id}/rules/compare",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unregistered"
    assert body["comparisons"] == []


def test_workflow_rule_prompt_unregistered_without_checkpoint(client):
    test_client, main = client
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)

    workflow = {"6": {"class_type": "KSampler", "inputs": {}}}
    workflow_id = _import_sample(test_client, main, workflow)

    response = test_client.get(
        f"/api/workflows/{workflow_id}/rules/prompt",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unregistered"
    assert body["fields"] == []


def test_prompt_candidates_returns_text_without_writing_draft(
    client, monkeypatch
):
    test_client, main = client
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)

    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sample_checkpoint.safetensors"}},
    }
    workflow_id = _import_sample(test_client, main, workflow)

    before = test_client.get(
        f"/api/workflows/{workflow_id}",
        headers={"Authorization": "Bearer test-session-token"},
    ).json()

    captured: dict = {}

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        captured["url"] = url
        captured["model"] = model
        captured["messages"] = messages
        captured["options"] = options
        return {
            "ok": True,
            "content": "candidate prompt text",
            "model": model,
            "error": None,
        }

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers={"Authorization": "Bearer test-session-token"},
        json={"model": "comfyui-prompt:v1", "task": "优化正向描述"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "comfyui-prompt:v1"
    assert body["candidates"] == ["candidate prompt text"]
    assert body["rule_status"] == "registered"

    # Only the trimmed rule context is sent; the unrelated checkpoint rule
    # must not leak into the messages.
    system_text = captured["messages"][0]["content"]
    assert "GENERATION SETTINGS::Steps" in system_text
    assert "other_checkpoint.safetensors" not in system_text

    # The working draft is untouched by candidate generation.
    after = test_client.get(
        f"/api/workflows/{workflow_id}",
        headers={"Authorization": "Bearer test-session-token"},
    ).json()
    assert after["working_prompts"] == before["working_prompts"]


def test_prompt_candidates_reports_local_model_failure(client, monkeypatch):
    test_client, main = client
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)

    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sample_checkpoint.safetensors"}},
    }
    workflow_id = _import_sample(test_client, main, workflow)

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        return {"ok": False, "content": None, "model": model, "error": "connection refused"}

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers={"Authorization": "Bearer test-session-token"},
        json={"model": "comfyui-prompt:v1"},
    )
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["online"] is False
    assert detail["error"] == "connection refused"


def test_prompt_candidates_rejects_empty_model(client):
    test_client, main = client
    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sample_checkpoint.safetensors"}},
    }
    workflow_id = _import_sample(test_client, main, workflow)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers={"Authorization": "Bearer test-session-token"},
        json={"model": "   "},
    )
    assert response.status_code == 422


def test_prompt_candidates_passes_whitelisted_options(client, monkeypatch):
    test_client, main = client
    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sample_checkpoint.safetensors"}},
    }
    workflow_id = _import_sample(test_client, main, workflow)

    captured: dict = {}

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        captured["options"] = options
        return {"ok": True, "content": "text", "model": model, "error": None}

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers={"Authorization": "Bearer test-session-token"},
        json={
            "model": "comfyui-prompt:v1",
            "temperature": 0.7,
            "top_p": 0.9,
            "num_ctx": 4096,
        },
    )
    assert response.status_code == 200
    assert captured["options"] == {
        "temperature": 0.7,
        "top_p": 0.9,
        "num_ctx": 4096,
    }


def test_prompt_candidates_rejects_out_of_range_options(client, monkeypatch):
    test_client, main = client
    workflow = {
        "5": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sample_checkpoint.safetensors"}},
    }
    workflow_id = _import_sample(test_client, main, workflow)

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        return {"ok": True, "content": "text", "model": model, "error": None}

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    out_of_range = [
        {"model": "m", "temperature": 3.0},
        {"model": "m", "top_p": 1.5},
        {"model": "m", "num_ctx": 128},
    ]
    for payload in out_of_range:
        response = test_client.post(
            f"/api/workflows/{workflow_id}/prompt/candidates",
            headers={"Authorization": "Bearer test-session-token"},
            json=payload,
        )
        assert response.status_code == 422


def test_workflow_rules_unregistered_without_checkpoint(client):
    test_client, main = client
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    _seed_context_rules(db)

    workflow = {"6": {"class_type": "KSampler", "inputs": {}}}
    workflow_id = _import_sample(test_client, main, workflow)

    response = test_client.get(
        f"/api/workflows/{workflow_id}/rules",
        headers={"Authorization": "Bearer test-session-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unregistered"
    assert body["reason"] == "no_checkpoint_recognized"
    assert body["checkpoint"] is None


def test_dedupe_prompt_tags_preserves_first_occurrence():
    text = "1girl, no lens flare, No Lens Flare, 1girl, soft light"
    assert workflow_api._dedupe_prompt_tags(text) == (
        "1girl, no lens flare, soft light"
    )


def test_dedupe_prompt_tags_keeps_lines_separate():
    text = "a, b, a\nc, b, c"
    assert workflow_api._dedupe_prompt_tags(text) == "a, b\nc, b"
