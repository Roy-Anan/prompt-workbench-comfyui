"""Verified, conservative restore for the local workbench backup archive.

Scope and safety posture (local single-user tool, fail-closed):

  * The archive is the ZIP produced by ``backup_export``; its ``manifest.json``
    must match this app before anything is written.
  * Every member path is normalized and whitelisted against the managed layout;
    absolute paths, drive letters and ``..`` traversal are rejected.
  * The archived SQLite database is opened on a temporary copy and must pass
    ``PRAGMA integrity_check``; a non-ok result aborts the restore before any
    existing data is touched.
  * A pre-restore backup of the current data root is written first; if that
    backup fails, the restore aborts and the existing data stays intact.
  * Conflict policy: by default existing files are kept (skipped, reported);
    ``overwrite=True`` replaces same-path files. The database is restored as a
    whole file (no table-level merge). ComfyUI-owned weights and original
    images are never present in the archive and are never touched here.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import backup_export
import datadir

APP_NAME = "ComfyUI-Prompt-Workbench"

# Whitelisted top-level prefixes a member may live under.
ALLOWED_PREFIXES = (
    Path("workflows"),
    Path("knowledge"),
    Path("prompts"),
    Path("drafts"),
    Path("history"),
    Path("settings"),
    Path("database"),
)

SETTINGS_REL = datadir.SETTINGS_REL
DATABASE_REL = datadir.DATABASE_REL


class RestoreError(Exception):
    """Raised when the archive cannot be safely restored.

    The message is a conservative, user-readable description; callers map it
    to a 4xx response. No partial restore is performed when this is raised.
    """


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _safe_member_path(name: str) -> Path:
    """Normalize an archive member name and reject traversal / absolute paths."""
    if not name or name.endswith("/"):
        raise RestoreError("归档包含无效目录条目")
    raw = name.replace("\\", "/")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise RestoreError("归档包含越界路径")
    candidate = Path(*parts)
    if candidate.is_absolute() or candidate.drive:
        raise RestoreError("归档包含绝对路径，已拒绝")
    # Reject Windows drive / UNC style names explicitly.
    if any(":" in part for part in parts):
        raise RestoreError("归档包含非法路径标识")
    return candidate


def _whitelisted(rel: Path) -> bool:
    return any(rel == prefix or rel.is_relative_to(prefix) for prefix in ALLOWED_PREFIXES)


def _check_database_integrity(db_bytes: bytes) -> str:
    """Open the archived DB on a temp copy and run the read-only integrity probe."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        handle.write(db_bytes)
        tmp = Path(handle.name)
    try:
        connection = sqlite3.connect(str(tmp), timeout=10)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        status = str(row[0]) if row else "unknown"
        return status
    except sqlite3.Error as exc:
        raise RestoreError(f"归档数据库无法打开：{type(exc).__name__}: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{_stamp()}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _prepare_database_replacement(root: Path) -> None:
    """Checkpoint and drop WAL sidecars before the live database is swapped.

    Replacing the main file while a leftover ``-wal`` sits next to it can leave
    the pair out of sync, and on Windows an open handle makes ``os.replace``
    fail outright with a bare PermissionError. This is best-effort: if a
    connection still holds the file, the swap below reports the real error.
    """
    database = root / DATABASE_REL
    if not database.exists():
        return
    try:
        with sqlite3.connect(database, timeout=2.0) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass
    for suffix in ("-wal", "-shm"):
        side = database.with_name(database.name + suffix)
        try:
            side.unlink(missing_ok=True)
        except OSError:
            pass


def restore_backup(
    root: Path,
    archive_bytes: bytes,
    overwrite: bool,
    version: str,
) -> dict[str, Any]:
    """Restore a verified backup archive into ``root``.

    Raises :class:`RestoreError` before writing existing data when the archive
    is invalid, fails integrity, or the pre-restore backup cannot be created.

    File members are written first and the database last, so a failed restore
    leaves the previous database in place; the returned ``restart_required``
    flag tells the UI the running instance still holds old connections and
    should be restarted before the restored data is trusted.
    """
    root = root.resolve()
    if not archive_bytes:
        raise RestoreError("归档内容为空")
    if not zipfile.is_zipfile(io.BytesIO(archive_bytes)):
        raise RestoreError("不是有效的 ZIP 归档")

    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise RestoreError(f"ZIP 无法打开：{exc}") from exc

    with archive:
        names = archive.namelist()
        if "manifest.json" not in names:
            raise RestoreError("归档缺少 manifest.json，已拒绝恢复")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RestoreError(f"manifest.json 解析失败：{exc}") from exc
        if not isinstance(manifest, dict):
            raise RestoreError("manifest.json 结构无效")
        if manifest.get("app") != APP_NAME:
            raise RestoreError("归档来源应用不匹配，已拒绝恢复")

        members: list[tuple[Path, bytes]] = []
        for name in names:
            if name == "manifest.json":
                continue
            rel = _safe_member_path(name)
            if not _whitelisted(rel):
                raise RestoreError(f"归档包含受保护/未知路径：{rel.as_posix()}")
            try:
                data = archive.read(name)
            except (OSError, zipfile.BadZipFile) as exc:
                raise RestoreError(f"归档条目读取失败：{rel.as_posix()}：{exc}") from exc
            members.append((rel, data))

        database_member: tuple[Path, bytes] | None = None
        file_members: list[tuple[Path, bytes]] = []
        for rel, data in members:
            if rel == DATABASE_REL:
                database_member = (rel, data)
            else:
                file_members.append((rel, data))

        # Verify the archived database before touching any existing data.
        db_status = "kept"
        if database_member is not None:
            integrity = _check_database_integrity(database_member[1])
            if integrity != "ok":
                raise RestoreError(f"归档数据库完整性校验未通过：{integrity}")
            db_status = "ready"

    # Pre-restore backup of the current data root; fail closed if it cannot be
    # created, so the existing state can always be rolled back manually.
    backups_dir = root / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    pre_backup_name = f"restore-pre-{_stamp()}.zip"
    pre_backup_path = backups_dir / pre_backup_name
    try:
        pre_bytes, _manifest = backup_export.build_backup_archive(root, version)
        pre_backup_path.write_bytes(pre_bytes)
    except OSError as exc:
        raise RestoreError(f"恢复前备份失败，现有数据保持原样：{exc}") from exc

    restored: list[str] = []
    skipped_existing: list[str] = []
    overwritten = 0

    for rel, data in file_members:
        target = root / rel
        if target.exists() and not overwrite:
            skipped_existing.append(rel.as_posix())
            continue
        if target.exists() and overwrite:
            overwritten += 1
        if rel == SETTINGS_REL:
            # Rewrite managed storage paths against the current root; external
            # ComfyUI paths and connection addresses are carried as archived.
            try:
                parsed = json.loads(data.decode("utf-8"))
                if isinstance(parsed, dict):
                    parsed = datadir.rewrite_settings_paths(parsed, root)
                    data = json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError):
                # Leave a settings file that does not parse as-is; do not invent
                # a repaired settings object.
                pass
        _atomic_write(target, data)
        restored.append(rel.as_posix())

    if database_member is not None:
        _prepare_database_replacement(root)
        _atomic_write(root / DATABASE_REL, database_member[1])
        db_status = "restored"

    return {
        "pre_backup": f"backups/{pre_backup_name}",
        "overwrite": overwrite,
        "restart_required": db_status == "restored",
        "manifest_version": manifest.get("version"),
        "manifest_generated_at": manifest.get("generated_at"),
        "restored_count": len(restored),
        "restored": restored,
        "skipped_existing": skipped_existing,
        "overwritten_count": overwritten,
        "database": db_status,
    }
