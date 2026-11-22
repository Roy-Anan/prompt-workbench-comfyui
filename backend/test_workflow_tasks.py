import pytest

import comfy_tasks
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
    response = test_client.post(
        "/api/workflows/import",
        headers=AUTH,
        json={"filename": "sample.json", "workflow": workflow},
    )
    assert response.status_code == 200
    return response.json()["id"]


def _confirm(test_client, workflow_id):
    response = test_client.post(
        f"/api/workflows/{workflow_id}/confirm",
        headers=AUTH,
        json={"positive_node_ids": ["1"], "negative_node_ids": ["2"]},
    )
    assert response.status_code == 200


def test_user_message_keeps_protected_terms():
    message = workflow_api._user_message(
        [], None, "draft", ["白发少女", "ru"]
    )
    assert "白发少女" in message
    assert "ru" in message
    assert "保留原写法" in message


def test_user_message_keeps_locked_fragments():
    message = workflow_api._user_message(
        [], None, "draft", None, ["红色瞳孔", "校服"]
    )
    assert "红色瞳孔" in message
    assert "校服" in message
    assert "原样保留" in message


def test_user_message_fresh_skips_existing_prompt():
    working = [{"role": "positive", "text": "白发少女"}, {"role": "negative", "text": "模糊"}]
    message = workflow_api._user_message(
        working, None, "fresh", None, None, "positive-in", "negative-in"
    )
    assert "当前工作流现有 Prompt" not in message
    assert "白发少女" not in message
    assert "模糊" not in message
    assert "positive-in" not in message
    assert "negative-in" not in message
    assert "从零生成" in message


def test_user_message_continue_keeps_existing_prompt():
    working = [{"role": "positive", "text": "白发少女"}]
    message = workflow_api._user_message(working, None, "continue")
    assert "白发少女" in message
    assert "续写" in message or "继续延展" in message


def test_clean_locked_fragments_normalizes():
    cleaned = workflow_api._clean_locked_fragments(
        ["  红色瞳孔  ", "", "红色瞳孔", "校服"]
    )
    assert cleaned == ["红色瞳孔", "校服"]


def test_clean_locked_fragments_rejects_non_string():
    import pytest

    with pytest.raises(ValueError):
        workflow_api._clean_locked_fragments(["校服", 5])


def test_clean_locked_fragments_rejects_non_list():
    import pytest

    with pytest.raises(ValueError):
        workflow_api._clean_locked_fragments("not-a-list")


def test_classify_history_status_is_conservative():
    classify = comfy_tasks.classify_history_status
    assert classify("success", True) == "succeeded"
    assert classify("success", None) == "succeeded"
    assert classify("error", False) == "failed"
    assert classify("interrupted", None) == "interrupted"
    assert classify(None, None) == "unknown"
    assert classify("running", None) == "unknown"


def _fake_chat_record(called: dict):
    def fake_chat(url, model, messages, timeout=30.0, options=None):
        called["chat"] = True
        called["options"] = options
        return {"ok": True, "content": "candidate", "model": model, "error": None}

    return fake_chat


def test_versions_empty_for_fresh_workflow(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())

    response = test_client.get(
        f"/api/workflows/{workflow_id}/versions", headers=AUTH
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_versions_unknown_workflow_returns_404(client):
    test_client, _ = client
    response = test_client.get(
        "/api/workflows/00000000-0000-4000-8000-000000000000/versions",
        headers=AUTH,
    )
    assert response.status_code == 404


def _write(test_client, workflow_id, positive, negative):
    response = test_client.post(
        f"/api/workflows/{workflow_id}/write",
        headers=AUTH,
        json={"positive_text": positive, "negative_text": negative},
    )
    assert response.status_code == 200
    return response.json()


def test_restore_unknown_workflow_returns_404(client):
    test_client, _ = client
    response = test_client.post(
        "/api/workflows/00000000-0000-4000-8000-000000000000/versions/20000101-000000-000000/restore",
        headers=AUTH,
    )
    assert response.status_code == 404


def test_candidate_blocked_when_comfyui_queue_active(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": True, "error": None},
    )
    called: dict = {}
    monkeypatch.setattr(llm_client, "chat", _fake_chat_record(called))

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=AUTH,
        json={"model": "qwen3:latest"},
    )
    assert response.status_code == 409
    assert called.get("chat") is not True


def test_candidate_allowed_when_queue_idle(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": False, "error": None},
    )
    called: dict = {}
    monkeypatch.setattr(llm_client, "chat", _fake_chat_record(called))

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=AUTH,
        json={"model": "qwen3:latest"},
    )
    assert response.status_code == 200
    assert called.get("chat") is True


