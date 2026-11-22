import sqlite3

import generation_history
import history_sync
from workflow_service import analyze_workflow


def _workflow(checkpoint="sample_checkpoint.safetensors", lora="sample_lora.safetensors"):
    return {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "canvas positive", "clip": ["3", 0]},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "canvas negative", "clip": ["3", 0]},
        },
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "text_encoder.safetensors"}},
        "4": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": lora,
                "strength_model": 0.8,
                "strength_clip": 0.7,
                "model": ["5", 0],
                "clip": ["3", 0],
            },
        },
        "5": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
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
        "7": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 832, "height": 1216, "batch_size": 1},
        },
    }


def _entry(prompt_api, prompt_id="pid-1", status="success", completed=True):
    return {
        "prompt": [1, prompt_id, prompt_api, {"create_time": 1700000000000}],
        "status": {"status_str": status, "completed": completed, "messages": []},
        "outputs": {
            "9": {
                "images": [
                    {"filename": "img_00001_.png", "subfolder": "", "type": "output"}
                ]
            }
        },
    }


def _connection():
    conn = sqlite3.connect(":memory:")
    generation_history.ensure_schema(conn)
    return conn


def _references(workflow_id="wf-a", workflow=None):
    return [(workflow_id, analyze_workflow(workflow or _workflow()))]


def test_sync_matches_known_workflow_and_reverse_parses():
    conn = _connection()
    history = {"pid-1": _entry(_workflow())}

    result = history_sync.sync_from_history(conn, history, _references())

    assert result["added"] == 1
    assert result["matched"] == 1
    assert result["unmatched"] == 0
    record = generation_history.get_by_prompt_id(conn, "pid-1")
    assert record["workflow_id"] == "wf-a"
    assert record["positive_prompt"] == "canvas positive"
    assert record["negative_prompt"] == "canvas negative"
    assert record["task_status"] == "succeeded"
    assert record["images"] == [
        {
            "node_id": "9",
            "filename": "img_00001_.png",
            "subfolder": "",
            "type": "output",
            "exists": None,
        }
    ]
    ckpts = [item["file"] for item in record["model_snapshot"]["checkpoints"]]
    assert ckpts == ["sample_checkpoint.safetensors"]


def test_sync_unmatched_when_checkpoint_differs():
    conn = _connection()
    foreign = _workflow(checkpoint="other_checkpoint.safetensors")
    history = {"pid-2": _entry(foreign)}

    result = history_sync.sync_from_history(conn, history, _references())

    assert result["added"] == 1
    assert result["matched"] == 0
    assert result["unmatched"] == 1
    record = generation_history.get_by_prompt_id(conn, "pid-2")
    assert record["workflow_id"] == ""


def test_sync_skips_existing_prompt_id():
    conn = _connection()
    first = {"pid-3": _entry(_workflow())}
    history_sync.sync_from_history(conn, first, _references())

    repeat = {"pid-3": _entry(_workflow())}
    result = history_sync.sync_from_history(conn, repeat, _references())

    assert result["added"] == 0
    assert result["skipped"] == 1
    record = generation_history.get_by_prompt_id(conn, "pid-3")
    # Existing row is never overwritten.
    assert record["workflow_id"] == "wf-a"


def test_sync_skips_malformed_entry():
    conn = _connection()
    history = {
        "pid-4": {"status": {"status_str": "success"}, "outputs": {}},
        "pid-5": _entry(_workflow()),
    }

    result = history_sync.sync_from_history(conn, history, _references())

    assert result["added"] == 1
    assert result["skipped"] == 1
    assert generation_history.get_by_prompt_id(conn, "pid-4") is None
    assert generation_history.get_by_prompt_id(conn, "pid-5") is not None


def test_rebind_reassigns_and_unbinds_record():
    conn = _connection()
    history = {"pid-6": _entry(_workflow())}
    history_sync.sync_from_history(conn, history, _references())

    rebound = history_sync.rebind_workflow(conn, "pid-6", "wf-b")
    assert rebound is not None
    assert rebound["workflow_id"] == "wf-b"

    unbound = history_sync.rebind_workflow(conn, "pid-6", "")
    assert unbound is not None
    assert unbound["workflow_id"] == ""


def test_rebind_returns_none_for_unknown_record():
    conn = _connection()
    assert history_sync.rebind_workflow(conn, "missing", "wf-b") is None
