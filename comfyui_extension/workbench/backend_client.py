"""Minimal loopback HTTP client for the workbench backend.

Runs inside the ComfyUI integrated Python and talks to the workbench FastAPI
backend over 127.0.0.1. Only the standard library is used so the extension
loads before any third-party requirement is installed.

Authentication: ComfyUI nodes cannot see the launcher's in-memory bootstrap
token, so the backend persists a separate read-only node token under the data
root (``userdata/settings/node_token.json``). This client reads that file by
default; an explicit node input or the ``WORKBENCH_SESSION_TOKEN`` environment
variable overrides it. The health probe is unauthenticated by design.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:17890"
APP_ID = "ComfyUI-Prompt-Workbench"
_TIMEOUT = 3.0


def _base_url(override: str = "") -> str:
    base = (override or os.environ.get("WORKBENCH_API_URL") or DEFAULT_BASE_URL)
    return base.strip("/")


def _default_token_file() -> Path | None:
    """Locate the persisted node token file, honoring data-root overrides."""
    env_file = os.environ.get("WORKBENCH_NODE_TOKEN_FILE", "").strip()
    if env_file:
        return Path(env_file)

    env_root = os.environ.get("WORKBENCH_DATA_ROOT", "").strip()
    if env_root:
        return Path(env_root) / "settings" / "node_token.json"

    # Package file: <repo>/comfyui_extension/workbench/backend_client.py,
    # so the repo root is two parents up (workbench -> comfyui_extension -> repo).
    pkg = Path(__file__).resolve().parent
    return pkg.parent.parent / "userdata" / "settings" / "node_token.json"


def _read_node_token_file() -> str:
    """Read the persisted read-only node token; empty on any failure.

    A missing or corrupt file must not crash the canvas -- the node simply
    reports that the backend session could not be read.
    """
    try:
        path = _default_token_file()
        if path and path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            token = data.get("token", "")
            if isinstance(token, str):
                return token.strip()
    except Exception:
        return ""
    return ""


def _token(override: str = "") -> str:
    if override.strip():
        return override.strip()
    env = os.environ.get("WORKBENCH_SESSION_TOKEN", "").strip()
    if env:
        return env
    return _read_node_token_file()


def _decode_error(exc: urllib.error.HTTPError) -> dict[str, Any]:
    """Turn an HTTP error into a readable payload, keeping the backend detail."""
    raw = exc.read().decode("utf-8") if exc.fp else ""
    try:
        payload = json.loads(raw) if raw else {"error": exc.reason}
    except json.JSONDecodeError:
        payload = {"error": exc.reason}
    detail = payload.get("detail") or payload.get("error") or exc.reason
    return {"error": str(detail), "status": exc.code, "detail": payload}


def _get(
    path: str,
    base_url: str = "",
    token: str = "",
    params: dict[str, str] | None = None,
    auth: bool = True,
) -> dict[str, Any]:
    url = _base_url(base_url) + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v})

    headers = {"Accept": "application/json"}
    tok = _token(token)
    if auth and tok:
        headers["Authorization"] = f"Bearer {tok}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        return _decode_error(exc)
    except Exception as exc:  # network/parse failure must not crash the canvas
        return {"error": f"{type(exc).__name__}: {exc}"}


def _post(
    path: str,
    base_url: str = "",
    token: str = "",
    body: dict[str, Any] | None = None,
    auth: bool = True,
) -> dict[str, Any]:
    url = _base_url(base_url) + path
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    tok = _token(token)
    if auth and tok:
        headers["Authorization"] = f"Bearer {tok}"

    payload = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return _decode_error(exc)
    except Exception as exc:  # network/parse failure must not crash the canvas
        return {"error": f"{type(exc).__name__}: {exc}"}


def release_vram(base_url: str = "", token: str = "") -> dict[str, Any]:
    """Ask the backend to unload resident Ollama models and free ComfyUI VRAM."""
    return _post("/api/system/release-vram", base_url, token)


def health(base_url: str = "", token: str = "") -> dict[str, Any]:
    """Unauthenticated identity probe; exposes app id/version only."""
    return _get("/api/health", base_url, token, auth=False)


def list_rules(
    level: str = "",
    scope_key: str = "",
    base_url: str = "",
    token: str = "",
) -> dict[str, Any]:
    """Read stored rules, optionally filtered by level and scope."""
    return _get(
        "/api/rules",
        base_url,
        token,
        params={"level": level, "scope_key": scope_key},
    )


def list_workflows(
    base_url: str = "",
    token: str = "",
) -> dict[str, Any]:
    """Read imported workbench workflows (id / filename / confirmed).

    This is a read-only GET; the node token is allowed through for safe methods.
    The list lets a node surface which backend workflow a context/candidate call
    should target without copying an id by hand.
    """
    return _get("/api/workflows", base_url, token)


def workflow_rule_prompt(
    workflow_id: str,
    base_url: str = "",
    token: str = "",
) -> dict[str, Any]:
    """Read the LLM-ready resolved rule context for one workflow.

    The projection carries only resolved fields, conflicts, unknowns and a
    short instruction; prompt bodies and notes are not included.
    """
    return _get(f"/api/workflows/{workflow_id}/rules/prompt", base_url, token)


def prompt_candidates(
    workflow_id: str,
    body: dict[str, Any],
    base_url: str = "",
    token: str = "",
) -> dict[str, Any]:
    """Ask the backend to produce a local-LLM prompt candidate.

    When ``workflow_id`` is empty the request goes to the standalone endpoint,
    so the same node can run a bare-model / writing round without a bound
    workflow. The suggestion is returned but never written back to a draft.
    """
    wid = (workflow_id or "").strip()
    path = (
        "/api/workflows/prompt/candidates"
        if not wid
        else f"/api/workflows/{wid}/prompt/candidates"
    )
    return _post(path, base_url, token, body)
