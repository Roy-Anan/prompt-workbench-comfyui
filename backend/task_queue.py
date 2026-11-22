"""Local pending submission queue (fail-closed, conservative).

ComfyUI's native queue has no pause/resume or reorder for 0.30.2, so the
workbench keeps its own in-process-persisted pending layer. Items here are only
*intent to submit*: nothing leaves the local machine until the background
dispatcher passes the same fail-closed gate used by manual submission.

Statuses:
- ``pending``   waiting to be dispatched
- ``paused``    held locally, not dispatched (per-item pause)
- ``submitting`` dispatcher is currently submitting to ComfyUI
- ``submitted``  already handed off; bridged to ``comfy_task`` by prompt_id
- ``cancelled`` removed from the pending list before submission
- ``failed``     gate/remote rejection; recorded, never auto-retried

The queue never resubmits on its own after a failure; retries stay manual.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

VALID_STATUSES = (
    "pending",
    "paused",
    "submitting",
    "submitted",
    "cancelled",
    "failed",
)

# Terminal / settled states that must not be reordered or cancelled locally.
_SETTLED = ("submitting", "submitted")
# States still visible in the pending queue listing.
_VISIBLE = ("pending", "paused", "submitting", "submitted", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workbench_queue (
    queue_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workflow_name TEXT NOT NULL,
    note TEXT,
    sort_order INTEGER NOT NULL,
    status TEXT NOT NULL,
    prompt_id TEXT,
    last_error TEXT,
    prompt_snapshot TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_column(connection: sqlite3.Connection, column: str, decl: str) -> None:
    cols = {
        row[1]
        for row in connection.execute("PRAGMA table_info(workbench_queue)").fetchall()
    }
    if column not in cols:
        connection.execute(
            f"ALTER TABLE workbench_queue ADD COLUMN {column} {decl}"
        )


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA)
    # Idempotent backfill for queues created before the snapshot column existed.
    _ensure_column(connection, "prompt_snapshot", "TEXT")
    connection.commit()


def _row(row: sqlite3.Row | tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = [
        "queue_id",
        "workflow_id",
        "workflow_name",
        "note",
        "sort_order",
        "status",
        "prompt_id",
        "last_error",
        "prompt_snapshot",
        "created_at",
        "updated_at",
    ]
    record = dict(zip(keys, row))
    record["note"] = record.get("note") or ""
    raw_snapshot = record.get("prompt_snapshot")
    if isinstance(raw_snapshot, str) and raw_snapshot.strip():
        try:
            parsed = json.loads(raw_snapshot)
            record["prompt_snapshot"] = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            record["prompt_snapshot"] = None
    else:
        record["prompt_snapshot"] = None
    return record


def list_items(
    connection: sqlite3.Connection,
    workflow_id: str | None = None,
) -> list[dict[str, Any]]:
    if workflow_id:
        rows = connection.execute(
            "SELECT * FROM workbench_queue WHERE workflow_id = ? "
            "AND status != 'cancelled' "
            "ORDER BY sort_order ASC, created_at ASC",
            (workflow_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM workbench_queue WHERE status != 'cancelled' "
            "ORDER BY sort_order ASC, created_at ASC"
        ).fetchall()
    return [record for record in (_row(row) for row in rows) if record]


def get_item(
    connection: sqlite3.Connection, queue_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM workbench_queue WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()
    return _row(row)


def enqueue(
    connection: sqlite3.Connection,
    workflow_id: str,
    workflow_name: str,
    note: str,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workflow_id = workflow_id.strip()
    workflow_name = workflow_name.strip() or workflow_id
    note = (note or "").strip()[:1000]
    if not workflow_id:
        raise ValueError("workflow_id 不能为空")

    snapshot_text = (
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        if isinstance(snapshot, dict)
        else None
    )

    now = _now()
    queue_id = str(uuid.uuid4())
    max_order = connection.execute(
        "SELECT COALESCE(MAX(sort_order), 0) FROM workbench_queue "
        "WHERE status != 'cancelled'"
    ).fetchone()[0]

    connection.execute(
        """
        INSERT INTO workbench_queue
            (queue_id, workflow_id, workflow_name, note, sort_order,
             status, prompt_id, last_error, prompt_snapshot, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, ?)
        """,
        (
            queue_id,
            workflow_id,
            workflow_name[:120],
            note,
            int(max_order) + 1,
            snapshot_text,
            now,
            now,
        ),
    )
    connection.commit()
    return get_item(connection, queue_id)  # type: ignore[return-value]


def cancel_item(
    connection: sqlite3.Connection, queue_id: str
) -> dict[str, Any] | None:
    """Remove a still-pending item from the local queue.

    Settled items (already submitting/submitted) are left untouched; cancel
    for those goes through the native task cancel/interrupt path instead.
    """
    item = get_item(connection, queue_id)
    if item is None:
        return None
    if item["status"] in _SETTLED:
        raise ValueError("该任务已提交，不能从本地队列取消；请用任务取消/中断。")
    now = _now()
    connection.execute(
        "UPDATE workbench_queue SET status = 'cancelled', updated_at = ? "
        "WHERE queue_id = ?",
        (now, queue_id),
    )
    connection.commit()
    return get_item(connection, queue_id)


def set_paused(
    connection: sqlite3.Connection, queue_id: str, paused: bool
) -> dict[str, Any] | None:
    item = get_item(connection, queue_id)
    if item is None:
        return None
    if item["status"] in _SETTLED:
        raise ValueError("该任务已提交，不能暂停或恢复。")
    target = "paused" if paused else "pending"
    now = _now()
    connection.execute(
        "UPDATE workbench_queue SET status = ?, updated_at = ? WHERE queue_id = ?",
        (target, now, queue_id),
    )
    connection.commit()
    return get_item(connection, queue_id)


def move_item(
    connection: sqlite3.Connection, queue_id: str, direction: str
) -> list[dict[str, Any]]:
    """Move an item up or down among the visible pending queue.

    Only pending/paused items may be reordered; settled items anchor their
    position. The swap is done against the adjacent movable neighbor.
    """
    if direction not in ("up", "down"):
        raise ValueError("direction 必须是 up 或 down")
    items = list_items(connection)
    ids = [item["queue_id"] for item in items]
    if queue_id not in ids:
        raise ValueError("排队项不存在")
    index = ids.index(queue_id)
    item = items[index]
    if item["status"] in _SETTLED:
        raise ValueError("已提交的任务不能调整顺序。")

    neighbor_index = index - 1 if direction == "up" else index + 1
    if neighbor_index < 0 or neighbor_index >= len(items):
        return items

    neighbor = items[neighbor_index]
    if neighbor["status"] in _SETTLED:
        return items

    now = _now()
    connection.execute(
        "UPDATE workbench_queue SET sort_order = ?, updated_at = ? WHERE queue_id = ?",
        (neighbor["sort_order"], now, queue_id),
    )
    connection.execute(
        "UPDATE workbench_queue SET sort_order = ?, updated_at = ? WHERE queue_id = ?",
        (item["sort_order"], now, neighbor["queue_id"]),
    )
    connection.commit()
    return list_items(connection)


def mark_submitting(connection: sqlite3.Connection, queue_id: str) -> None:
    connection.execute(
        "UPDATE workbench_queue SET status = 'submitting', updated_at = ? "
        "WHERE queue_id = ?",
        (_now(), queue_id),
    )
    connection.commit()


def mark_submitted(
    connection: sqlite3.Connection, queue_id: str, prompt_id: str
) -> None:
    now = _now()
    connection.execute(
        "UPDATE workbench_queue SET status = 'submitted', prompt_id = ?, "
        "last_error = NULL, updated_at = ? WHERE queue_id = ?",
        (prompt_id, now, queue_id),
    )
    connection.commit()


def mark_failed(
    connection: sqlite3.Connection, queue_id: str, error: str
) -> None:
    now = _now()
    connection.execute(
        "UPDATE workbench_queue SET status = 'failed', last_error = ?, "
        "updated_at = ? WHERE queue_id = ?",
        (error[:1000], now, queue_id),
    )
    connection.commit()


def requeue_stale_submitting(connection: sqlite3.Connection) -> int:
    """Return orphaned ``submitting`` rows to ``pending`` on startup.

    ``submitting`` is written just before the submit call. If the process dies
    in between (restart, Ctrl+C, crash) or the follow-up write fails, the row is
    "settled" for good: never retried, never cancellable, and it blocks the
    whole queue. Only call this at startup, before the dispatcher runs, when
    nothing can be genuinely in flight.
    """
    cursor = connection.execute(
        "UPDATE workbench_queue SET status = 'pending', updated_at = ? "
        "WHERE status = 'submitting'",
        (_now(),),
    )
    connection.commit()
    return cursor.rowcount or 0


def next_ready(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """First item eligible for dispatch: ``pending`` (not paused), in order."""
    row = connection.execute(
        "SELECT * FROM workbench_queue WHERE status = 'pending' "
        "ORDER BY sort_order ASC, created_at ASC LIMIT 1"
    ).fetchone()
    return _row(row)
