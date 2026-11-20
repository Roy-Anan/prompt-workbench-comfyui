import generation_history
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


def _submit_one(test_client, monkeypatch, prompt_id="prompt-1"):
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)

    monkeypatch.setattr(
        workflow_api.comfy_client, "comfyui_online", lambda url: True
    )
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "probe_vram_free",
        lambda url, timeout=2.5: {
            "online": True,
            "vram_free": 8 * 1024 ** 3,
            "error": None,
        },
    )
    monkeypatch.setattr(
        workflow_api.comfy_client,
        "loaded_ollama_models",
        lambda url: {"online": True, "reachable": True, "models": [], "error": None},
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

    def fake_submit(url, prompt_api, client_id, timeout=10.0):
        return {
            "ok": True,
            "prompt_id": prompt_id,
            "node_errors": {},
            "error": None,
        }

    monkeypatch.setattr(workflow_api.comfy_client, "submit_prompt", fake_submit)
    response = test_client.post(
        f"/api/workflows/{workflow_id}/tasks", headers=AUTH
    )
    assert response.status_code == 200
    return workflow_id


def test_submit_records_frozen_snapshot(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    body = test_client.get("/api/history", headers=AUTH).json()
    assert len(body["items"]) == 1
    record = body["items"][0]
    assert record["prompt_id"] == "prompt-1"
    assert record["workflow_id"] == workflow_id
    assert record["positive_prompt"] == "positive"
    assert record["negative_prompt"] == "negative"
    assert record["model_snapshot"]["checkpoints"] == [
        {"node_id": "5", "file": "x.safetensors"}
    ]
    assert record["task_status"] == "submitted"


def test_snapshot_survives_later_draft_edit(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    write_response = test_client.post(
        f"/api/workflows/{workflow_id}/write",
        headers=AUTH,
        json={"positive_text": "changed later", "negative_text": ""},
    )
    assert write_response.status_code == 200

    record = test_client.get(
        "/api/history/prompt-1", headers=AUTH
    ).json()
    assert record["positive_prompt"] == "positive"
    assert record["negative_prompt"] == "negative"


def test_refresh_succeeded_attaches_images(client, monkeypatch):
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
                    "outputs": {
                        "9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}
                    },
                }
            },
        },
    )

    test_client.get(
        f"/api/workflows/{workflow_id}/tasks?refresh=true", headers=AUTH
    )
    record = test_client.get(
        "/api/history/prompt-1", headers=AUTH
    ).json()
    assert record["task_status"] == "succeeded"
    assert record["images"] == [
        {
            "node_id": "9",
            "filename": "a.png",
            "subfolder": "",
            "type": "output",
            "exists": None,
        }
    ]


def test_history_filter_by_workflow_and_404(client, monkeypatch):
    test_client, _ = client
    workflow_id = _submit_one(test_client, monkeypatch)

    filtered = test_client.get(
        f"/api/history?workflow_id={workflow_id}", headers=AUTH
    ).json()
    assert len(filtered["items"]) == 1

    other = test_client.get(
        "/api/history?workflow_id=00000000-0000-0000-0000-000000000000",
        headers=AUTH,
    ).json()
    assert other["items"] == []

    missing = test_client.get(
        "/api/history/does-not-exist", headers=AUTH
    )
    assert missing.status_code == 404


def test_history_filters_by_status_keyword_and_combo(client, monkeypatch):
    test_client, main = client
    workflow_id = _submit_one(test_client, monkeypatch, prompt_id="prompt-1")

    with main.model_identity.connect(
        main.datadir.database_path(main.DATA_ROOT)
    ) as connection:
        main.generation_history.ensure_schema(connection)
        main.generation_history.record_snapshot(
            connection,
            "prompt-2",
            workflow_id,
            [
                {"role": "positive", "text": "landscape scenery"},
                {"role": "negative", "text": "ugly"},
            ],
            {
                "model_nodes": [
                    {"node_id": "5", "inputs": {"ckpt_name": "landscape.safetensors"}}
                ],
                "lora_nodes": [],
            },
            "succeeded",
        )

    def query(params: str):
        return test_client.get(
            f"/api/history?{params}", headers=AUTH
        ).json()["items"]

    # Status exact match; unknown status returns empty rather than remapped.
    assert [item["prompt_id"] for item in query("status=submitted")] == ["prompt-1"]
    assert [item["prompt_id"] for item in query("status=succeeded")] == ["prompt-2"]
    assert query("status=weird-status") == []

    # Keyword matches prompt text and frozen model snapshot file names.
    assert [item["prompt_id"] for item in query("keyword=landscape")] == ["prompt-2"]
    assert [item["prompt_id"] for item in query("keyword=x.safetensors")] == ["prompt-1"]
    assert query("keyword=zzz-not-found") == []

    # Combined filters narrow both ways.
    assert len(query("status=succeeded&keyword=landscape")) == 1
    assert query("status=submitted&keyword=landscape") == []


