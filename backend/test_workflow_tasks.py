import pytest

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


def _ui_workflow():
    return {"nodes": [{"id": 1, "type": "CLIPTextEncode"}], "links": []}


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


def _patch_gates(monkeypatch, online=True, loaded=None, submit=None, vram=None):
    monkeypatch.setattr(
        workflow_api.comfy_client, "comfyui_online", lambda url: online
    )
    if vram is None:
        vram = {"online": True, "vram_free": 8 * 1024 ** 3, "error": None}
    monkeypatch.setattr(
        workflow_api.comfy_client, "probe_vram_free", lambda url, timeout=2.5: vram
    )
    if loaded is None:
        loaded = {"online": True, "reachable": True, "models": [], "error": None}
    monkeypatch.setattr(
        workflow_api.comfy_client, "loaded_ollama_models", lambda url: loaded
    )
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "release_resources",
        lambda ollama_url, comfyui_url, timeout=5.0: {
            "ollama_reachable": True,
            "ollama_stopped": [],
            "ollama_errors": [],
            "ollama_still_resident": [],
            "comfyui_freed": True,
            "comfyui_error": None,
        },
    )
    if submit is not None:
        monkeypatch.setattr(
            workflow_api.comfy_client, "submit_prompt", submit
        )


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


def test_submit_rejects_unconfirmed_workflow(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _patch_gates(monkeypatch)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 400


def test_submit_rejects_ui_format(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _ui_workflow())
    _patch_gates(monkeypatch)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 400
    assert "API" in response.json()["detail"]


def test_submit_rejects_when_comfyui_offline(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    _patch_gates(monkeypatch, online=False)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 502
    assert response.json()["detail"]["online"] is False


def test_submit_allows_when_ollama_process_not_running(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    loaded = {
        "online": False,
        "reachable": False,
        "models": [],
        "error": "connection refused",
    }

    def fake_submit(url, prompt_api, client_id, timeout=10.0):
        return {
            "ok": True,
            "prompt_id": "prompt-1",
            "node_errors": {},
            "error": None,
        }

    _patch_gates(monkeypatch, loaded=loaded, submit=fake_submit)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 200


def test_submit_rejects_when_ollama_response_malformed(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    loaded = {
        "online": False,
        "reachable": True,
        "models": [],
        "error": "malformed response",
    }
    _patch_gates(monkeypatch, loaded=loaded)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 502
    assert "卸载" in response.json()["detail"]["error"]


def test_submit_rejects_when_llm_still_loaded(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    loaded = {
        "online": True,
        "reachable": True,
        "models": ["qwen3:latest"],
        "error": None,
    }
    _patch_gates(monkeypatch, loaded=loaded)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 409
    assert response.json()["detail"]["loaded"] == ["qwen3:latest"]


def test_submit_rejects_when_vram_below_threshold(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    vram = {"online": True, "vram_free": 2 * 1024 ** 3, "error": None}
    _patch_gates(monkeypatch, vram=vram)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 409
    assert "5GB" in response.json()["detail"]["error"]


def test_submit_rejects_when_vram_unknown(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    vram = {"online": True, "vram_free": None, "error": "缺少显存空闲字段"}
    _patch_gates(monkeypatch, vram=vram)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 502
    assert "显存空闲" in response.json()["detail"]["error"]


def test_submit_records_task_when_gates_pass(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)

    def fake_submit(url, prompt_api, client_id, timeout=10.0):
        return {
            "ok": True,
            "prompt_id": "prompt-1",
            "node_errors": {},
            "error": None,
        }

    _patch_gates(monkeypatch, submit=fake_submit)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 200
    record = response.json()
    assert record["prompt_id"] == "prompt-1"
    assert record["workflow_id"] == workflow_id
    assert record["status"] == "submitted"

    listed = test_client.get(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    ).json()
    assert [item["prompt_id"] for item in listed["items"]] == ["prompt-1"]


def test_submit_guarded_prefers_frozen_snapshot(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)

    captured = {}

    def fake_submit(url, prompt_api, client_id, timeout=10.0):
        captured["prompt_api"] = prompt_api
        return {"ok": True, "prompt_id": "prompt-9", "node_errors": {}, "error": None}

    _patch_gates(monkeypatch, submit=fake_submit)

    frozen_working = {"99": {"class_type": "NOTE", "inputs": {"text": "frozen"}}}
    record = workflow_api._submit_guarded(
        workflow_id, snapshot={"working": frozen_working, "prompts": []}
    )
    assert record["prompt_id"] == "prompt-9"
    assert captured["prompt_api"] == frozen_working


def test_submit_guarded_rejects_broken_snapshot(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    _patch_gates(monkeypatch)

    with pytest.raises(workflow_api.HTTPException) as exc:
        workflow_api._submit_guarded(
            workflow_id, snapshot={"working": None, "prompts": []}
        )
    assert exc.value.status_code == 400


def test_submit_failure_records_no_task(client, monkeypatch):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)

    def fake_submit(url, prompt_api, client_id, timeout=10.0):
        return {
            "ok": False,
            "prompt_id": None,
            "node_errors": {"3": {"errors": ["missing model"]}},
            "error": "ComfyUI 报告节点错误",
        }

    _patch_gates(monkeypatch, submit=fake_submit)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 502
    assert response.json()["detail"]["node_errors"]

    listed = test_client.get(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    ).json()
    assert listed["items"] == []


def test_list_tasks_returns_404_for_unknown_workflow(client):
    test_client, _ = client
    response = test_client.get(
        "/api/workflows/00000000-0000-4000-8000-000000000000/tasks",
        headers=AUTH,
    )
    assert response.status_code == 404


def _submit_one(test_client, monkeypatch, prompt_id="prompt-1"):
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)

    def fake_submit(url, prompt_api, client_id, timeout=10.0):
        return {
            "ok": True,
            "prompt_id": prompt_id,
            "node_errors": {},
            "error": None,
        }

    _patch_gates(monkeypatch, submit=fake_submit)
    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 200
    return workflow_id


def test_refresh_maps_history_success(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    monkeypatch.setattr(
        workflow_api.comfy_client,
        "fetch_task_states",
        lambda url: {
            "online": True,
            "error": None,
            "running": [],
            "pending": [],
            "history": {
                "prompt-1": {
                    "status_str": "success",
                    "completed": True,
                    "outputs": {"9": [{"filename": "a.png"}]},
                }
            },
        },
    )

    body = test_client.get(
        f"/api/workflows/{workflow_id}/tasks?refresh=true", headers=AUTH
    ).json()
    record = body["items"][0]
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"9": [{"filename": "a.png"}]}
    assert body["sync_error"] is None


def test_refresh_maps_queue_running(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    monkeypatch.setattr(
        workflow_api.comfy_client,
        "fetch_task_states",
        lambda url: {
            "online": True,
            "error": None,
            "running": ["prompt-1"],
            "pending": [],
            "history": {},
        },
    )

    body = test_client.get(
        f"/api/workflows/{workflow_id}/tasks?refresh=true", headers=AUTH
    ).json()
    assert body["items"][0]["status"] == "running"


def test_refresh_marks_unknown_when_absent_from_queue_and_history(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    monkeypatch.setattr(
        workflow_api.comfy_client,
        "fetch_task_states",
        lambda url: {
            "online": True,
            "error": None,
            "running": [],
            "pending": [],
            "history": {},
        },
    )

    body = test_client.get(
        f"/api/workflows/{workflow_id}/tasks?refresh=true", headers=AUTH
    ).json()
    record = body["items"][0]
    assert record["status"] == "unknown"
    assert record["last_error"]


def test_refresh_marks_stalled_missing_task_failed(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    monkeypatch.setattr(
        workflow_api.comfy_client,
        "fetch_task_states",
        lambda url: {
            "online": True,
            "error": None,
            "running": [],
            "pending": [],
            "history": {},
        },
    )
    monkeypatch.setattr(workflow_api.comfy_tasks, "is_stalled", lambda record, now=None: True)

    body = test_client.get(
        f"/api/workflows/{workflow_id}/tasks?refresh=true", headers=AUTH
    ).json()
    record = body["items"][0]
    assert record["status"] == "failed"
    assert "重启 ComfyUI" in record["last_error"]


def test_refresh_marks_stalled_running_task_failed(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    monkeypatch.setattr(
        workflow_api.comfy_client,
        "fetch_task_states",
        lambda url: {
            "online": True,
            "error": None,
            "running": ["prompt-1"],
            "pending": [],
            "history": {},
        },
    )
    monkeypatch.setattr(workflow_api.comfy_tasks, "is_stalled", lambda record, now=None: True)

    body = test_client.get(
        f"/api/workflows/{workflow_id}/tasks?refresh=true", headers=AUTH
    ).json()
    record = body["items"][0]
    assert record["status"] == "failed"
    assert "卡死" in record["last_error"]


def test_refresh_offline_keeps_local_status(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    monkeypatch.setattr(
        workflow_api.comfy_client,
        "fetch_task_states",
        lambda url: {
            "online": False,
            "error": "connection refused",
            "running": [],
            "pending": [],
            "history": {},
        },
    )

    body = test_client.get(
        f"/api/workflows/{workflow_id}/tasks?refresh=true", headers=AUTH
    ).json()
    assert body["items"][0]["status"] == "submitted"
    assert body["sync_error"] == "connection refused"


def test_classify_history_status_is_conservative():
    classify = workflow_api.comfy_tasks.classify_history_status
    assert classify("success", True) == "succeeded"
    assert classify("success", None) == "succeeded"
    assert classify("error", False) == "failed"
    assert classify("interrupted", None) == "interrupted"
    assert classify(None, None) == "unknown"
    assert classify("running", None) == "unknown"


def _set_status(main, prompt_id: str, status: str) -> None:
    db = main.DATA_ROOT / "database" / "workbench.sqlite3"
    with workflow_api.model_identity.connect(db) as connection:
        workflow_api.comfy_tasks.ensure_schema(connection)
        workflow_api.comfy_tasks.update_status(connection, prompt_id, status)


def test_cancel_pending_task_dequeues_and_marks_interrupted(client, monkeypatch):
    test_client, main = client
    workflow_id = _submit_one(test_client, monkeypatch)
    dequeued: list[str] = []
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "cancel_pending",
        lambda url, pid: dequeued.append(pid) or {"ok": True, "error": None},
    )
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "interrupt_running",
        lambda url, pid: {"ok": False, "error": "should not call"},
    )

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks/prompt-1/cancel", headers=AUTH
    )
    assert response.status_code == 200
    assert dequeued == ["prompt-1"]
    assert response.json()["status"] == "interrupted"


def test_interrupt_running_task_targets_prompt(client, monkeypatch):
    test_client, main = client
    workflow_id = _submit_one(test_client, monkeypatch)
    _set_status(main, "prompt-1", "running")
    interrupted: list[str] = []
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "interrupt_running",
        lambda url, pid: interrupted.append(pid) or {"ok": True, "error": None},
    )
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "cancel_pending",
        lambda url, pid: {"ok": False, "error": "should not call"},
    )

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks/prompt-1/cancel", headers=AUTH
    )
    assert response.status_code == 200
    assert interrupted == ["prompt-1"]
    assert response.json()["status"] == "interrupted"


def test_cancel_refuses_terminal_task(client, monkeypatch):
    test_client, main = client
    workflow_id = _submit_one(test_client, monkeypatch)
    _set_status(main, "prompt-1", "succeeded")

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks/prompt-1/cancel", headers=AUTH
    )
    assert response.status_code == 409


def test_cancel_refuses_unknown_task(client, monkeypatch):
    test_client, main = client
    workflow_id = _submit_one(test_client, monkeypatch)
    _set_status(main, "prompt-1", "unknown")

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks/prompt-1/cancel", headers=AUTH
    )
    assert response.status_code == 409


def test_cancel_offline_leaves_local_status(client, monkeypatch):
    test_client, main = client
    workflow_id = _submit_one(test_client, monkeypatch)
    monkeypatch.setattr(
        workflow_api.comfy_client, "comfyui_online", lambda url: False
    )

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks/prompt-1/cancel", headers=AUTH
    )
    assert response.status_code == 502
    listed = test_client.get(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    ).json()
    assert listed["items"][0]["status"] == "submitted"


def test_cancel_unknown_prompt_returns_404(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks/does-not-exist/cancel", headers=AUTH
    )
    assert response.status_code == 404


def _fake_chat_record(called: dict):
    def fake_chat(url, model, messages, timeout=30.0, options=None):
        called["chat"] = True
        called["options"] = options
        return {"ok": True, "content": "candidate", "model": model, "error": None}

    return fake_chat


def test_retry_resubmits_failed_task(client, monkeypatch):
    test_client, main = client
    workflow_id = _submit_one(test_client, monkeypatch, prompt_id="prompt-1")
    _set_status(main, "prompt-1", "failed")

    calls: list[str] = []

    def fake_submit(url, prompt_api, client_id, timeout=10.0):
        pid = "prompt-2" if not calls else "prompt-x"
        calls.append(pid)
        return {"ok": True, "prompt_id": pid, "node_errors": {}, "error": None}

    monkeypatch.setattr(workflow_api.comfy_client, "submit_prompt", fake_submit)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks/prompt-1/retry", headers=AUTH
    )
    assert response.status_code == 200
    new_record = response.json()
    assert new_record["prompt_id"] == "prompt-2"
    assert new_record["status"] == "submitted"

    listed = test_client.get(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    ).json()["items"]
    by_id = {item["prompt_id"]: item for item in listed}
    assert set(by_id) == {"prompt-1", "prompt-2"}
    assert by_id["prompt-1"]["status"] == "failed"


def test_retry_refuses_succeeded_task(client, monkeypatch):
    test_client, main = client
    workflow_id = _submit_one(test_client, monkeypatch)
    _set_status(main, "prompt-1", "succeeded")

    submit_called = {"n": 0}
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "submit_prompt",
        lambda *args, **kwargs: submit_called.__setitem__("n", 1)
        or {"ok": False, "prompt_id": None, "node_errors": {}, "error": "x"},
    )

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks/prompt-1/retry", headers=AUTH
    )
    assert response.status_code == 409
    assert submit_called["n"] == 0


