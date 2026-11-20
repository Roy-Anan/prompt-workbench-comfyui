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
        ext.NODE_DISPLAY_NAME_MAPPINGS["WorkbenchRuleContext"] == "工作台规则清单"
    )
    assert (EXT_DIR / ext.WEB_DIRECTORY).is_dir()


def test_rule_context_node_shape():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]
    types = cls.INPUT_TYPES()
    # Workflow-unbound: only the rule-store level/scope remain.
    assert set(types["required"]) == {"level", "scope_key", "backend_url"}
    assert types["required"]["level"][0] == ["checkpoint", "architecture", "global"]


def test_client_reports_error_without_backend(monkeypatch):
    ext = _load()
    import backend_client

    # Point at a closed loopback port so the probe fails fast and fails closed.
    monkeypatch.setattr(backend_client, "_base_url", lambda override="": "http://127.0.0.1:1")
    data = backend_client.list_rules("global")
    assert "error" in data

    node = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]()
    text, raw = node.load("global", "", "", "")
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


# --- Rule-list node ---------------------------------------------------------


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
    text, raw = node.load("global", "", "", "")

    assert "cfg" in text
    assert "7" in text
    assert '"items"' in raw


def test_rule_context_has_no_workflow_input():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleContext"]
    types = cls.INPUT_TYPES()
    assert "workflow_id" not in types["required"]
    assert "workflow_id" not in types["optional"]
    assert "source" not in types["required"]


# --- Prompt candidate node -------------------------------------------------


def test_extension_registers_candidate_node():
    ext = _load()
    assert "WorkbenchPromptCandidate" in ext.NODE_CLASS_MAPPINGS
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]
    assert cls.CATEGORY == "Workbench/LLM"
    assert cls.RETURN_TYPES == ("STRING", "STRING", "STRING", "STRING", "STRING")
    assert cls.RETURN_NAMES == (
        "positive_text",
        "negative_text",
        "raw_json",
        "clean_positive",
        "clean_negative",
    )
    assert (
        ext.NODE_DISPLAY_NAME_MAPPINGS["WorkbenchPromptCandidate"]
        == "工作台提示词候选生成"
    )
    # The node is canvas-bound: no backend workflow_id input remains.
    types = cls.INPUT_TYPES()
    assert "workflow_id" not in types["required"]
    assert "workflow_id" not in types["optional"]


def test_candidate_node_requires_model_only():
    ext = _load()
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()

    # Only the model is mandatory; no workflow binding is involved.
    positive, negative, raw, clean_positive, clean_negative = node.generate(
        "", "draft", "任务", "", ""
    )
    assert "模型名" in positive
    assert negative == ""
    assert clean_positive == ""
    assert clean_negative == ""


def test_candidate_node_renders_candidate(monkeypatch):
    ext = _load()
    import backend_client

    payload = {"model": "qwen3:latest", "mode": "polish", "candidates": ["润色后的提示词"]}
    monkeypatch.setattr(
        backend_client, "prompt_candidates", lambda wid, body, base="", tok="": payload
    )

    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    positive, negative, raw, clean_positive, clean_negative = node.generate(
        "qwen3:latest", "polish", "优化一下", "", ""
    )

    assert "润色后的提示词" in positive
    assert negative == ""
    assert "qwen3" in positive
    assert '"candidates"' in raw
    # Clean ports carry only the prompt body, never the header metadata.
    assert clean_positive == "润色后的提示词"
    assert clean_negative == ""


def test_candidate_node_passes_sampling_options(monkeypatch):
    ext = _load()
    import backend_client

    captured: dict = {}

    def fake_post(wid, body, base="", tok=""):
        captured.update(body)
        return {"model": body["model"], "candidates": ["x"]}

    monkeypatch.setattr(backend_client, "prompt_candidates", fake_post)
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    node.generate(
        "m", "draft", "任务", "", "",
        temperature=0.7, top_p=0.9, num_ctx=4096,
    )

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
    node.generate("m", "draft", "任务", "", "")

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
    positive, negative, raw, clean_positive, clean_negative = node.generate(
        "m", "draft", "任务", "", ""
    )
    assert "队列有待执行任务" in positive
    assert "409" in raw
    # Errors stay out of the clean ports so they cannot be written back as a prompt.
    assert clean_positive == ""
    assert clean_negative == ""