def test_candidate_allowed_when_comfyui_offline(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": False, "active": False, "error": "refused"},
    )
    called: dict = {}
    monkeypatch.setattr(llm_client, "chat", _fake_chat_record(called))

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=AUTH,
        json={"model": "qwen3:latest"},
    )
    assert response.status_code == 200
    assert called.get("chat") is True


def test_candidate_mode_polish_passes_instruction(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": False, "error": None},
    )
    captured: dict = {}

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        captured["messages"] = messages
        return {"ok": True, "content": "polished", "model": model, "error": None}

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=AUTH,
        json={"model": "qwen3:latest", "mode": "polish"},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "润色"
    user_message = captured["messages"][-1]["content"]
    assert "润色" in user_message


def test_candidate_mode_negative_passes_instruction(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": False, "error": None},
    )
    captured: dict = {}

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        captured["messages"] = messages
        return {"ok": True, "content": "negative", "model": model, "error": None}

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=AUTH,
        json={"model": "qwen3:latest", "mode": "negative"},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "负向词"
    assert response.json()["mode_negative_output"] is True
    user_message = captured["messages"][-1]["content"]
    assert "负向" in user_message


def test_candidate_rejects_unknown_mode(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": False, "error": None},
    )
    monkeypatch.setattr(llm_client, "chat", _fake_chat_record({}))

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=AUTH,
        json={"model": "qwen3:latest", "mode": "rocket"},
    )
    assert response.status_code == 422


def test_candidate_passes_locked_fragments(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": False, "error": None},
    )
    captured: dict = {}

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        captured["messages"] = messages
        return {"ok": True, "content": "kept", "model": model, "error": None}

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=AUTH,
        json={
            "model": "qwen3:latest",
            "locked_fragments": ["红色瞳孔", "校服"],
        },
    )
    assert response.status_code == 200
    user_message = captured["messages"][-1]["content"]
    assert "红色瞳孔" in user_message
    assert "校服" in user_message
    assert "原样保留" in user_message


def test_candidate_rejects_non_string_locked(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": False, "error": None},
    )
    monkeypatch.setattr(llm_client, "chat", _fake_chat_record({}))

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=AUTH,
        json={"model": "qwen3:latest", "locked_fragments": ["校服", 5]},
    )
    assert response.status_code == 422


def test_candidate_strips_outer_fence(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": False, "error": None},
    )

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        return {
            "ok": True,
            "content": "```prompt\n1girl, smile\n```",
            "model": model,
            "error": None,
        }

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=AUTH,
        json={"model": "qwen3:latest"},
    )
    assert response.status_code == 200
    assert response.json()["candidates"] == ["1girl, smile"]


def test_candidate_rejects_empty_cleaned(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "queue_has_active_task",
        lambda url: {"online": True, "active": False, "error": None},
    )

    def fake_chat(url, model, messages, timeout=30.0, options=None):
        return {"ok": True, "content": "```\n```", "model": model, "error": None}

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/prompt/candidates",
        headers=AUTH,
        json={"model": "qwen3:latest"},
    )
    assert response.status_code == 502


def test_draft_create_read_update_delete_roundtrip(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())

    create = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"name": "草稿一", "positive": "1girl", "negative": "blurry", "note": "备注"},
    )
    assert create.status_code == 200
    draft_id = create.json()["id"]

    listed = test_client.get(
        f"/api/workflows/{workflow_id}/drafts", headers=AUTH
    ).json()["items"]
    assert len(listed) == 1
    assert listed[0]["name"] == "草稿一"

    fetched = test_client.get(
        f"/api/workflows/{workflow_id}/drafts/{draft_id}", headers=AUTH
    ).json()
    assert fetched["positive"] == "1girl"
    assert fetched["negative"] == "blurry"

    updated = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"draft_id": draft_id, "name": "草稿一改", "positive": "1girl, smile", "negative": ""},
    )
    assert updated.status_code == 200
    assert updated.json()["positive"] == "1girl, smile"

    deleted = test_client.delete(
        f"/api/workflows/{workflow_id}/drafts/{draft_id}", headers=AUTH
    )
    assert deleted.status_code == 200
    after = test_client.get(
        f"/api/workflows/{workflow_id}/drafts", headers=AUTH
    ).json()["items"]
    assert after == []


