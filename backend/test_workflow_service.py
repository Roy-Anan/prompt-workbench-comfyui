import copy
import json
import pytest
from workflow_service import (
    WorkflowError,
    WorkflowStore,
    analyze_workflow,
    detect_format,
)
def sample_api_workflow():
    return {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "old positive",
                "clip": ["3", 0],
            },
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "old negative",
                "clip": ["3", 0],
            },
        },
        "3": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "text_encoder.safetensors",
            },
        },
        "4": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": "sample_lora.safetensors",
                "strength_model": 0.8,
                "strength_clip": 0.7,
                "model": ["5", 0],
                "clip": ["3", 0],
            },
        },
        "5": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": "sample_checkpoint.safetensors",
            },
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 123456,
                "steps": 24,
                "cfg": 6.5,
                "positive": ["1", 0],
                "negative": ["2", 0],
                "model": ["4", 0],
            },
        },
    }
def test_detect_api_workflow():
    assert detect_format(sample_api_workflow()) == "api"
def test_api_parser_finds_prompt_roles_models_and_lora():
    report = analyze_workflow(sample_api_workflow())
    assert report["format"] == "api"
    assert report["safe_prompt_write_supported"] is True
    roles = {
        item["node_id"]: item["roles"]
        for item in report["prompt_nodes"]
    }
    assert roles["1"] == ["positive"]
    assert roles["2"] == ["negative"]
    model_types = {item["class_type"] for item in report["model_nodes"]}
    assert "CLIPLoader" in model_types
    assert "CheckpointLoaderSimple" in model_types
    assert len(report["lora_nodes"]) == 1
    assert report["lora_nodes"][0]["node_id"] == "4"
def test_api_parser_extracts_latent_size():
    workflow = sample_api_workflow()
    workflow["7"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 832, "height": 1216, "batch_size": 1},
    }
    report = analyze_workflow(workflow)
    assert report["latent"] == {"node_id": "7", "width": 832, "height": 1216}


def test_api_parser_extracts_clip_skip_from_last_layer_node():
    workflow = sample_api_workflow()
    workflow["8"] = {
        "class_type": "CLIPSetLastLayer",
        "inputs": {"clip": ["3", 0], "stop_at_clip_layer": -2},
    }
    report = analyze_workflow(workflow)
    assert report["clip_skip"] == {"node_id": "8", "clip_skip": 2, "stop_at_clip_layer": -2}


def test_api_parser_clip_skip_none_without_node():
    report = analyze_workflow(sample_api_workflow())
    assert report["clip_skip"] is None


def test_api_parser_clip_skip_ignores_non_negative_stop_layer():
    workflow = sample_api_workflow()
    workflow["8"] = {
        "class_type": "CLIPSetLastLayer",
        "inputs": {"clip": ["3", 0], "stop_at_clip_layer": 0},
    }
    report = analyze_workflow(workflow)
    assert report["clip_skip"] is None


def test_api_parser_without_latent_reports_none():
    report = analyze_workflow(sample_api_workflow())
    assert report["latent"] is None


def test_api_parser_lists_all_latent_and_clip_skip_nodes():
    workflow = sample_api_workflow()
    workflow["7"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 832, "height": 1216, "batch_size": 1},
    }
    workflow["10"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 768, "height": 1152, "batch_size": 1},
    }
    workflow["8"] = {
        "class_type": "CLIPSetLastLayer",
        "inputs": {"clip": ["3", 0], "stop_at_clip_layer": -2},
    }
    workflow["11"] = {
        "class_type": "CLIPSetLastLayer",
        "inputs": {"clip": ["3", 0], "stop_at_clip_layer": -3},
    }
    report = analyze_workflow(workflow)

    # Top-level facts keep the first node; the remaining nodes are listed,
    # never merged or silently dropped.
    assert report["latent"] == {"node_id": "7", "width": 832, "height": 1216}
    assert [n["node_id"] for n in report["latent_nodes"]] == ["7", "10"]
    assert report["clip_skip"] == {"node_id": "8", "clip_skip": 2, "stop_at_clip_layer": -2}
    assert [n["node_id"] for n in report["clip_skip_nodes"]] == ["8", "11"]
    assert [n["clip_skip"] for n in report["clip_skip_nodes"]] == [2, 3]


