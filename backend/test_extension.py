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


def test_extension_registers_rule_query_node():
    ext = _load()
    assert "WorkbenchRuleQuery" in ext.NODE_CLASS_MAPPINGS
    assert ext.NODE_DISPLAY_NAME_MAPPINGS["WorkbenchRuleQuery"] == "工作台规则查询"
    assert (EXT_DIR / ext.WEB_DIRECTORY).is_dir()


def test_rule_query_node_shape():
    ext = _load()
    cls = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleQuery"]
    types = cls.INPUT_TYPES()
    assert set(types["required"]) == {"level", "scope_key", "backend_url"}
    assert types["required"]["level"][0] == ["checkpoint", "architecture", "global"]
    assert cls.RETURN_TYPES == ("STRING", "STRING")
    assert cls.CATEGORY == "Workbench/Rules"


def test_client_reports_error_without_backend(monkeypatch):
    ext = _load()
    import backend_client

    # Point at a closed loopback port so the probe fails fast and fails closed.
    monkeypatch.setattr(backend_client, "_base_url", lambda override="": "http://127.0.0.1:1")
    data = backend_client.list_rules("global")
    assert "error" in data

    node = ext.NODE_CLASS_MAPPINGS["WorkbenchRuleQuery"]()
    text, raw = node.query_rules("global", "", "", "")
    assert "无法读取规则" in text
    assert raw == "{}"


def _write_token_file(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")


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