def test_retry_refuses_unknown_task(client, monkeypatch):
    test_client, main = client
    workflow_id = _submit_one(test_client, monkeypatch)
    _set_status(main, "prompt-1", "unknown")

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks/prompt-1/retry", headers=AUTH
    )
    assert response.status_code == 409


def test_retry_unknown_prompt_returns_404(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks/does-not-exist/retry", headers=AUTH
    )
    assert response.status_code == 404


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


def test_restore_version_endpoint_writes_back_and_adds_snapshot(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    _write(test_client, workflow_id, "v1 positive", "v1 negative")
    _write(test_client, workflow_id, "v2 positive", "v2 negative")

    versions = test_client.get(
        f"/api/workflows/{workflow_id}/versions", headers=AUTH
    ).json()["items"]
    stamp = versions[0]["stamp"]

    restored = test_client.post(
        f"/api/workflows/{workflow_id}/versions/{stamp}/restore",
        headers=AUTH,
    )
    assert restored.status_code == 200
    working = {
        p["role"]: p["text"]
        for p in restored.json()["workflow"]["working_prompts"]
    }
    assert working["positive"] == "v1 positive"
    assert working["negative"] == "v1 negative"

    after = test_client.get(
        f"/api/workflows/{workflow_id}/versions", headers=AUTH
    ).json()["items"]
    assert len(after) == 3


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
    assert response.json()["mode"] == "polish"
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
    assert response.json()["mode"] == "negative"
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