def test_candidate_node_has_no_workflow_input():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]
    types = cls.INPUT_TYPES()
    assert "workflow_id" not in types["required"]
    assert "workflow_id" not in types["optional"]


def test_candidate_node_model_is_combo():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]
    # Combo seeded with an empty placeholder; the extension JS fills the real
    # Ollama models and VALIDATE_INPUTS opts the field out of the enum check.
    assert cls.INPUT_TYPES()["required"]["model"][0] == [""]


def test_candidate_node_profile_combo_defaults_auto():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]
    profile = cls.INPUT_TYPES()["optional"]["prompt_profile"]
    assert profile[0] == ["auto"]
    assert profile[1]["default"] == "auto"


def test_candidate_node_pickers_are_combos():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]
    optional = cls.INPUT_TYPES()["optional"]
    assert optional["persona"][0] == ["none"]
    assert optional["genre"][0] == [""]
    assert optional["style"][0] == [""]


def test_candidate_node_validate_inputs_accepts_dynamic_pickers():
    ext = _load()
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    # VALIDATE_INPUTS opts the backend-filled pickers out of the static combo
    # enum check while keeping the normal check for the fixed "mode" combo.
    assert node.VALIDATE_INPUTS("m", "none", "auto", "", "") is True


def test_candidate_node_exposes_identity_and_layer_slots():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]
    optional = cls.INPUT_TYPES()["optional"]
    for slot in ("persona", "genre", "style", "positive_in", "negative_in"):
        assert slot in optional


def test_candidate_node_passes_identity_and_layers(monkeypatch):
    ext = _load()
    import backend_client

    captured: dict = {}

    def fake_post(wid, body, base="", tok=""):
        captured["wid"] = wid
        captured["body"] = body
        return {"model": body["model"], "candidates": ["x"]}

    monkeypatch.setattr(backend_client, "prompt_candidates", fake_post)
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    node.generate(
        "m", "draft", "写一段", "", "",
        persona="writing_assistant",
        prompt_profile="none",
        genre="yanqing",
        style="gufeng",
    )

    body = captured["body"]
    assert body["persona"] == "writing_assistant"
    assert body["prompt_profile"] == "none"
    assert body["genre"] == "yanqing"
    assert body["style"] == "gufeng"


def test_candidate_node_defaults_to_bare_model_and_auto_profile(monkeypatch):
    ext = _load()
    import backend_client

    captured: dict = {}

    def fake_post(wid, body, base="", tok=""):
        captured["body"] = body
        return {"model": body["model"], "candidates": ["x"]}

    monkeypatch.setattr(backend_client, "prompt_candidates", fake_post)
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    node.generate("m", "draft", "任务", "", "")

    body = captured["body"]
    assert body["persona"] == "none"
    assert "prompt_profile" not in body


def test_candidate_node_negative_goes_to_negative_port(monkeypatch):
    ext = _load()
    import backend_client

    monkeypatch.setattr(
        backend_client,
        "prompt_candidates",
        lambda wid, body, base="", tok="": {"model": "m", "candidates": ["lowres, bad hands"]},
    )
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    positive, negative, raw, clean_positive, clean_negative = node.generate(
        "m", "negative", "补负向", "", ""
    )
    assert positive == ""
    assert "bad hands" in negative
    assert clean_positive == ""
    assert clean_negative == "lowres, bad hands"


def test_candidate_node_always_uses_standalone_route(monkeypatch):
    ext = _load()
    import backend_client

    captured: dict = {}

    def fake_post(wid, body, base="", tok=""):
        captured["wid"] = wid
        return {"model": body["model"], "candidates": ["x"]}

    monkeypatch.setattr(backend_client, "prompt_candidates", fake_post)
    node = ext.NODE_CLASS_MAPPINGS["WorkbenchPromptCandidate"]()
    node.generate("m", "draft", "任务", "", "")
    assert captured["wid"] == ""