def test_prompt_write_changes_only_confirmed_text_fields(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    source = sample_api_workflow()
    imported = store.import_workflow("sample_api.json", source)
    workflow_id = imported["id"]
    store.confirm_prompt_nodes(
        workflow_id,
        positive_node_ids=["1"],
        negative_node_ids=["2"],
    )
    result = store.write_prompts(
        workflow_id,
        positive_text="new positive prompt",
        negative_text="new negative prompt",
    )
    assert result["ok"] is True
    assert len(result["changes"]) == 2
    original = json.loads(
        (tmp_path / "workflows" / workflow_id / "original.json")
        .read_text(encoding="utf-8")
    )
    working = json.loads(
        (tmp_path / "workflows" / workflow_id / "working.json")
        .read_text(encoding="utf-8")
    )
    # The original file is never changed.
    assert original == source
    assert original["1"]["inputs"]["text"] == "old positive"
    assert original["2"]["inputs"]["text"] == "old negative"
    # Only the two explicitly whitelisted text fields are different.
    assert working["1"]["inputs"]["text"] == "new positive prompt"
    assert working["2"]["inputs"]["text"] == "new negative prompt"
    assert working["6"]["inputs"]["seed"] == 123456
    assert working["6"]["inputs"]["steps"] == 24
    assert working["6"]["inputs"]["cfg"] == 6.5
    assert working["5"] == original["5"]
    assert working["4"] == original["4"]
    # New writes no longer auto-create version snapshots.
    versions = tmp_path / "workflows" / workflow_id / "versions"
    assert list(versions.glob("*.json")) == []


def test_restore_version_rejects_missing_snapshot(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    workflow_id = imported["id"]
    store.confirm_prompt_nodes(
        workflow_id,
        positive_node_ids=["1"],
        negative_node_ids=["2"],
    )
    store.write_prompts(
        workflow_id,
        positive_text="v1 positive",
        negative_text="v1 negative",
    )

    with pytest.raises(WorkflowError, match="版本快照不存在"):
        store.restore_version(workflow_id, "20000101-000000-000000")

    with pytest.raises(WorkflowError, match="版本快照路径越界|无效的版本标识"):
        store.restore_version(workflow_id, "../escape")


def test_write_rejects_unconfirmed_nodes(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow(
        "sample_api.json",
        sample_api_workflow(),
    )
    with pytest.raises(WorkflowError):
        store.write_prompts(
            imported["id"],
            positive_text="should not write",
            negative_text=None,
        )
def test_write_rejects_non_whitelist_mutation(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow(
        "sample_api.json",
        sample_api_workflow(),
    )
    workflow_id = imported["id"]
    store.confirm_prompt_nodes(
        workflow_id,
        positive_node_ids=["1"],
        negative_node_ids=["2"],
    )
    working_file = (
        tmp_path / "workflows" / workflow_id / "working.json"
    )
    working = json.loads(working_file.read_text(encoding="utf-8"))
    working["6"]["inputs"]["seed"] = 999999
    working_file.write_text(
        json.dumps(working, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with pytest.raises(WorkflowError, match="非白名单"):
        store.write_prompts(
            workflow_id,
            positive_text="must be rejected",
            negative_text=None,
        )
def test_structural_diff_rejects_non_whitelist_value_change(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow(
        "sample_api.json",
        sample_api_workflow(),
    )
    workflow_id = imported["id"]
    store.confirm_prompt_nodes(
        workflow_id,
        positive_node_ids=["1"],
        negative_node_ids=["2"],
    )
    working_file = tmp_path / "workflows" / workflow_id / "working.json"
    working = json.loads(working_file.read_text(encoding="utf-8"))
    working["6"]["inputs"]["steps"] = 30
    working_file.write_text(
        json.dumps(working, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with pytest.raises(WorkflowError, match="非白名单"):
        store.write_prompts(
            workflow_id,
            positive_text="must be rejected",
            negative_text=None,
        )
def test_write_rejects_changed_original_fingerprint(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow(
        "sample_api.json",
        sample_api_workflow(),
    )
    workflow_id = imported["id"]
    store.confirm_prompt_nodes(
        workflow_id,
        positive_node_ids=["1"],
        negative_node_ids=["2"],
    )
    original_file = (
        tmp_path / "workflows" / workflow_id / "original.json"
    )
    original = json.loads(original_file.read_text(encoding="utf-8"))
    original["6"]["inputs"]["seed"] = 999
    original_file.write_text(
        json.dumps(original, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with pytest.raises(WorkflowError, match="指纹"):
        store.write_prompts(
            workflow_id,
            positive_text="must be rejected",
            negative_text=None,
        )
def test_ui_workflow_is_parsed_but_not_writable():
    ui_workflow = {
        "nodes": [
            {
                "id": 1,
                "type": "CLIPTextEncode",
                "inputs": [],
                "widgets_values": ["example prompt"],
            }
        ],
        "links": [],
    }
    assert detect_format(ui_workflow) == "ui"
    report = analyze_workflow(ui_workflow)
    assert report["format"] == "ui"
    assert report["safe_prompt_write_supported"] is False
    assert report["prompt_nodes"][0]["node_id"] == "1"
    assert report["prompt_nodes"][0]["editable"] is False
def test_ui_parser_reads_loader_and_lora_widget_values():
    ui_workflow = {
        "nodes": [
            {
                "id": 1,
                "type": "CheckpointLoaderSimple",
                "inputs": [
                    {
                        "name": "ckpt_name",
                        "type": "COMBO",
                        "widget": {"name": "ckpt_name"},
                        "link": None,
                    }
                ],
                "widgets_values": ["intorealismUltra_v80.safetensors"],
            },
            {
                "id": 10,
                "type": "LoraLoader",
                "inputs": [
                    {"name": "model", "type": "MODEL", "link": 1},
                    {"name": "clip", "type": "CLIP", "link": 2},
                    {
                        "name": "lora_name",
                        "type": "COMBO",
                        "widget": {"name": "lora_name"},
                        "link": None,
                    },
                    {
                        "name": "strength_model",
                        "type": "FLOAT",
                        "widget": {"name": "strength_model"},
                        "link": None,
                    },
                    {
                        "name": "strength_clip",
                        "type": "FLOAT",
                        "widget": {"name": "strength_clip"},
                        "link": None,
                    },
                ],
                "widgets_values": [
                    "suyuexifull_c1-st6000.safetensors",
                    0.7,
                    0.85,
                ],
            },
        ],
        "links": [],
    }
    report = analyze_workflow(ui_workflow)
    assert report["format"] == "ui"
    assert report["model_nodes"][0]["inputs"] == {
        "ckpt_name": "intorealismUltra_v80.safetensors"
    }
    assert report["lora_nodes"][0]["inputs"] == {
        "lora_name": "suyuexifull_c1-st6000.safetensors",
        "strength_model": 0.7,
        "strength_clip": 0.85,
    }
def test_detect_api_workflow_wrapped_in_nodes():
    wrapped = {"nodes": sample_api_workflow()}
    assert detect_format(wrapped) == "api"
    report = analyze_workflow(wrapped)
    assert report["format"] == "api"
    roles = {
        item["node_id"]: item["roles"]
        for item in report["prompt_nodes"]
    }
    assert roles["1"] == ["positive"]
    assert roles["2"] == ["negative"]
def test_passthrough_conditioning_node_trace_continues_upstream():
    workflow = {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "positive via combine", "clip": ["5", 0]},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "negative", "clip": ["5", 0]},
        },
        "3": {
            "class_type": "ConditioningCombine",
            "inputs": {"conditioning_1": ["1", 0], "conditioning_2": ["4", 0]},
        },
        "4": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["1", 0]},
        },
        "5": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "text_encoder.safetensors"},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1,
                "positive": ["3", 0],
                "negative": ["2", 0],
            },
        },
    }
    report = analyze_workflow(workflow)
    group = next(
        item for item in report["prompt_groups"] if item["sampler_node_id"] == "6"
    )
    assert "1" in group["positive_node_ids"]
    roles = {
        item["node_id"]: item["roles"]
        for item in report["prompt_nodes"]
    }
    assert "positive" in roles["1"]
def test_reimport_and_reopen_persists_across_store_instances(tmp_path):
    data_root = tmp_path / "workflows"
    first_store = WorkflowStore(data_root)
    source = sample_api_workflow()
    imported = first_store.import_workflow("sample_api.json", source)
    workflow_id = imported["id"]
    first_store.confirm_prompt_nodes(
        workflow_id,
        positive_node_ids=["1"],
        negative_node_ids=["2"],
    )
    first_store.write_prompts(
        workflow_id,
        positive_text="persisted positive",
        negative_text="persisted negative",
    )
    # 重新打开：模拟进程重启后用同一数据目录重建 Store。
    reopened = WorkflowStore(data_root)
    detail = reopened.get_workflow(workflow_id)
    prompts = {
        item["role"]: item["text"] for item in detail["working_prompts"]
    }
    assert prompts["positive"] == "persisted positive"
    assert prompts["negative"] == "persisted negative"
    assert detail["manifest"]["confirmed"] is True
    listing = reopened.list_workflows()
    assert any(item["id"] == workflow_id for item in listing)
    # 原始文件在重开后仍未被修改。
    original = json.loads(
        (data_root / workflow_id / "original.json").read_text(encoding="utf-8")
    )
    assert original == source
def test_invalid_workflow_is_rejected():
    with pytest.raises(WorkflowError):
        detect_format({"hello": "world"})
def test_import_rejects_oversized_workflow(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    huge = {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "x" * (21 * 1024 * 1024)},
        }
    }
    with pytest.raises(WorkflowError, match="20 MB"):
        store.import_workflow("too-large.json", huge)


def test_real_exported_api_workflow_full_writeback_roundtrip(tmp_path):
    """Real ComfyUI 导出的 API workflow：识别 -> 确认 -> 写回 -> 重开持久化 -> 快照。

    结构与真机样本 suyuexisdxlapi.json 对齐：Checkpoint + LoRA + KSampler +
    空 Latent + VAE 解码 + 保存图像，正向节点 4、负向节点 5。
    """
    source = {
        "1": {
            "inputs": {"ckpt_name": "intorealismUltra_v80.safetensors"},
            "class_type": "CheckpointLoaderSimple",
        },
        "4": {
            "inputs": {
                "text": "suyuexi, 1girl, old positive",
                "clip": ["10", 1],
            },
            "class_type": "CLIPTextEncode",
        },
        "5": {
            "inputs": {
                "text": "cartoon, anime, old negative",
                "clip": ["10", 1],
            },
            "class_type": "CLIPTextEncode",
        },
        "6": {
            "inputs": {"width": 704, "height": 1024, "batch_size": 1},
            "class_type": "EmptyLatentImage",
        },
        "7": {
            "inputs": {
                "seed": 231661496131269,
                "steps": 35,
                "cfg": 5.5,
                "sampler_name": "dpmpp_2m_sde",
                "scheduler": "karras",
                "denoise": 1,
                "model": ["10", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
            },
            "class_type": "KSampler",
        },
        "8": {
            "inputs": {"samples": ["7", 0], "vae": ["1", 2]},
            "class_type": "VAEDecode",
        },
        "9": {
            "inputs": {"filename_prefix": "pony_sdxl", "images": ["8", 0]},
            "class_type": "SaveImage",
        },
        "10": {
            "inputs": {
                "lora_name": "suyuexifull_c1-st6000.safetensors",
                "strength_model": 0.7,
                "strength_clip": 0.85,
                "model": ["1", 0],
                "clip": ["1", 1],
            },
            "class_type": "LoraLoader",
        },
    }
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("suyuexisdxlapi.json", source)
    report = imported["report"]

    assert report["format"] == "api"
    assert report["node_count"] == 8
    assert report["safe_prompt_write_supported"] is True
    assert report["model_nodes"][0]["inputs"]["ckpt_name"] == (
        "intorealismUltra_v80.safetensors"
    )
    lora = report["lora_nodes"][0]
    assert lora["inputs"]["lora_name"] == "suyuexifull_c1-st6000.safetensors"
    assert lora["inputs"]["strength_model"] == 0.7
    assert lora["inputs"]["strength_clip"] == 0.85
    group = report["prompt_groups"][0]
    assert group["positive_node_ids"] == ["4"]
    assert group["negative_node_ids"] == ["5"]

    store.confirm_prompt_nodes(imported["id"], ["4"], ["5"])
    written = store.write_prompts(
        imported["id"],
        positive_text="suyuexi, 1girl, new positive",
        negative_text="cartoon, anime, new negative",
    )
    assert written["ok"] is True
    assert [item["node_id"] for item in written["changes"]] == ["4", "5"]

    # 重开持久化：同一数据目录重建 Store。
    reopened = WorkflowStore(tmp_path / "workflows")
    detail = reopened.get_workflow(imported["id"])
    persisted = {item["node_id"]: item["text"] for item in detail["working_prompts"]}
    assert persisted["4"] == "suyuexi, 1girl, new positive"
    assert persisted["5"] == "cartoon, anime, new negative"

    # 原始文件未被改动；新写入不再自动留存版本快照。
    original = json.loads(
        (tmp_path / "workflows" / imported["id"] / "original.json")
        .read_text(encoding="utf-8")
    )
    assert original == source
    versions = list(
        (tmp_path / "workflows" / imported["id"] / "versions").glob("*.json")
    )
    assert versions == []


def test_draft_create_list_read_does_not_touch_working(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    workflow_id = imported["id"]

    before_working = (
        tmp_path / "workflows" / workflow_id / "working.json"
    ).read_text(encoding="utf-8")

    draft = store.save_draft(
        workflow_id,
        draft_id=None,
        name="日常头像",
        positive="1girl, soft light",
        negative="blurry, lowres",
        note="备用方向",
    )
    assert draft["id"]
    assert draft["name"] == "日常头像"

    listed = store.list_drafts(workflow_id)
    assert len(listed) == 1
    assert listed[0]["positive"] == "1girl, soft light"
    assert listed[0]["negative"] == "blurry, lowres"

    fetched = store.get_draft(workflow_id, draft["id"])
    assert fetched["note"] == "备用方向"

    # Working copy and version snapshots are untouched by draft saving.
    after_working = (
        tmp_path / "workflows" / workflow_id / "working.json"
    ).read_text(encoding="utf-8")
    assert after_working == before_working
    assert not (tmp_path / "workflows" / workflow_id / "versions").exists()


def test_draft_update_rejects_duplicate_name_and_missing(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    workflow_id = imported["id"]

    first = store.save_draft(
        workflow_id, None, "头像 A", "positive A", None, ""
    )
    store.save_draft(workflow_id, None, "头像 B", "positive B", None, "")

    # Updating the first draft keeps its own name; a second draft cannot reuse it.
    updated = store.save_draft(
        workflow_id, first["id"], "头像 A 改", "positive A2", "neg A2", "备注"
    )
    assert updated["positive"] == "positive A2"
    assert updated["negative"] == "neg A2"

    with pytest.raises(WorkflowError, match="同名"):
        store.save_draft(
            workflow_id, first["id"], "头像 B", "x", None, ""
        )

    with pytest.raises(WorkflowError, match="无效的草稿标识"):
        store.get_draft(workflow_id, "not-a-uuid")

    with pytest.raises(WorkflowError, match="草稿不存在"):
        store.get_draft(workflow_id, "11111111-1111-4111-8111-111111111111")


def test_draft_recycle_bin_and_isolation_between_workflows(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    first = store.import_workflow("a.json", sample_api_workflow())
    second_source = copy.deepcopy(sample_api_workflow())
    second_source["1"]["inputs"]["text"] = "other positive"
    second = store.import_workflow("b.json", second_source)

    draft = store.save_draft(
        first["id"], None, "草稿一", "positive", None, ""
    )
    # Drafts are scoped to their own workflow.
    assert store.list_drafts(second["id"]) == []

    # Soft delete: hidden by default, but still listed in the recycle bin.
    store.delete_draft(first["id"], draft["id"])
    assert store.list_drafts(first["id"]) == []
    trashed = store.list_drafts(first["id"], include_trashed=True)
    assert len(trashed) == 1
    assert trashed[0]["trashed"] is True

    # Restore brings it back into the active draft box.
    store.restore_draft(first["id"], draft["id"])
    active = store.list_drafts(first["id"])
    assert len(active) == 1
    assert active[0]["trashed"] is False

    # Soft delete again, then purge permanently from the recycle bin.
    store.delete_draft(first["id"], draft["id"])
    store.purge_draft(first["id"], draft["id"])
    assert store.list_drafts(first["id"], include_trashed=True) == []

    with pytest.raises(WorkflowError, match="草稿不存在"):
        store.purge_draft(first["id"], draft["id"])


def test_import_same_workflow_is_deduplicated_by_fingerprint(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    first = store.import_workflow("same.json", sample_api_workflow())
    second = store.import_workflow("same.json", sample_api_workflow())

    assert second["id"] == first["id"]
    assert second["duplicate"] is True
    assert first.get("duplicate") is not True
    assert len(store.list_workflows()) == 1

    # A changed workflow produces a separate record.
    changed = copy.deepcopy(sample_api_workflow())
    changed["1"]["inputs"]["text"] = "changed positive"
    third = store.import_workflow("changed.json", changed)
    assert third["id"] != first["id"]
    assert third.get("duplicate") is not True
    assert len(store.list_workflows()) == 2


def test_draft_rejects_empty_prompt_and_blank_name(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    workflow_id = imported["id"]

    with pytest.raises(WorkflowError, match="至少需要一段"):
        store.save_draft(workflow_id, None, "空草稿", "", "", "")

    with pytest.raises(WorkflowError, match="名称不能为空"):
        store.save_draft(workflow_id, None, "   ", "positive", None, "")


def test_draft_favored_toggle_and_preserved_on_update(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    workflow_id = imported["id"]

    draft = store.save_draft(
        workflow_id, None, "收藏测试", "positive", None, ""
    )
    assert store.list_drafts(workflow_id)[0]["favored"] is False

    toggled = store.toggle_favored(workflow_id, draft["id"])
    assert toggled["favored"] is True
    assert store.list_drafts(workflow_id)[0]["favored"] is True

    # Updating the draft keeps the favored marker and does not reset it.
    updated = store.save_draft(
        workflow_id, draft["id"], "收藏测试改", "positive2", None, ""
    )
    assert updated["favored"] is True

    back = store.toggle_favored(workflow_id, draft["id"])
    assert back["favored"] is False

    with pytest.raises(WorkflowError, match="草稿不存在"):
        store.toggle_favored(
            workflow_id, "11111111-1111-4111-8111-111111111111"
        )


def test_draft_pinned_toggle_and_sorts_first(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    workflow_id = imported["id"]

    draft_a = store.save_draft(workflow_id, None, "草稿A", "positive-a", None, "")
    draft_b = store.save_draft(workflow_id, None, "草稿B", "positive-b", None, "")

    # Newest update first: B before A.
    names = [d["name"] for d in store.list_drafts(workflow_id)]
    assert names == ["草稿B", "草稿A"]

    store.toggle_pinned(workflow_id, draft_a["id"])
    names = [d["name"] for d in store.list_drafts(workflow_id)]
    assert names == ["草稿A", "草稿B"]
    assert store.list_drafts(workflow_id)[0]["pinned"] is True

    # Updating the draft keeps the pinned marker.
    updated = store.save_draft(
        workflow_id, draft_a["id"], "草稿A改", "positive-a2", None, ""
    )
    assert updated["pinned"] is True

    back = store.toggle_pinned(workflow_id, draft_a["id"])
    assert back["pinned"] is False

    with pytest.raises(WorkflowError, match="草稿不存在"):
        store.toggle_pinned(
            workflow_id, "11111111-1111-4111-8111-111111111111"
        )


def test_draft_tags_cleaned_and_preserved_on_update(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    workflow_id = imported["id"]

    draft = store.save_draft(
        workflow_id,
        None,
        "标签草稿",
        "positive",
        None,
        "",
        tags=[" 头像 ", "头像", "  ", 123, "角色"],
    )

    listed = store.list_drafts(workflow_id)
    assert listed[0]["tags"] == ["头像", "角色"]
    fetched = store.get_draft(workflow_id, draft["id"])
    assert fetched["tags"] == ["头像", "角色"]

    # Updating replaces the tag set; an empty tag list clears it.
    updated = store.save_draft(
        workflow_id, draft["id"], "标签草稿改", "positive2", None, "", tags=["夜景"]
    )
    assert updated["tags"] == ["夜景"]

    cleared = store.save_draft(
        workflow_id, draft["id"], "标签草稿改", "positive3", None, "", tags=[]
    )
    assert cleared["tags"] == []


def test_draft_source_history_link_and_lookup(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    workflow_id = imported["id"]

    draft = store.save_draft(
        workflow_id,
        None,
        "索引草稿",
        "positive",
        None,
        "",
        source_history_id="pid-abc-123",
    )
    listed = store.list_drafts(workflow_id)
    assert listed[0]["source_history_id"] == "pid-abc-123"

    linked = store.find_linked_draft(workflow_id, "pid-abc-123")
    assert linked == {"id": draft["id"], "name": "索引草稿"}
    assert store.find_linked_draft(workflow_id, "pid-unknown") is None

    # An unbound record (empty workflow_id, e.g. canvas-direct generation)
    # has no draft folder to resolve; it links to nothing instead of raising.
    assert store.find_linked_draft("", "pid-abc-123") is None
    assert store.find_linked_draft("   ", "pid-abc-123") is None

    # The linked workflow was deleted: a dangling history record must not
    # raise FileNotFoundError from the draft lookup.
    assert store.find_linked_draft(
        "11111111-1111-1111-1111-111111111111", "pid-abc-123"
    ) is None

    # Non-string / blank source refs normalize back to None.
    plain = store.save_draft(
        workflow_id, None, "无来源草稿", "positive2", None, "", source_history_id=123
    )
    assert plain["source_history_id"] is None
    assert store.find_linked_draft(workflow_id, "pid-abc-123") is not None


def test_draft_env_snapshot_frozen_on_save_and_kept_on_update(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    workflow_id = imported["id"]

    draft = store.save_draft(workflow_id, None, "快照草稿", "positive", None, "")
    snapshot = store.list_drafts(workflow_id)[0]["env_snapshot"]

    assert snapshot["checkpoints"][0]["file"] == "sample_checkpoint.safetensors"
    assert snapshot["loras"][0]["file"] == "sample_lora.safetensors"
    assert snapshot["clips"][0]["file"] == "text_encoder.safetensors"
    assert snapshot["samplers"] == [{"steps": 24, "cfg": 6.5}]
    assert snapshot["resolution"] is None
    assert snapshot["clip_skip"] is None

    # A later prompt edit keeps the originally frozen snapshot.
    updated = store.save_draft(
        workflow_id, draft["id"], "快照草稿改", "positive2", None, ""
    )
    assert updated["env_snapshot"]["checkpoints"] == snapshot["checkpoints"]
    assert updated["env_snapshot"]["samplers"] == snapshot["samplers"]


def test_save_to_source_overwrites_matching_file_with_bak(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    workflow_id = imported["id"]
    store.confirm_prompt_nodes(
        workflow_id, positive_node_ids=["1"], negative_node_ids=["2"]
    )
    store.write_prompts(workflow_id, "new positive", "new negative")

    source_dir = tmp_path / "comfy_workflows"
    source_dir.mkdir()
    source_file = source_dir / "sample_api.json"
    source_file.write_text('{"stale": true}', encoding="utf-8")

    result = store.save_to_source(workflow_id, source_dir)
    assert result["filename"] == "sample_api.json"

    saved = json.loads(source_file.read_text(encoding="utf-8"))
    assert saved["1"]["inputs"]["text"] == "new positive"
    assert saved["2"]["inputs"]["text"] == "new negative"

    backup = source_dir / "sample_api.json.bak"
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8")) == {"stale": True}


def test_save_to_source_rejects_missing_source_dir(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    with pytest.raises(WorkflowError, match="目录不存在或未配置"):
        store.save_to_source(imported["id"], tmp_path / "no_such_dir")


def test_save_to_source_rejects_missing_target_file(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    imported = store.import_workflow("sample_api.json", sample_api_workflow())
    source_dir = tmp_path / "comfy_workflows"
    source_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="源文件不存在"):
        store.save_to_source(imported["id"], source_dir)


def test_save_to_source_rejects_ui_format(tmp_path):
    store = WorkflowStore(tmp_path / "workflows")
    ui_workflow = {"nodes": [], "links": [], "groups": [], "config": {}, "extra": {}}
    imported = store.import_workflow("ui.json", ui_workflow)
    source_dir = tmp_path / "comfy_workflows"
    source_dir.mkdir()
    (source_dir / "ui.json").write_text("{}", encoding="utf-8")
    with pytest.raises(WorkflowError, match="API 格式"):
        store.save_to_source(imported["id"], source_dir)
