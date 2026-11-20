"""Minimal loopback HTTP client for the workbench backend.

Runs inside the ComfyUI integrated Python and talks to the workbench FastAPI
backend over 127.0.0.1. Only the standard library is used so the extension
loads before any third-party requirement is installed.

Authentication: the backend accepts a local Bearer bootstrap token
(``WORKBENCH_SESSION_TOKEN``). The token can be passed per node or supplied
through the environment; the health probe is unauthenticated by design.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:17890"
APP_ID = "ComfyUI-Prompt-Workbench"
_TIMEOUT = 3.0


def _base_url(override: str = "") -> str:
    base = (override or os.environ.get("WORKBENCH_API_URL") or DEFAULT_BASE_URL)
    return base.strip("/")


def _token(override: str = "") -> str:
    return (override or os.environ.get("WORKBENCH_SESSION_TOKEN", "")).strip()


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
    except Exception as exc:  # network/parse failure must not crash the canvas
        return {"error": f"{type(exc).__name__}: {exc}"}


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
