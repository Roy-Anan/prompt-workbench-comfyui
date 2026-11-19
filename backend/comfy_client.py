"""ComfyUI / Ollama HTTP client for task submission and pre-submit gates.

Only read-only probes and one guarded submit live here. There is no cloud
fallback: every unreachable, timed-out, or malformed response fails closed
with a conservative report instead of a fabricated result.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

APP_MARKER = "ComfyUI-Prompt-Workbench"

# R118: minimum free VRAM required before a guarded submit, 5 GiB.
# A probe that cannot read a free figure fails closed (unknown -> reject).
VRAM_FREE_MIN_BYTES = 5 * 1024 ** 3
# Larger latent (SDXL-class, ~704x1024 and up) peaks higher on an 8 GB card,
# so keep a wider headroom before submit.
VRAM_FREE_LARGE_BYTES = 6 * 1024 ** 3
_LARGE_LATENT_PIXELS = 704 * 1024


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[int, dict[str, Any] | None, str | None]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", APP_MARKER)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
        data = json.loads(raw) if raw.strip() else {}
        return status, (data if isinstance(data, dict) else None), None
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        return 0, None, str(exc)


def _connection_refused(exc: Exception) -> bool:
    """True when the local service process is not listening (connection refused).

    urllib wraps a Windows ``WinError 10061`` / Linux ``ECONNREFUSED`` inside a
    ``URLError`` whose ``reason`` is a ``ConnectionRefusedError``; other OSErrors
    and timeouts are treated as a reachable-but-ambiguous state so the caller
    still fails closed instead of assuming no model is resident.
    """
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ConnectionRefusedError) or isinstance(
        exc, ConnectionRefusedError
    )


def _get_json(
    url: str,
    timeout: float,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    request = urllib.request.Request(url)
    request.add_header("User-Agent", APP_MARKER)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return (data if isinstance(data, dict) else None), None, True
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        reachable = not _connection_refused(exc)
        return None, str(exc), reachable


def _queue_ids(items: Any) -> list[str]:
    ids: list[str] = []
    if not isinstance(items, list):
        return ids
    for item in items:
        if not isinstance(item, list) or not item:
            continue
        # ComfyUI queue entry: ``[index, prompt_id, prompt, ...]``.
        # The prompt_id lives at index 1; index 0 is the numeric queue
        # position. Fall back to index 0 only if it already carries a string
        # identifier (older/alternate layouts).
        prompt_id: Any = None
        if len(item) > 1 and isinstance(item[1], str):
            prompt_id = item[1]
        elif isinstance(item[0], str):
            prompt_id = item[0]
        if isinstance(prompt_id, str) and prompt_id:
            ids.append(prompt_id)
    return ids


def fetch_task_states(
    comfyui_url: str,
    timeout: float = 2.5,
) -> dict[str, Any]:
    """Project queue and history details for local task status reconciliation.

    Only identifiers and a conservative status summary are read; nothing is
    submitted, cancelled, or reordered. A missing queue response fails closed
    with ``online=False`` and empty projections.
    """
    queue_data, queue_error, _queue_reachable = _get_json(
        comfyui_url.rstrip("/") + "/queue", timeout
    )
    history_data, history_error, _history_reachable = _get_json(
        comfyui_url.rstrip("/") + "/history", timeout
    )

    if queue_data is None:
        return {
            "online": False,
            "error": queue_error,
            "running": [],
            "pending": [],
            "history": {},
        }

    history: dict[str, Any] = {}
    if isinstance(history_data, dict):
        for prompt_id, entry in history_data.items():
            if not isinstance(prompt_id, str) or not isinstance(entry, dict):
                continue
            status = entry.get("status")
            status_str: str | None = None
            completed: Any = None
            if isinstance(status, dict):
                raw_str = status.get("status_str")
                status_str = raw_str if isinstance(raw_str, str) else None
                completed = status.get("completed")
            history[prompt_id] = {
                "status_str": status_str,
                "completed": completed,
                "outputs": entry.get("outputs"),
            }

    return {
        "online": True,
        "error": history_error if history_data is None else None,
        "running": _queue_ids(queue_data.get("queue_running")),
        "pending": _queue_ids(queue_data.get("queue_pending")),
        "history": history,
    }


def _latent_pixel_count(prompt_api: Any) -> int | None:
    """Sum width*height from latent-size nodes; None when not derivable."""
    if not isinstance(prompt_api, dict):
        return None
    total: int | None = None
    for node in prompt_api.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        if "Latent" not in class_type:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        width = inputs.get("width")
        height = inputs.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            total = (total or 0) + width * height
    return total


def min_vram_required(prompt_api: Any) -> int:
    """Conservative free-VRAM floor for a submit, scaled by latent size.

    SDXL-class large latents peak higher on an 8 GB card; workflows without a
    readable latent size fall back to the base floor.
    """
    pixels = _latent_pixel_count(prompt_api)
    if pixels is not None and pixels >= _LARGE_LATENT_PIXELS:
        return VRAM_FREE_LARGE_BYTES
    return VRAM_FREE_MIN_BYTES


def queue_has_active_task(
    comfyui_url: str,
    timeout: float = 2.5,
) -> dict[str, Any]:
    """Report whether ComfyUI is online with a running or pending task.

    When ComfyUI is unreachable it cannot hold an in-flight task, so the
    report is ``online=False, active=False`` rather than a blocking unknown.
    """
    states = fetch_task_states(comfyui_url, timeout)
    if not states["online"]:
        return {"online": False, "active": False, "error": states["error"]}
    return {
        "online": True,
        "active": bool(states["running"] or states["pending"]),
        "error": None,
    }


def comfyui_online(comfyui_url: str, timeout: float = 2.5) -> bool:
    """True only if /system_stats answers with a JSON body."""
    data, _, _ = _get_json(comfyui_url.rstrip("/") + "/system_stats", timeout)
    return data is not None


def probe_vram_free(
    comfyui_url: str,
    timeout: float = 2.5,
) -> dict[str, Any]:
    """Read-only free VRAM snapshot from ``/system_stats``.

    Returns ``online=False`` when the endpoint is unreachable; ``vram_free``
    is ``None`` when the response carries no usable free figure, so the caller
    fails closed instead of guessing. With multiple devices the conservative
    minimum free figure is reported.
    """
    data, error, reachable = _get_json(
        comfyui_url.rstrip("/") + "/system_stats", timeout
    )
    if data is None:
        return {
            "online": False,
            "reachable": reachable,
            "vram_free": None,
            "error": error,
        }
    devices = data.get("devices")
    free_values: list[int] = []
    if isinstance(devices, list):
        for item in devices:
            if not isinstance(item, dict):
                continue
            vram_free = item.get("vram_free")
            if isinstance(vram_free, int) and vram_free >= 0:
                free_values.append(vram_free)
    if not free_values:
        return {"online": True, "reachable": True, "vram_free": None, "error": "缺少显存空闲字段"}
    return {
        "online": True,
        "reachable": True,
        "vram_free": min(free_values),
        "error": None,
    }


def loaded_ollama_models(
    ollama_url: str,
    timeout: float = 2.5,
) -> dict[str, Any]:
    """Read-only list of models currently loaded in Ollama (``/api/ps``).

    ``reachable`` reports whether the HTTP service answered at the connection
    layer. A refused connection means the Ollama process is not running, so no
    local LLM can be resident; an online-but-malformed response stays
    ``reachable=True`` so the caller can still fail closed on ambiguity.
    """
    data, error, reachable = _get_json(
        ollama_url.rstrip("/") + "/api/ps", timeout
    )
    if data is None:
        return {
            "online": False,
            "reachable": reachable,
            "models": [],
            "error": error,
        }
    entries = data.get("models")
    names: list[str] = []
    if isinstance(entries, list):
        for item in entries:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
    return {"online": True, "reachable": True, "models": names, "error": None}


def unload_ollama_model(
    ollama_url: str,
    model: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Unload one resident Ollama model.

    Ollama has no dedicated stop HTTP route: the supported way to release a
    model from memory is a ``/api/generate`` round with an empty prompt and
    ``keep_alive: 0`` (unload immediately after the request).
    """
    _status, _data, error = _post_json(
        ollama_url.rstrip("/") + "/api/generate",
        {"model": model, "prompt": "", "keep_alive": 0, "stream": False},
        timeout,
    )
    return {"ok": error is None, "model": model, "error": error}


