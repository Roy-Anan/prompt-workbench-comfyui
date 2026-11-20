"""Tests for the external ComfyUI extension skeleton (no real backend/process)."""

from __future__ import annotations

import importlib.util
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
