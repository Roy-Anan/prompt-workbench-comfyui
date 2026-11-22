"""Tests for the extension local read-only proxy (no ComfyUI server/backend)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = REPO_ROOT / "comfyui_extension" / "workbench"

if str(EXT_DIR) not in sys.path:
    sys.path.insert(0, str(EXT_DIR))

import backend_client  # noqa: E402
import proxy  # noqa: E402


def test_proxy_whitelisted_routes_map_to_backend_paths():
    assert proxy.backend_path_for("health") == "/api/health"
    assert proxy.backend_path_for("overview") == "/api/overview"
    assert proxy.backend_path_for("sidebar-summary") == "/api/sidebar/summary"
    assert proxy.backend_path_for("workflows") == "/api/workflows"
    assert proxy.backend_path_for("modes") == "/api/modes"


def test_proxy_rejects_unknown_suffix():
    assert proxy.backend_path_for("rules") is None
    assert proxy.backend_path_for("") is None
    assert proxy.backend_path_for("../../etc/passwd") is None


def test_forward_degrades_to_502_when_backend_offline(monkeypatch):
    monkeypatch.setattr(backend_client, "_base_url", lambda override="": "http://127.0.0.1:1")

    status, payload = proxy.forward("/api/health")

    assert status == 502
    assert "error" in payload


def test_register_routes_returns_false_without_server_module():
    # The plain pytest environment has no ComfyUI ``server`` package.
    assert proxy.register_routes() is False
