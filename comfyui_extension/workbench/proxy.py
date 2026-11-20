"""Local read-only proxy bridging the ComfyUI sidebar to the workbench backend.

The sidebar script runs in the ComfyUI browser origin (``127.0.0.1:8188``) and
cannot hold the launcher's in-memory session ticket or read the persisted node
token from disk. This module registers loopback routes on the ComfyUI server:
the Python side attaches the read-only node token before forwarding a narrow
whitelist of ``GET`` projections to the FastAPI backend. The browser only ever
talks to its own ComfyUI origin; the token never reaches the browser.

Only the routes in :data:`PROXY_ROUTES` are reachable through the proxy. Every
other suffix is rejected. State-changing verbs are rejected even for whitelisted
routes.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

import backend_client

# Proxy suffix -> backend read-only path. Keep this list minimal and limited to
# non-sensitive projections; draft/history Prompt bodies are never proxied.
PROXY_ROUTES: dict[str, str] = {
    "health": "/api/health",
    "overview": "/api/overview",
    "sidebar-summary": "/api/sidebar/summary",
    "workflows": "/api/workflows",
    "ollama-models": "/api/ollama/models",
    "profiles": "/api/prompt-profiles",
    "personas": "/api/personas",
    "genres": "/api/genres",
    "styles": "/api/styles",
}

SAFE_METHODS = {"GET", "HEAD"}


def backend_path_for(suffix: str) -> str | None:
    """Return the whitelisted backend path for a proxy suffix, or ``None``."""
    return PROXY_ROUTES.get(suffix)


def forward(backend_path: str, base_url: str = "") -> tuple[int, dict[str, Any]]:
    """Forward one whitelisted GET to the backend with the node token attached.

    Returns ``(status, payload)``. Network/parse failures degrade to a ``502``
    JSON body instead of raising, so a down backend never crashes the canvas.
    """
    token = backend_client._token()
    url = backend_client._base_url(base_url) + backend_path
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=backend_client._TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        try:
            payload = json.loads(raw) if raw else {"error": exc.reason}
        except json.JSONDecodeError:
            payload = {"error": exc.reason}
        return exc.code, payload
    except Exception as exc:  # network/parse failure must stay a JSON response
        return 502, {"error": f"{type(exc).__name__}: {exc}"}


def register_routes() -> bool:
    """Register the proxy routes on the ComfyUI server.

    Returns ``True`` when registered, ``False`` when the ComfyUI server module
    is unavailable (e.g. under plain pytest collection). Failure to register
    must not break the rest of the extension.
    """
    try:
        from server import PromptServer
        from aiohttp import web
    except ImportError:
        return False

    routes = PromptServer.instance.routes

    async def _proxy(request):
        suffix = request.match_info.get("suffix", "")
        if request.method not in SAFE_METHODS:
            return web.json_response(
                {"error": "method not allowed"}, status=405
            )
        backend_path = backend_path_for(suffix)
        if backend_path is None:
            return web.json_response({"error": "not allowed"}, status=403)
        status, payload = await asyncio.to_thread(forward, backend_path)
        return web.json_response(payload, status=status)

    # RouteTableDef exposes decorator-style registration (the same pattern the
    # bundled custom nodes use). A GET route implicitly allows HEAD, so a
    # separate HEAD registration would conflict at dispatch time.
    routes.get("/workbench-api/{suffix:.*}")(_proxy)
    return True