def free_comfyui_models(
    comfyui_url: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Unload all loaded ComfyUI models and free VRAM via ``/free``.

    This releases the checkpoint, LoRA, CLIP and VAE currently resident;
    ComfyUI reloads them on the next prompt.
    """
    _status, _data, error = _post_json(
        comfyui_url.rstrip("/") + "/free",
        {"unload_models": True, "free_memory": True},
        timeout,
    )
    return {"ok": error is None, "error": error}


def release_resources(
    ollama_url: str,
    comfyui_url: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Best-effort unload of resident Ollama and ComfyUI models.

    Never raises: per-target failures are collected and returned so the
    caller can fall back to the existing fail-closed VRAM / LLM-resident
    gates instead of failing the whole release on one target. After issuing
    the unload calls the resident list is re-probed so models that still
    show up are reported as ``still_resident`` rather than assumed freed.
    """
    loaded = loaded_ollama_models(ollama_url, timeout)
    ollama_stopped: list[str] = []
    ollama_errors: list[str] = []
    if loaded["reachable"]:
        for name in loaded["models"]:
            result = unload_ollama_model(ollama_url, name, timeout)
            if result["ok"]:
                ollama_stopped.append(name)
            else:
                ollama_errors.append(f"{name}: {result['error']}")

        after = loaded_ollama_models(ollama_url, timeout)
        still_resident = [name for name in after["models"]]
    else:
        still_resident = []

    comfy = free_comfyui_models(comfyui_url, timeout)
    return {
        "ollama_reachable": loaded["reachable"],
        "ollama_stopped": ollama_stopped,
        "ollama_errors": ollama_errors,
        "ollama_still_resident": still_resident,
        "comfyui_freed": comfy["ok"],
        "comfyui_error": comfy["error"],
    }


def cancel_pending(
    comfyui_url: str,
    prompt_id: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Remove a pending prompt from the ComfyUI queue (``/queue`` delete)."""
    _status, _data, error = _post_json(
        comfyui_url.rstrip("/") + "/queue",
        {"delete": [prompt_id]},
        timeout,
    )
    return {"ok": error is None, "error": error}


def interrupt_running(
    comfyui_url: str,
    prompt_id: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Interrupt the running prompt identified by ``prompt_id`` (``/interrupt``)."""
    _status, _data, error = _post_json(
        comfyui_url.rstrip("/") + "/interrupt",
        {"prompt_id": prompt_id},
        timeout,
    )
    return {"ok": error is None, "error": error}


def submit_prompt(
    comfyui_url: str,
    prompt_api: dict[str, Any],
    client_id: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Submit an API-format prompt to ComfyUI ``/prompt``.

    A response carrying non-empty ``node_errors`` is treated as a rejected
    submission. No task is retried automatically here.
    """
    target = comfyui_url.rstrip("/") + "/prompt"
    _status, data, error = _post_json(
        target,
        {"prompt": prompt_api, "client_id": client_id},
        timeout,
    )
    if error is not None or data is None:
        return {
            "ok": False,
            "prompt_id": None,
            "node_errors": None,
            "error": error or "提交响应无效",
        }

    prompt_id = data.get("prompt_id")
    node_errors = data.get("node_errors")
    if not isinstance(prompt_id, str) or not prompt_id:
        return {
            "ok": False,
            "prompt_id": None,
            "node_errors": node_errors,
            "error": "响应缺少 prompt_id",
        }
    if isinstance(node_errors, dict) and node_errors:
        return {
            "ok": False,
            "prompt_id": prompt_id,
            "node_errors": node_errors,
            "error": "ComfyUI 报告节点错误",
        }
    return {"ok": True, "prompt_id": prompt_id, "node_errors": {}, "error": None}
