"""Local Ollama chat client for prompt candidates (M4).

This module only talks to the configured local Ollama instance. There is
**no cloud fallback**: an unreachable host, timeout, non-JSON response or
empty message is reported as a conservative error with no fabricated
candidate text. The candidate returned here is never written back to a
workflow by this layer.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

APP_NAME = "ComfyUI-Prompt-Workbench"
DEFAULT_TIMEOUT = 30.0
MAX_CANDIDATE_CHARS = 200_000
# A prompt candidate is one short prompt block; a local 8B model can otherwise
# drift into multi-thousand-token explanations. Cap generated tokens as a
# safety net unless the caller explicitly passes its own ``num_predict``.
MAX_PREDICT_TOKENS = 640
# Ollama otherwise starts the model with a 4096-token context window; the
# assembled candidate prompt (existing positive/negative prompts + rule
# layers) can exceed that and comes back as a 400. 8192 leaves headroom for
# an 8B-class local model on this 8GB card; the UI/API can still override it.
DEFAULT_NUM_CTX = 8192


def chat(
    ollama_url: str,
    model: str,
    messages: list[dict[str, Any]],
    timeout: float = DEFAULT_TIMEOUT,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send one non-streaming chat round to local Ollama ``/api/chat``.

    Returns ``{"ok": True, "content": str, "model": str, "error": None}`` on
    a usable response, otherwise ``{"ok": False, "content": None, ...}`` with
    an error string. Callers must treat ``ok is False`` as a failed request
    and must not guess a candidate. ``options`` carries whitelisted generation
    parameters (temperature / top_p / num_ctx); a ``num_predict`` ceiling is
    injected by default unless the caller supplies its own.
    """
    target = ollama_url.rstrip("/") + "/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        # Release the model from VRAM as soon as this round finishes instead
        # of leaving it resident for the server default keep-alive window.
        "keep_alive": 0,
        # Candidates are short tag/phrase output: disable the built-in
        # reasoning channel on thinking models (e.g. Qwen3) so the prediction
        # budget is not consumed by a hidden chain-of-thought that leaves the
        # visible answer empty. Models/servers that do not support this flag
        # ignore it.
        "think": False,
    }
    opts = dict(options or {})
    opts.setdefault("num_predict", MAX_PREDICT_TOKENS)
    opts.setdefault("num_ctx", DEFAULT_NUM_CTX)
    payload["options"] = opts
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = UrlRequest(target, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", APP_NAME)

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            return {
                "ok": False,
                "content": None,
                "model": model,
                "error": "empty model response",
            }
        if len(content.strip()) > MAX_CANDIDATE_CHARS:
            return {
                "ok": False,
                "content": None,
                "model": model,
                "error": "candidate exceeds maximum allowed length",
            }
        return {
            "ok": True,
            "content": content.strip(),
            "model": model,
            "error": None,
        }
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except OSError:
            body = ""
        detail = f"HTTP {exc.code}: {exc.reason}"
        if body:
            detail += f" | {body[:300]}"
        return {
            "ok": False,
            "content": None,
            "model": model,
            "error": detail,
        }
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "content": None,
            "model": model,
            "error": str(exc),
        }
