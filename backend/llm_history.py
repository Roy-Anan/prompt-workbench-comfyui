"""History records for the two standalone LLM panels.

The ComfyUI out-prompt history stays in :mod:`generation_history`; this module
covers the other two workbench generators:

* **Vision (image-to-text)** — a local VL model describes an uploaded picture.
  Only pictures picked from the asset library are referenced in history;
  temporary local uploads are never persisted here. The record stores asset
  references only, never copied image bytes.
* **Writing** — a standalone writing-assistant candidate is saved to history
  only on an explicit user action; generated candidates are not auto-recorded.

Records are deleted permanently (no recycle bin). Deleting a history row
never touches drafts, asset originals or ComfyUI output files.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vision_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    model TEXT,
    prompt TEXT,
    description TEXT NOT NULL,
    asset_ids TEXT NOT NULL,
    image_names TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS writing_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    model TEXT,
    persona_id TEXT,
    persona_name TEXT,
    genre TEXT,
    style TEXT,
    mode TEXT,
    task TEXT,
    output_text TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---- Vision (image-to-text) ----


def record_vision(
    connection: sqlite3.Connection,
    *,
    model: str | None,
    prompt: str | None,
    description: str,
    asset_ids: list[int],
    image_names: list[str],
) -> dict[str, Any]:
    description = (description or "").strip()
    if not description:
        raise ValueError("识别结果为空，不能存入历史")
    clean_ids = [int(a) for a in asset_ids if a is not None]
    clean_names = [str(n or "") for n in image_names]
    now = _now()
    connection.execute(
        """
        INSERT INTO vision_history
            (created_at, model, prompt, description, asset_ids, image_names)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            (model or None),
            (prompt or None),
            description,
            json.dumps(clean_ids, ensure_ascii=False),
            json.dumps(clean_names, ensure_ascii=False),
        ),
    )
    connection.commit()
    record_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    return get_vision(connection, record_id)


def get_vision(connection: sqlite3.Connection, record_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM vision_history WHERE id = ?", (record_id,)
    ).fetchone()
    if row is None:
        return None
    asset_ids = _decode(row[5]) or []
    image_names = _decode(row[6]) or []
    assets = [
        {"asset_id": int(asset_id), "name": str(name)}
        for asset_id, name in zip(asset_ids, image_names)
    ]
    return {
        "kind": "vision",
        "id": str(row[0]),
        "created_at": row[1],
        "model": row[2],
        "prompt": row[3],
        "description": row[4],
        "assets": assets,
    }


def list_vision(
    connection: sqlite3.Connection, keyword: str | None = None
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if keyword and keyword.strip():
        term = f"%{_escape_like(keyword.strip().lower())}%"
        clauses.append(
            "(LOWER(COALESCE(prompt, '')) LIKE ? ESCAPE '\\' "
            "OR LOWER(description) LIKE ? ESCAPE '\\')"
        )
        params.extend([term, term])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT * FROM vision_history {where} ORDER BY created_at DESC",
        params,
    ).fetchall()
    records = [record for record in (get_vision(connection, row[0]) for row in rows) if record]
    return records


def delete_vision(connection: sqlite3.Connection, record_id: int) -> bool:
    cursor = connection.execute(
        "DELETE FROM vision_history WHERE id = ?", (record_id,)
    )
    connection.commit()
    return cursor.rowcount > 0


# ---- Writing ----


def record_writing(
    connection: sqlite3.Connection,
    *,
    model: str | None,
    persona_id: str | None,
    persona_name: str | None,
    genre: str | None,
    style: str | None,
    mode: str | None,
    task: str | None,
    output_text: str,
) -> dict[str, Any]:
    output_text = (output_text or "").strip()
    if not output_text:
        raise ValueError("候选内容为空，不能存入历史")
    now = _now()
    connection.execute(
        """
        INSERT INTO writing_history
            (created_at, model, persona_id, persona_name, genre, style,
             mode, task, output_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            (model or None),
            (persona_id or None),
            (persona_name or None),
            (genre or None),
            (style or None),
            (mode or None),
            (task or None),
            output_text,
        ),
    )
    connection.commit()
    record_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    return get_writing(connection, record_id)


def get_writing(
    connection: sqlite3.Connection, record_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM writing_history WHERE id = ?", (record_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "kind": "writing",
        "id": str(row[0]),
        "created_at": row[1],
        "model": row[2],
        "persona_id": row[3],
        "persona_name": row[4],
        "genre": row[5],
        "style": row[6],
        "mode": row[7],
        "task": row[8],
        "output_text": row[9],
    }


def list_writing(
    connection: sqlite3.Connection, keyword: str | None = None
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if keyword and keyword.strip():
        term = f"%{_escape_like(keyword.strip().lower())}%"
        clauses.append(
            "(LOWER(COALESCE(persona_name, '')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(genre, '')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(style, '')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(task, '')) LIKE ? ESCAPE '\\' "
            "OR LOWER(output_text) LIKE ? ESCAPE '\\')"
        )
        params.extend([term, term, term, term, term])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT * FROM writing_history {where} ORDER BY created_at DESC",
        params,
    ).fetchall()
    return [
        record
        for record in (get_writing(connection, row[0]) for row in rows)
        if record
    ]


def delete_writing(connection: sqlite3.Connection, record_id: int) -> bool:
    cursor = connection.execute(
        "DELETE FROM writing_history WHERE id = ?", (record_id,)
    )
    connection.commit()
    return cursor.rowcount > 0