def test_draft_tag_rename_merges_across_drafts(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())

    first = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={
            "name": "草稿甲",
            "positive": "p1",
            "negative": "",
            "tags": ["旧标签", "通用"],
        },
    )
    assert first.status_code == 200
    second = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"name": "草稿乙", "positive": "p2", "negative": "", "tags": ["旧标签"]},
    )
    assert second.status_code == 200

    renamed = test_client.post(
        "/api/workflows/tags/rename",
        headers=AUTH,
        json={"old_tag": "旧标签", "new_tag": "新标签"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["renamed"] == 2

    listed = test_client.get(
        f"/api/workflows/{workflow_id}/drafts", headers=AUTH
    ).json()["items"]
    tags_by_name = {item["name"]: item["tags"] for item in listed}
    assert tags_by_name["草稿甲"] == ["新标签", "通用"]
    assert tags_by_name["草稿乙"] == ["新标签"]


def test_draft_tag_rename_rejects_same_tag(client):
    test_client, _ = client
    _import(test_client, _api_workflow())

    response = test_client.post(
        "/api/workflows/tags/rename",
        headers=AUTH,
        json={"old_tag": "相同", "new_tag": "相同"},
    )
    assert response.status_code == 400


def test_draft_update_preserves_name_and_note(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())

    create = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"name": "保留名", "positive": "p1", "negative": "n1", "note": "保留备注"},
    )
    assert create.status_code == 200
    draft_id = create.json()["id"]

    # 前端更新场景：沿用原名称与备注，只覆盖正/负向 Prompt。
    updated = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={
            "draft_id": draft_id,
            "name": "保留名",
            "positive": "p2",
            "negative": "",
            "note": "保留备注",
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "保留名"
    assert body["note"] == "保留备注"
    assert body["positive"] == "p2"
    assert body["negative"] == ""


def test_draft_update_changes_name_and_note_without_self_collision(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())

    first = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"name": "旧名", "positive": "p1", "note": "旧备注"},
    )
    assert first.status_code == 200
    draft_id = first.json()["id"]

    other = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"name": "另一条", "positive": "p3"},
    )
    assert other.status_code == 200

    # 更新可改名 / 改备注；自身旧名不构成同名冲突。
    renamed = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={
            "draft_id": draft_id,
            "name": "新名",
            "positive": "p2",
            "note": "新备注",
        },
    )
    assert renamed.status_code == 200
    body = renamed.json()
    assert body["name"] == "新名"
    assert body["note"] == "新备注"
    assert body["positive"] == "p2"

    listed = test_client.get(
        f"/api/workflows/{workflow_id}/drafts", headers=AUTH
    ).json()["items"]
    names = {entry["name"] for entry in listed}
    assert names == {"新名", "另一条"}

    # 改成与另一条同名仍被拒绝（同名去重只排除自身）。
    clash = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"draft_id": draft_id, "name": "另一条", "positive": "p4"},
    )
    assert clash.status_code == 400


def test_draft_rejects_empty_prompt_duplicate_name_and_missing(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())

    empty = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"name": "空", "positive": "", "negative": ""},
    )
    assert empty.status_code == 400

    first = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"name": "同名", "positive": "a"},
    )
    assert first.status_code == 200
    duplicate = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"name": "同名", "positive": "b"},
    )
    assert duplicate.status_code == 400

    missing_wf = test_client.post(
        "/api/workflows/00000000-0000-4000-8000-000000000000/drafts",
        headers=AUTH,
        json={"name": "x", "positive": "a"},
    )
    assert missing_wf.status_code == 404


def test_draft_favorite_toggle_roundtrip(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())

    create = test_client.post(
        f"/api/workflows/{workflow_id}/drafts",
        headers=AUTH,
        json={"name": "收藏草稿", "positive": "p1"},
    )
    assert create.status_code == 200
    draft_id = create.json()["id"]
    assert create.json()["favored"] is False

    on = test_client.post(
        f"/api/workflows/{workflow_id}/drafts/{draft_id}/favorite", headers=AUTH
    )
    assert on.status_code == 200
    assert on.json()["favored"] is True
    assert test_client.get(
        f"/api/workflows/{workflow_id}/drafts", headers=AUTH
    ).json()["items"][0]["favored"] is True

    off = test_client.post(
        f"/api/workflows/{workflow_id}/drafts/{draft_id}/favorite", headers=AUTH
    )
    assert off.status_code == 200
    assert off.json()["favored"] is False

    missing = test_client.post(
        f"/api/workflows/{workflow_id}/drafts/11111111-1111-4111-8111-111111111111/favorite",
        headers=AUTH,
    )
    assert missing.status_code == 400


def test_release_vram_endpoint(client, monkeypatch):
    test_client, _ = client
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
    response = test_client.post("/api/system/release-vram", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == payload
