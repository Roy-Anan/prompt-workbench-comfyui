"""Tests for the external ComfyUI extension skeleton (no real backend/process)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = REPO_ROOT / "comfyui_extension" / "workbench"


def _load():
    spec = importlib.util.spec_from_file_location("workbench_ext", EXT_DIR / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extension_registers_rule_context_node():
    ext = _load()
    assert "WorkbenchRuleContext" in ext.NODE_CLASS_MAPPINGS
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]
    assert cls.CATEGORY == "Workbench/Context"
    assert cls.RETURN_TYPES == ("STRING", "STRING")
    assert (
        ext.NODE_DISPLAY_NAME_MAPPINGS["WorkbenchRuleContext"] == "工作台规则上下文"
    )
    assert (EXT_DIR / ext.WEB_DIRECTORY).is_dir()


def test_rule_context_node_shape():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]
    types = cls.INPUT_TYPES()
    assert set(types["required"]) == {
        "source",
        "workflow_id",
        "level",
        "scope_key",
        "backend_url",
    }
    assert types["required"]["source"][0] == ["workflow", "rules"]
    assert types["required"]["level"][0] == ["checkpoint", "architecture", "global"]
    assert types["required"]["workflow_id"][0] == [""]


def test_client_reports_error_without_backend(monkeypatch):
    ext = _load()
    import backend_client

    # Point at a closed loopback port so the probe fails fast and fails closed.
    monkeypatch.setattr(backend_client, "_base_url", lambda override="": "http://127.0.0.1:1")
    data = backend_client.list_rules("global")
    assert "error" in data

    node = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]()
    text, raw = node.load("rules", "", "global", "", "", "")
    assert "无法读取规则" in text
    assert raw == "{}"


def _write_token_file(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")


def test_client_list_workflows_reads_endpoint(monkeypatch):
    import backend_client

    captured: dict = {}

    def fake_get(path, base_url="", token="", params=None, auth=True):
        captured["path"] = path
        return {"items": [{"id": "wf-1", "filename": "a.json", "confirmed": True}]}

    monkeypatch.setattr(backend_client, "_get", fake_get)
    data = backend_client.list_workflows()

    assert captured["path"] == "/api/workflows"
    assert data["items"][0]["id"] == "wf-1"


def test_client_reads_persisted_node_token(monkeypatch, tmp_path):
    import backend_client

    token_file = tmp_path / "node_token.json"
    _write_token_file(token_file, "file-node-token")
    monkeypatch.setattr(backend_client, "_default_token_file", lambda: token_file)
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)

    assert backend_client._token() == "file-node-token"


def test_client_explicit_token_overrides_file(monkeypatch, tmp_path):
    import backend_client

    token_file = tmp_path / "node_token.json"
    _write_token_file(token_file, "file-node-token")
    monkeypatch.setattr(backend_client, "_default_token_file", lambda: token_file)

    assert backend_client._token("explicit-token") == "explicit-token"


def test_client_env_token_overrides_file(monkeypatch, tmp_path):
    import backend_client

    token_file = tmp_path / "node_token.json"
    _write_token_file(token_file, "file-node-token")
    monkeypatch.setattr(backend_client, "_default_token_file", lambda: token_file)
    monkeypatch.setenv("WORKBENCH_SESSION_TOKEN", "env-token")

    assert backend_client._token() == "env-token"


def test_client_missing_token_file_fails_open(monkeypatch, tmp_path):
    import backend_client

    monkeypatch.setattr(
        backend_client, "_default_token_file", lambda: tmp_path / "nope.json"
    )
    monkeypatch.delenv("WORKBENCH_SESSION_TOKEN", raising=False)

    assert backend_client._token() == ""


def test_client_default_token_file_points_at_repo_userdata(monkeypatch):
    import backend_client

    monkeypatch.delenv("WORKBENCH_NODE_TOKEN_FILE", raising=False)
    monkeypatch.delenv("WORKBENCH_DATA_ROOT", raising=False)

    expected = REPO_ROOT / "userdata" / "settings" / "node_token.json"
    assert backend_client._default_token_file() == expected


def test_extension_registers_release_node():
    ext = _load()
    assert "WorkbenchReleaseVram" in ext.NODE_CLASS_MAPPINGS
    assert (
        ext.NODE_DISPLAY_NAME_MAPPINGS["WorkbenchReleaseVram"]
        == "工作台释放显存/卸载LLM"
    )
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchReleaseVram"]
    assert cls.CATEGORY == "Workbench/System"
    assert cls.RETURN_TYPES == ("STRING", "STRING")


def test_release_node_skipped_when_disabled(monkeypatch):
    ext = _load()
    import backend_client

    called = {"n": 0}

    def fake_release(base_url="", token=""):
        called["n"] += 1
        return {}

    monkeypatch.setattr(backend_client, "release_vram", fake_release)
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchReleaseVram"]()
    text, raw = node.release(False, "", "")

    assert "跳过" in text
    assert called["n"] == 0


def test_release_node_summarizes_result(monkeypatch):
    ext = _load()
    import backend_client

    payload = {
        "ollama_reachable": True,
        "ollama_stopped": ["m:v1"],
        "ollama_errors": [],
        "ollama_still_resident": [],
        "comfyui_freed": True,
        "comfyui_error": None,
    }
    monkeypatch.setattr(
        backend_client, "release_vram", lambda base_url="", token="": payload
    )

    node = ext.NODE_CLASS_MAPPINGS["WorkbenchReleaseVram"]()
    text, raw = node.release(True, "", "")

    assert "已卸载 LLM：m:v1" in text
    assert "ComfyUI 模型显存已释放" in text
    assert "m:v1" in raw


def test_release_node_reports_error(monkeypatch):
    ext = _load()
    import backend_client

    monkeypatch.setattr(
        backend_client,
        "release_vram",
        lambda base_url="", token="": {"error": "URLError: x"},
    )

    node = ext.NODE_CLASS_MAPPINGS["WorkbenchReleaseVram"]()
    text, raw = node.release(True, "", "")

    assert "卸载失败" in text
    assert raw == "{}"


# --- Merged rule-context node: workflow mode -----------------------------


def test_rule_context_workflow_requires_id():
    ext = _load()
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]()
    text, raw = node.load("workflow", "", "global", "", "", "")
    assert "workflow_id" in text
    assert raw == "{}"


def test_rule_context_workflow_renders_resolved_and_conflict(monkeypatch):
    ext = _load()
    import backend_client

    payload = {
        "status": "registered",
        "checkpoint": "x.safetensors",
        "architecture_label": "SD1.5",
        "fields": [
            {"key": "base", "status": "resolved", "value": "SD1.5", "source_level": "architecture"},
            {"key": "step", "status": "conflict", "message": "两处取值不一致"},
        ],
        "conflicts": [],
        "unknown": [],
        "instruction": "未提供的参数不要臆造。",
    }
    monkeypatch.setattr(
        backend_client, "workflow_rule_prompt", lambda wid, base="", tok="": payload
    )

    node = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]()
    text, raw = node.load("workflow", "wf-1", "global", "", "", "")

    assert "SD1.5" in text
    assert "base" in text
    assert "冲突" in text
    assert "不要臆造" in text
    assert '"step"' in raw


def test_rule_context_workflow_reports_backend_error(monkeypatch):
    ext = _load()
    import backend_client

    monkeypatch.setattr(
        backend_client,
        "workflow_rule_prompt",
        lambda wid, base="", tok="": {"error": "URLError: x"},
    )
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]()
    text, raw = node.load("workflow", "wf-1", "global", "", "", "")
    assert "无法读取规则上下文" in text
    assert raw == "{}"


def test_rule_context_rules_renders_items(monkeypatch):
    ext = _load()
    import backend_client

    payload = {
        "items": [
            {
                "rule_key": "cfg",
                "value": "7",
                "level": "global",
                "scope_key": "-",
                "note": "保守",
            }
        ]
    }
    monkeypatch.setattr(
        backend_client, "list_rules", lambda level, scope="", base="", tok="": payload
    )

    node = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]()
    text, raw = node.load("rules", "", "global", "", "", "")

    assert "cfg" in text
    assert "7" in text
    assert '"items"' in raw


def test_rule_context_workflow_id_is_combo_placeholder():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]
    # Seeded combo; the extension JS fills real options from the backend list.
    assert cls.INPUT_TYPES()["required"]["workflow_id"][0] == [""]


# --- Prompt candidate node -------------------------------------------------


def test_extension_registers_candidate_node():
    ext = _load()
    assert "WorkbenchPromptCandidate" in ext.NODE_CLASS_MAPPINGS
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]
    assert cls.CATEGORY == "Workbench/LLM"
    assert cls.RETURN_TYPES == ("STRING", "STRING")
    assert (
        ext.NODE_DISPLAY_NAME_MAPPINGS["WorkbenchPromptCandidate"]
        == "工作台提示词候选生成"
    )


def test_candidate_node_requires_id_and_model():
    ext = _load()
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()

    text, _ = node.generate("", "m", "draft", "任务", "", "")
    assert "workflow_id" in text

    text, _ = node.generate("wf-1", "", "draft", "任务", "", "")
    assert "模型名" in text


def test_candidate_node_renders_candidate(monkeypatch):
    ext = _load()
    import backend_client

    payload = {"model": "qwen3:latest", "mode": "polish", "candidates": ["润色后的提示词"]}
    monkeypatch.setattr(
        backend_client, "prompt_candidates", lambda wid, body, base="", tok="": payload
    )

    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    text, raw = node.generate("wf-1", "qwen3:latest", "polish", "优化一下", "", "")

    assert "润色后的提示词" in text
    assert "qwen3" in text
    assert '"candidates"' in raw


def test_candidate_node_passes_sampling_options(monkeypatch):
    ext = _load()
    import backend_client

    captured: dict = {}

    def fake_post(wid, body, base="", tok=""):
        captured.update(body)
        return {"model": body["model"], "candidates": ["x"]}

    monkeypatch.setattr(backend_client, "prompt_candidates", fake_post)
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    node.generate("wf-1", "m", "draft", "任务", "", "", 0.7, 0.9, 4096)

    assert captured["temperature"] == 0.7
    assert captured["top_p"] == 0.9
    assert captured["num_ctx"] == 4096


def test_candidate_node_omits_unset_options(monkeypatch):
    ext = _load()
    import backend_client

    captured: dict = {}

    def fake_post(wid, body, base="", tok=""):
        captured.update(body)
        return {"model": body["model"], "candidates": ["x"]}

    monkeypatch.setattr(backend_client, "prompt_candidates", fake_post)
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    node.generate("wf-1", "m", "draft", "任务", "", "")

    assert "temperature" not in captured
    assert "top_p" not in captured
    assert "num_ctx" not in captured


def test_candidate_node_reports_backend_error(monkeypatch):
    ext = _load()
    import backend_client

    monkeypatch.setattr(
        backend_client,
        "prompt_candidates",
        lambda wid, body, base="", tok="": {"error": "队列有待执行任务", "status": 409},
    )
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    text, raw = node.generate("wf-1", "m", "draft", "任务", "", "")
    assert "队列有待执行任务" in text
    assert "409" in raw


def test_candidate_node_workflow_id_is_combo_placeholder():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]
    assert cls.INPUT_TYPES()["required"]["workflow_id"][0] == [""]
