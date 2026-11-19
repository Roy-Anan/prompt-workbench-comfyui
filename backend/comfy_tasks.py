"""Persistent local record of submitted ComfyUI tasks.

The workbench records what it submitted and the last known status; ComfyUI
remains the source of truth for a running task. Interrupted or failed tasks
are never resubmitted automatically from this layer.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

# A task still active (or missing) this long after submission, with no
# terminal history entry, is treated as stalled rather than left hanging on
# ``unknown``. Kept conservative so a slow low-VRAM sampling run is not
# killed prematurely.
STALL_TIMEOUT = timedelta(minutes=15)

VALID_STATUSES = (
    "submitted",
    "running",
    "succeeded",
    "failed",
    "interrupted",
    "unknown",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS comfy_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id TEXT NOT NULL UNIQUE,
    workflow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    node_errors TEXT,
    last_error TEXT,
    outputs TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_history_status(
    status_str: str | None,
    completed: Any,
) -> str:
    """Map a ComfyUI history entry to a conservative local status.

    Anything not explicitly reported as success/error/interrupted stays
    ``unknown`` rather than being guessed as finished or failed.
    """
    if status_str == "success" or completed is True:
        return "succeeded"
    if status_str == "error":
        return "failed"
    if status_str in ("interrupted", "stopped", "cancelled"):
        return "interrupted"
    return "unknown"


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA)
    connection.commit()


def _decode(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _row(row: sqlite3.Row | tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = [
        "id",
        "prompt_id",
        "workflow_id",
        "status",
        "submitted_at",
        "updated_at",
        "node_errors",
        "last_error",
        "outputs",
    ]
    record = dict(zip(keys, row))
    record["node_errors"] = _decode(record.get("node_errors"))
    record["outputs"] = _decode(record.get("outputs"))
    return record


def record_submission(
    connection: sqlite3.Connection,
    prompt_id: str,
    workflow_id: str,
    node_errors: Any,
) -> dict[str, Any]:
    now = _now()
    connection.execute(
        """
        INSERT INTO comfy_task
            (prompt_id, workflow_id, status, submitted_at, updated_at,
             node_errors, last_error, outputs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prompt_id,
            workflow_id,
            "submitted",
            now,
            now,
            json.dumps(node_errors, ensure_ascii=False),
            None,
            None,
        ),
    )
    connection.commit()
    return get_by_prompt_id(connection, prompt_id)


def get_by_prompt_id(
    connection: sqlite3.Connection,
    prompt_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM comfy_task WHERE prompt_id = ?",
        (prompt_id,),
    ).fetchone()
    return _row(row)


def list_for_workflow(
    connection: sqlite3.Connection,
    workflow_id: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM comfy_task WHERE workflow_id = ? "
        "ORDER BY submitted_at DESC",
        (workflow_id,),
    ).fetchall()
    return [record for record in (_row(row) for row in rows) if record]


def update_status(
    connection: sqlite3.Connection,
    prompt_id: str,
    status: str,
    last_error: str | None = None,
    outputs: Any = None,
) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    connection.execute(
        """
        UPDATE comfy_task
        SET status = ?, updated_at = ?, last_error = ?, outputs = ?
        WHERE prompt_id = ?
        """,
        (
            status,
            _now(),
            last_error,
            None if outputs is None else json.dumps(outputs, ensure_ascii=False),
            prompt_id,
        ),
    )
    connection.commit()


def _parse_submitted_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_stalled(record: dict[str, Any], now: datetime | None = None) -> bool:
    """True when an in-flight record has exceeded the stall timeout.

    Malformed or missing timestamps do not count as stalled; reconciliation
    keeps the conservative previous status instead.
    """
    submitted = _parse_submitted_at(record.get("submitted_at"))
    if submitted is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current - submitted > STALL_TIMEOUT
