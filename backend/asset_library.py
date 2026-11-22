"""Local image asset library for the workbench.

A standalone, read-mostly-by-default asset manager: users import local images
either as a managed copy (bytes copied into the workbench ``assets`` directory,
survives after the original moves/deletes) or as a path reference (only the
original absolute path is recorded; the original file is never copied, moved,
or deleted).

Scope kept narrow per the asset-library brief:
  * manual groups, one default ``未分组`` group, tags with AND filtering;
  * thumbnail grid metadata, rename / regroup / retag / delete;
  * copy deletion may remove the managed copy on request; reference deletion
    only removes the library record and never touches the original file;
  * image bytes are served through a protected backend proxy endpoint, not an
    open local-file reader.

No AI tagging, no auto classification, no prompt composition, no ComfyUI
coupling.
"""

from __future__ import annotations

import base64
import re
import secrets
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Image extensions accepted by the library, lower-case with leading dot.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

DEFAULT_GROUP_NAME = "未分组"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS asset_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_tag (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('copy', 'reference')),
    storage_rel TEXT,
    source_path TEXT,
    group_id INTEGER REFERENCES asset_group(id) ON DELETE SET NULL,
    size_bytes INTEGER,
    ext TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_item_group ON asset_item(group_id);
CREATE TABLE IF NOT EXISTS asset_item_tag (
    asset_id INTEGER NOT NULL REFERENCES asset_item(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES asset_tag(id) ON DELETE CASCADE,
    PRIMARY KEY(asset_id, tag_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.row_factory = sqlite3.Row
    return connection


def _normalize_factory(connection: sqlite3.Connection) -> None:
    """Every helper here reads rows by column name.

    Most callers hand over ``asset_library.connect()`` (factory already set), but
    other modules open plain connections for their own metadata tables. Rather
    than make every read tuple-safe, the public entry points normalize the
    caller's short-lived connection once.
    """
    if connection.row_factory is not sqlite3.Row:
        connection.row_factory = sqlite3.Row


def ensure_schema(connection: sqlite3.Connection) -> None:
    _normalize_factory(connection)
    connection.executescript(_SCHEMA)
    _ensure_default_group(connection)
    connection.commit()


def _ensure_default_group(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT id FROM asset_group WHERE is_default = 1"
    ).fetchone()
    if row is not None:
        return int(row[0])
    cursor = connection.execute(
        "INSERT INTO asset_group(name, is_default, created_at) VALUES (?, 1, ?)",
        (DEFAULT_GROUP_NAME, _now()),
    )
    return int(cursor.lastrowid)


def _default_group_id(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT id FROM asset_group WHERE is_default = 1"
    ).fetchone()
    if row is None:
        return _ensure_default_group(connection)
    return int(row[0])


def _normalize_name(raw: str) -> str:
    return (raw or "").strip()


def _normalize_tag_names(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: dict[str, str] = {}
    for entry in raw:
        name = str(entry or "").strip()
        if name:
            seen.setdefault(name.lower(), name)
    return list(seen.values())


def _resolve_tags(connection: sqlite3.Connection, names: list[str]) -> list[int]:
    tag_ids: list[int] = []
    for name in names:
        connection.execute(
            "INSERT OR IGNORE INTO asset_tag(name, created_at) VALUES (?, ?)",
            (name, _now()),
        )
        row = connection.execute(
            "SELECT id FROM asset_tag WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            tag_ids.append(int(row["id"]))
    return tag_ids


def _attach_tags(connection: sqlite3.Connection, asset_id: int, tag_ids: list[int]) -> None:
    connection.execute("DELETE FROM asset_item_tag WHERE asset_id = ?", (asset_id,))
    for tag_id in tag_ids:
        connection.execute(
            "INSERT OR IGNORE INTO asset_item_tag(asset_id, tag_id) VALUES (?, ?)",
            (asset_id, tag_id),
        )


def _tags_for(connection: sqlite3.Connection, asset_id: int) -> list[str]:
    rows = connection.execute(
        """
        SELECT t.name FROM asset_tag t
        JOIN asset_item_tag at ON at.tag_id = t.id
        WHERE at.asset_id = ?
        ORDER BY t.name COLLATE NOCASE
        """,
        (asset_id,),
    ).fetchall()
    return [row["name"] for row in rows]


def _ext_of(name: str) -> str:
    return Path(name).suffix.lower()


def _validate_image_suffix(suffix: str) -> None:
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(f"不支持的图片格式：{suffix or '未知'}（仅 PNG/JPG/JPEG/WebP）")


# ---- Groups ----

def list_groups(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT g.id, g.name, g.is_default,
               (SELECT COUNT(*) FROM asset_item i WHERE i.group_id = g.id) AS count
        FROM asset_group g
        ORDER BY g.is_default DESC, g.name COLLATE NOCASE
        """
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "is_default": bool(row["is_default"]),
            "count": int(row["count"]),
        }
        for row in rows
    ]


def create_group(connection: sqlite3.Connection, name: str) -> dict[str, Any]:
    name = _normalize_name(name)
    if not name:
        raise ValueError("分组名称不能为空")
    try:
        cursor = connection.execute(
            "INSERT INTO asset_group(name, is_default, created_at) VALUES (?, 0, ?)",
            (name, _now()),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("分组名称已存在") from exc
    connection.commit()
    return {"id": int(cursor.lastrowid), "name": name, "is_default": False, "count": 0}


def rename_group(
    connection: sqlite3.Connection,
    group_id: int,
    name: str,
    assets_dir: Path | None = None,
) -> None:
    name = _normalize_name(name)
    if not name:
        raise ValueError("分组名称不能为空")
    row = connection.execute("SELECT name, is_default FROM asset_group WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise LookupError("分组不存在")
    if bool(row["is_default"]):
        raise ValueError("默认分组不可重命名")
    old_name = row["name"]
    try:
        connection.execute(
            "UPDATE asset_group SET name = ? WHERE id = ?", (name, group_id)
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("分组名称已存在") from exc

    if assets_dir is not None and old_name != name:
        old_folder = f"{group_id}-{_safe_folder(old_name)}"
        new_folder = f"{group_id}-{_safe_folder(name)}"
        if old_folder != new_folder:
            # Rewrite stored relative paths before touching the directory.
            items = connection.execute(
                "SELECT id, storage_rel FROM asset_item WHERE group_id = ? AND kind = 'copy'",
                (group_id,),
            ).fetchall()
            for row in items:
                rel = row["storage_rel"] or ""
                if rel.startswith(f"{old_folder}/"):
                    new_rel = f"{new_folder}/{rel.split('/', 1)[1]}"
                    connection.execute(
                        "UPDATE asset_item SET storage_rel = ? WHERE id = ?",
                        (new_rel, row["id"]),
                    )
            old_dir = (assets_dir / "copies" / old_folder).resolve()
            new_dir = (assets_dir / "copies" / new_folder).resolve()
            try:
                old_dir.relative_to((assets_dir / "copies").resolve())
                new_dir.relative_to((assets_dir / "copies").resolve())
            except ValueError:
                old_dir = new_dir = None
            if old_dir is not None and old_dir.is_dir() and not new_dir.exists():
                new_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_dir), str(new_dir))
    connection.commit()


def delete_group(
    connection: sqlite3.Connection,
    group_id: int,
    assets_dir: Path | None = None,
) -> None:
    row = connection.execute("SELECT is_default FROM asset_group WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise LookupError("分组不存在")
    if bool(row["is_default"]):
        raise ValueError("默认分组不可删除")
    default_id = _default_group_id(connection)

    if assets_dir is not None:
        members = connection.execute(
            "SELECT id, kind, storage_rel, group_id FROM asset_item WHERE group_id = ?",
            (group_id,),
        ).fetchall()
        for member in members:
            if member["kind"] == "copy":
                record = {
                    "id": member["id"],
                    "kind": member["kind"],
                    "storage_rel": member["storage_rel"],
                    "group_id": member["group_id"],
                }
                _move_copy_to_group(connection, assets_dir, record, default_id)

    connection.execute(
        "UPDATE asset_item SET group_id = ?, updated_at = ? WHERE group_id = ?",
        (default_id, _now(), group_id),
    )
    connection.execute("DELETE FROM asset_group WHERE id = ?", (group_id,))
    connection.commit()


# ---- Tags ----

def list_tags(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT t.id, t.name,
               (SELECT COUNT(*) FROM asset_item_tag at WHERE at.tag_id = t.id) AS count
        FROM asset_tag t
        ORDER BY t.name COLLATE NOCASE
        """
    ).fetchall()
    return [
        {"id": int(row["id"]), "name": row["name"], "count": int(row["count"])}
        for row in rows
    ]


def delete_tag(connection: sqlite3.Connection, tag_id: int) -> None:
    connection.execute("DELETE FROM asset_item_tag WHERE tag_id = ?", (tag_id,))
    connection.execute("DELETE FROM asset_tag WHERE id = ?", (tag_id,))
    connection.commit()


# ---- Items ----

def _group_id_or_default(connection: sqlite3.Connection, group_id: int | None) -> int:
    if group_id is None:
        return _default_group_id(connection)
    row = connection.execute("SELECT id FROM asset_group WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise LookupError("分组不存在")
    return int(row["id"])


def _unique_copy_name(copies_dir: Path, ext: str) -> str:
    for _ in range(20):
        candidate = f"{secrets.token_hex(16)}{ext}"
        if not (copies_dir / candidate).exists():
            return candidate
    raise OSError("无法生成唯一的素材文件名")


def _safe_folder(name: str) -> str:
    """Windows-safe folder stem for a group display name."""
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip()).strip("_")
    return text or "group"


def _group_folder(connection: sqlite3.Connection, group_id: int | None) -> str:
    """Physical subfolder under ``copies/`` for one group.

    The default group keeps a stable ``_default`` folder; other groups get an
    id-prefixed slug so two similar names cannot collide on disk.
    """
    if group_id is None:
        return "_default"
    row = connection.execute(
        "SELECT name, is_default FROM asset_group WHERE id = ?", (group_id,)
    ).fetchone()
    if row is None or bool(row["is_default"]):
        return "_default"
    return f"{group_id}-{_safe_folder(row['name'])}"


def _copy_path(assets_dir: Path, storage_rel: str) -> Path | None:
    copies_dir = (assets_dir / "copies").resolve()
    target = (copies_dir / storage_rel).resolve()
    try:
        target.relative_to(copies_dir)
    except ValueError:
        return None
    return target


def _move_copy_to_group(
    connection: sqlite3.Connection,
    assets_dir: Path,
    record: dict[str, Any],
    new_group_id: int,
) -> None:
    """Move a managed copy into the physical folder of its new group."""
    new_folder = _group_folder(connection, new_group_id)
    storage_rel = record.get("storage_rel") or ""
    src = _copy_path(assets_dir, storage_rel) if storage_rel else None

    if src is not None and src.is_file():
        group_dir = assets_dir / "copies" / new_folder
        group_dir.mkdir(parents=True, exist_ok=True)
        dest_name = _unique_copy_name(group_dir, src.suffix.lower())
        dest = group_dir / dest_name
        shutil.move(str(src), str(dest))
        new_rel = f"{new_folder}/{dest_name}"
        # Best-effort cleanup of a now-empty old group folder.
        old_dir = src.parent
        try:
            old_dir.rmdir()
        except OSError:
            pass
    else:
        # Source already missing: keep the basename under the new folder so the
        # record still resolves as missing rather than escaping the root.
        basename = storage_rel.split("/")[-1]
        new_rel = f"{new_folder}/{basename}" if basename else ""

    connection.execute(
        "UPDATE asset_item SET storage_rel = ? WHERE id = ?",
        (new_rel, record["id"]),
    )


def migrate_copy_group_folders(
    connection: sqlite3.Connection,
    assets_dir: Path,
) -> int:
    """Relocate legacy flat managed copies into their group subfolder.

    Older records stored a bare file name under ``copies/``; new records carry
    ``<group-folder>/<token>``. This idempotent one-shot moves those legacy
    files and rewrites ``storage_rel``; records already under a subfolder are
    left untouched. A missing source file still gets a folder-prefixed rel so
    the record resolves as missing inside the copies root rather than escaping
    it.
    """
    _normalize_factory(connection)
    rows = connection.execute(
        "SELECT id, storage_rel, group_id FROM asset_item WHERE kind = 'copy'"
    ).fetchall()
    migrated = 0
    copies_dir = (assets_dir / "copies").resolve()
    for row in rows:
        storage_rel = row["storage_rel"] or ""
        if "/" in storage_rel or "\\" in storage_rel:
            continue
        group_folder = _group_folder(connection, row["group_id"])
        src = (copies_dir / storage_rel).resolve() if storage_rel else None
        inside_root = False
        if src is not None:
            try:
                src.relative_to(copies_dir)
                inside_root = True
            except ValueError:
                inside_root = False

        if inside_root and src.is_file():
            group_dir = assets_dir / "copies" / group_folder
            group_dir.mkdir(parents=True, exist_ok=True)
            dest_name = _unique_copy_name(group_dir, src.suffix.lower())
            dest = group_dir / dest_name
            shutil.move(str(src), str(dest))
            new_rel = f"{group_folder}/{dest_name}"
            try:
                src.parent.rmdir()
            except OSError:
                pass
        else:
            basename = storage_rel.replace("\\", "/").split("/")[-1]
            new_rel = f"{group_folder}/{basename}" if basename else ""

        connection.execute(
            "UPDATE asset_item SET storage_rel = ? WHERE id = ?",
            (new_rel, row["id"]),
        )
        migrated += 1
    connection.commit()
    return migrated


def import_copies(
    connection: sqlite3.Connection,
    assets_dir: Path,
    files: list[dict[str, Any]],
    group_id: int | None = None,
    tag_names: list[str] | None = None,
    note: str = "",
) -> list[dict[str, Any]]:
    """Import uploaded images as managed copies under ``assets_dir/copies``.

    ``files`` entries carry ``name`` and base64 ``data`` (no data-URL prefix).
    The original file outside the workbench is never touched.
    """
    target_group = _group_id_or_default(connection, group_id)
    tag_ids = _resolve_tags(connection, _normalize_tag_names(tag_names))
    copies_dir = assets_dir / "copies"
    group_folder = _group_folder(connection, target_group)
    group_dir = copies_dir / group_folder
    group_dir.mkdir(parents=True, exist_ok=True)

    created: list[dict[str, Any]] = []
    for entry in files:
        original_name = _normalize_name(str(entry.get("name") or ""))
        suffix = _ext_of(original_name)
        _validate_image_suffix(suffix)
        raw_data = str(entry.get("data") or "")
        try:
            payload = base64.b64decode(raw_data, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"图片数据解码失败：{original_name}") from exc
        if not payload:
            raise ValueError(f"图片内容为空：{original_name}")

        stored_name = _unique_copy_name(group_dir, suffix)
        (group_dir / stored_name).write_bytes(payload)
        storage_rel = f"{group_folder}/{stored_name}"

        now = _now()
        display_name = original_name or stored_name
        cursor = connection.execute(
            """
            INSERT INTO asset_item(name, kind, storage_rel, source_path, group_id,
                                   size_bytes, ext, note, created_at, updated_at)
            VALUES (?, 'copy', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                display_name,
                storage_rel,
                original_name,
                target_group,
                len(payload),
                suffix,
                (note or "").strip(),
                now,
                now,
            ),
        )
        asset_id = int(cursor.lastrowid)
        _attach_tags(connection, asset_id, tag_ids)
        created.append(_get_record(connection, asset_id, assets_dir))

    connection.commit()
    return created


def import_references(
    connection: sqlite3.Connection,
    paths: list[str],
    group_id: int | None = None,
    tag_names: list[str] | None = None,
    note: str = "",
) -> list[dict[str, Any]]:
    """Record existing local images by absolute path; no bytes are copied."""
    target_group = _group_id_or_default(connection, group_id)
    tag_ids = _resolve_tags(connection, _normalize_tag_names(tag_names))

    created: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in paths:
        raw_path = str(raw_path or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        if not path.is_file():
            raise ValueError(f"引用路径不存在或不是文件：{raw_path}")
        suffix = path.suffix.lower()
        _validate_image_suffix(suffix)
        resolved = str(path.resolve())
        if resolved.lower() in seen:
            continue
        seen.add(resolved.lower())

        now = _now()
        cursor = connection.execute(
            """
            INSERT INTO asset_item(name, kind, storage_rel, source_path, group_id,
                                   size_bytes, ext, note, created_at, updated_at)
            VALUES (?, 'reference', NULL, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                path.name,
                resolved,
                target_group,
                path.stat().st_size,
                suffix,
                (note or "").strip(),
                now,
                now,
            ),
        )
        asset_id = int(cursor.lastrowid)
        _attach_tags(connection, asset_id, tag_ids)
        created.append(_get_record(connection, asset_id, None))

    connection.commit()
    return created


def _record_exists(record: dict[str, Any], assets_dir: Path | None) -> bool:
    if record["kind"] == "copy":
        if not record["storage_rel"] or assets_dir is None:
            return False
        path = _copy_path(assets_dir, record["storage_rel"])
        return path is not None and path.is_file()
    source = record.get("source_path") or ""
    if not source:
        return False
    return Path(source).expanduser().is_file()


def _get_record(connection: sqlite3.Connection, asset_id: int, assets_dir: Path | None) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT i.*, g.name AS group_name
        FROM asset_item i
        LEFT JOIN asset_group g ON g.id = i.group_id
        WHERE i.id = ?
        """,
        (asset_id,),
    ).fetchone()
    if row is None:
        return None
    record = {
        "id": int(row["id"]),
        "name": row["name"],
        "kind": row["kind"],
        "storage_rel": row["storage_rel"],
        "source_path": row["source_path"],
        "group_id": int(row["group_id"]) if row["group_id"] is not None else None,
        "group_name": row["group_name"],
        "size_bytes": row["size_bytes"],
        "ext": row["ext"],
        "note": row["note"] or "",
        "tags": _tags_for(connection, asset_id),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    record["exists"] = _record_exists(record, assets_dir)
    return record


def list_items(
    connection: sqlite3.Connection,
    assets_dir: Path | None,
    group_id: int | None = None,
    tag_names: list[str] | None = None,
    keyword: str = "",
) -> list[dict[str, Any]]:
    sql = "SELECT DISTINCT i.id FROM asset_item i"
    params: list[Any] = []
    clauses: list[str] = []

    if group_id is not None:
        clauses.append("i.group_id = ?")
        params.append(group_id)

    tag_filters = _normalize_tag_names(tag_names)
    if tag_filters:
        tag_conditions: list[str] = []
        for index, tag_name in enumerate(tag_filters):
            af = f"af{index}"
            tg = f"tg{index}"
            sql += (
                f" JOIN asset_item_tag {af} ON {af}.asset_id = i.id"
                f" JOIN asset_tag {tg} ON {tg}.id = {af}.tag_id"
            )
            tag_conditions.append(f"{tg}.name COLLATE NOCASE = ?")
            params.append(tag_name)
        clauses.append("(" + " AND ".join(tag_conditions) + ")")

    keyword_term = _normalize_name(keyword).lower()
    if keyword_term:
        clauses.append("LOWER(i.name) LIKE ?")
        params.append(f"%{keyword_term}%")

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY i.created_at DESC, i.id DESC"

    rows = connection.execute(sql, params).fetchall()
    # ``row[0]`` works on plain tuples and on sqlite3.Row, so this stays correct
    # whichever factory the caller's connection uses.
    ids = [int(row[0]) for row in rows]
    if not ids:
        return []

    # One batched read instead of one SELECT per asset: every caller (素材库页、
    # 总览、侧栏摘要) paid N queries plus N tag queries before, which got slow
    # with a few hundred assets. Tag names are packed with the unit separator so
    # the whole record still comes from a single statement.
    placeholders = ",".join("?" for _ in ids)
    cursor = connection.execute(
        f"""
        SELECT i.*, g.name AS group_name,
               (SELECT GROUP_CONCAT(name, char(31)) FROM (
                   SELECT t.name FROM asset_tag t
                   JOIN asset_item_tag at ON at.tag_id = t.id
                   WHERE at.asset_id = i.id
                   ORDER BY t.name COLLATE NOCASE
               )) AS tag_blob
        FROM asset_item i
        LEFT JOIN asset_group g ON g.id = i.group_id
        WHERE i.id IN ({placeholders})
        """,
        ids,
    )
    # Column names come from the cursor so plain-tuple connections work too.
    columns = [description[0] for description in cursor.description or ()]
    by_id: dict[int, dict[str, Any]] = {}
    for row in cursor.fetchall():
        values = dict(zip(columns, row))
        record = {
            "id": int(values["id"]),
            "name": values["name"],
            "kind": values["kind"],
            "storage_rel": values["storage_rel"],
            "source_path": values["source_path"],
            "group_id": int(values["group_id"]) if values["group_id"] is not None else None,
            "group_name": values["group_name"],
            "size_bytes": values["size_bytes"],
            "ext": values["ext"],
            "note": values["note"] or "",
            "tags": [tag for tag in (values["tag_blob"] or "").split("\x1f") if tag],
            "created_at": values["created_at"],
            "updated_at": values["updated_at"],
        }
        record["exists"] = _record_exists(record, assets_dir)
        by_id[record["id"]] = record

    return [by_id[asset_id] for asset_id in ids if asset_id in by_id]


def get_item(connection: sqlite3.Connection, asset_id: int, assets_dir: Path | None) -> dict[str, Any] | None:
    return _get_record(connection, asset_id, assets_dir)


def update_item(
    connection: sqlite3.Connection,
    asset_id: int,
    assets_dir: Path | None,
    name: str | None = None,
    group_id: int | None = None,
    tag_names: list[str] | None = None,
    note: str | None = None,
) -> dict[str, Any] | None:
    existing = _get_record(connection, asset_id, assets_dir)
    if existing is None:
        raise LookupError("素材不存在")

    if name is not None:
        cleaned = _normalize_name(name)
        if not cleaned:
            raise ValueError("素材名称不能为空")
        connection.execute("UPDATE asset_item SET name = ?, updated_at = ? WHERE id = ?",
                           (cleaned, _now(), asset_id))
    if group_id is not None:
        resolved = _group_id_or_default(connection, group_id)
        if (
            existing["kind"] == "copy"
            and existing["group_id"] != resolved
            and assets_dir is not None
        ):
            _move_copy_to_group(connection, assets_dir, existing, resolved)
        connection.execute("UPDATE asset_item SET group_id = ?, updated_at = ? WHERE id = ?",
                           (resolved, _now(), asset_id))
    if note is not None:
        connection.execute("UPDATE asset_item SET note = ?, updated_at = ? WHERE id = ?",
                           ((note or "").strip(), _now(), asset_id))
    if tag_names is not None:
        tag_ids = _resolve_tags(connection, _normalize_tag_names(tag_names))
        _attach_tags(connection, asset_id, tag_ids)

    connection.commit()
    return _get_record(connection, asset_id, assets_dir)


def delete_item(
    connection: sqlite3.Connection,
    assets_dir: Path,
    asset_id: int,
    delete_copy: bool = False,
) -> None:
    record = _get_record(connection, asset_id, assets_dir)
    if record is None:
        raise LookupError("素材不存在")

    # Reference records never remove the original file, regardless of the flag.
    if record["kind"] == "copy" and delete_copy and record["storage_rel"]:
        target = _copy_path(assets_dir, record["storage_rel"])
        if target is not None:
            target.unlink(missing_ok=True)

    connection.execute("DELETE FROM asset_item_tag WHERE asset_id = ?", (asset_id,))
    connection.execute("DELETE FROM asset_item WHERE id = ?", (asset_id,))
    connection.commit()


def get_record(
    connection: sqlite3.Connection, asset_id: int, assets_dir: Path | None = None
) -> dict[str, Any] | None:
    """Public read access to one asset record (used by other modules)."""
    _normalize_factory(connection)
    return _get_record(connection, asset_id, assets_dir)


def resolve_image_path(
    connection: sqlite3.Connection,
    assets_dir: Path,
    asset_id: int,
) -> Path | None:
    """Return the on-disk path for the protected proxy, or None when unavailable."""
    _normalize_factory(connection)
    record = _get_record(connection, asset_id, assets_dir)
    if record is None:
        return None

    if record["kind"] == "copy":
        if not record["storage_rel"]:
            return None
        target = _copy_path(assets_dir, record["storage_rel"])
        if target is None:
            return None
        if target.is_file() and target.suffix.lower() in IMAGE_SUFFIXES:
            return target
        return None

    source = record.get("source_path") or ""
    if not source:
        return None
    path = Path(source).expanduser()
    if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
        return path.resolve()
    return None
