"""Background dispatcher for the local pending queue.

A single daemon thread drains the local queue one item at a time. Before each
submit it re-checks the same fail-closed conditions as manual submission:
ComfyUI reachable, no in-flight native task, and the LLM-unloaded gate. Items
that cannot submit yet are left pending and retried next tick; gate/remote
rejections are recorded as ``failed`` and are never retried automatically.

This layer only hands off already-confirmed workflows; it does not convert UI
workflows, write prompts, or touch the ComfyUI directory.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import comfy_client
import datadir
import model_identity
import runtime_log
import task_queue

TICK_SECONDS = 3.0

_stop_event = threading.Event()
_paused = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def set_global_paused(paused: bool) -> None:
    if paused:
        _paused.set()
    else:
        _paused.clear()


def is_global_paused() -> bool:
    return _paused.is_set()


def _database_path():
    return datadir.database_path(datadir.default_data_root())


def _has_inflight_task(connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM comfy_task WHERE status IN ('submitted', 'running') LIMIT 1"
    ).fetchone()
    return row is not None


def _detail_text(exc: Any) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return str(detail.get("error") or detail)
    if detail is None:
        return type(exc).__name__
    return str(detail)


def _dispatch_one() -> None:
    # Lazy import keeps module load side-effect free and avoids an import cycle
    # with the API router that re-exports this dispatcher.
    import workflow_api

    with model_identity.connect(_database_path()) as connection:
        task_queue.ensure_schema(connection)
        item = task_queue.next_ready(connection)
        if item is None:
            return

        if _has_inflight_task(connection):
            return

        comfyui_url = workflow_api._comfyui_url()
        active = comfy_client.queue_has_active_task(comfyui_url)
        if not active["online"] or active["active"]:
            # Offline or busy: hold the item, do not fail it.
            return

        task_queue.mark_submitting(connection, item["queue_id"])

    try:
        record = workflow_api._submit_guarded(
            item["workflow_id"], snapshot=item.get("prompt_snapshot")
        )
    except Exception as exc:  # HTTPException or unexpected failure
        with model_identity.connect(_database_path()) as connection:
            task_queue.mark_failed(connection, item["queue_id"], _detail_text(exc))
        return

    prompt_id = record.get("prompt_id") if isinstance(record, dict) else None
    with model_identity.connect(_database_path()) as connection:
        if prompt_id:
            task_queue.mark_submitted(connection, item["queue_id"], prompt_id)
        else:
            task_queue.mark_failed(
                connection, item["queue_id"], "提交成功但未返回 prompt_id"
            )


def _run() -> None:
    while not _stop_event.is_set():
        if not _paused.is_set():
            try:
                _dispatch_one()
            except Exception:
                # Never let the thread die on a transient error, but leave a
                # trace: a silently swallowed failure looks exactly like an
                # empty queue, and a stuck item is otherwise undiagnosable.
                runtime_log.logger().exception("队列调度异常，下一轮重试")
        _stop_event.wait(TICK_SECONDS)


def start() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop_event.clear()
        _thread = threading.Thread(
            target=_run, name="workbench-queue-dispatcher", daemon=True
        )
        _thread.start()


def stop() -> None:
    global _thread
    _stop_event.set()
    with _lock:
        thread = _thread
        _thread = None
    if thread is not None:
        thread.join(timeout=2.0)