def test_extract_images_supports_list_and_dict_shapes():
    list_shape = {"9": [{"filename": "a.png"}]}
    dict_shape = {"10": {"images": [{"filename": "b.png"}]}}
    assert generation_history.extract_images(list_shape)[0]["filename"] == "a.png"
    assert generation_history.extract_images(dict_shape)[0]["filename"] == "b.png"
    assert generation_history.extract_images(None) == []
    assert generation_history.extract_images({"9": {}}) == []


def test_model_snapshot_stays_conservative_on_unknown_report():
    snapshot = generation_history.extract_model_snapshot({})
    assert snapshot == {
        "checkpoints": [],
        "loras": [],
        "vaes": [],
        "clips": [],
        "resolution": None,
        "clip_skip": None,
        "samplers": [],
    }


def test_extract_env_params_freezes_resolution_clip_skip_and_samplers():
    report = {
        "latent": {"width": 768, "height": 1024},
        "clip_skip": {"clip_skip": 2, "stop_at_clip_layer": -2},
        "prompt_groups": [
            {
                "params": {
                    "steps": 20,
                    "cfg": 7.0,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "denoise": 1.0,
                    "seed": 12345,
                }
            },
            {"params": {"steps": 30}},
        ],
    }
    params = generation_history.extract_env_params(report)
    assert params["resolution"] == "768x1024"
    assert params["clip_skip"] == 2
    assert params["samplers"] == [
        {
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "dpmpp_2m",
            "scheduler": "karras",
            "denoise": 1.0,
        },
        {"steps": 30},
    ]

    empty = generation_history.extract_env_params(None)
    assert empty == {"resolution": None, "clip_skip": None, "samplers": []}


def test_annotate_exists_marks_present_and_missing(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    images = [
        {"node_id": "9", "filename": "a.png", "subfolder": "", "type": "output", "exists": None},
        {"node_id": "10", "filename": "missing.png", "subfolder": "", "type": "output", "exists": None},
    ]
    annotated = generation_history.annotate_exists(images, str(tmp_path))
    assert annotated[0]["exists"] is True
    assert annotated[1]["exists"] is False


def test_resolve_image_path_allows_inside_rejects_traversal_and_non_image(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.png").write_bytes(b"x")

    resolved = generation_history.resolve_image_path(
        str(tmp_path), "a.png", "sub"
    )
    assert resolved is not None and resolved.is_file()

    # Missing file, unconfigured dir, non-image extension, traversal and
    # path separators in the filename all resolve to None.
    assert generation_history.resolve_image_path(str(tmp_path), "missing.png", "") is None
    assert generation_history.resolve_image_path("", "a.png", "") is None
    assert generation_history.resolve_image_path(str(tmp_path), "x.txt", "") is None
    assert generation_history.resolve_image_path(
        str(tmp_path), "../a.png", ""
    ) is None
    assert generation_history.resolve_image_path(
        str(tmp_path), "sub/a.png", ""
    ) is None
    assert generation_history.resolve_image_path(
        str(tmp_path), "a.png", "../../etc"
    ) is None


def test_annotate_exists_none_without_output_dir():
    images = [{"filename": "a.png", "exists": None}]
    assert generation_history.annotate_exists(images, "")[0]["exists"] is None


def test_annotate_exists_rejects_path_escape(tmp_path):
    images = [{"filename": "../escape.png", "subfolder": "", "exists": None}]
    assert generation_history.annotate_exists(images, str(tmp_path))[0]["exists"] is False


def test_history_image_route_not_captured_by_prompt_id(client, tmp_path, monkeypatch):
    """The static image route must win over ``/api/history/{prompt_id}``.

    If registered after the dynamic route, ``image`` is captured as a prompt_id
    and the image endpoint returns 404 even when the file exists on disk.
    """
    test_client, main = client
    (tmp_path / "a.png").write_bytes(b"fake-png-bytes")
    monkeypatch.setattr(
        main, "load_settings", lambda: {"storage": {"output": str(tmp_path)}}
    )

    resp = test_client.get("/api/history/image?filename=a.png", headers=AUTH)

    assert resp.status_code == 200
    assert resp.content == b"fake-png-bytes"
