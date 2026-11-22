"""Unified data-directory service for the local Web runtime.

All business code resolves data paths through this module instead of hard-coding
``%LOCALAPPDATA%`` or a project path in several places.

Default layout (inside the Git project, kept out of version control):

    <repo-root>/userdata/
        database/      SQLite database
        knowledge/     model / architecture / LoRA knowledge material
        prompts/       system rules and prompt templates
        workflows/     workflow copies managed by the workbench
        drafts/        prompt drafts
        history/       operation history and generation records
        cache/         rebuildable cache
        logs/          runtime logs
        backups/       data backups
        exports/       user-initiated exports
        settings/      workbench configuration files

ComfyUI-owned data (Checkpoint / LoRA weights, VAE, generated images, ComfyUI
output and model caches) stays in the ComfyUI directories; only paths and
metadata are recorded here.

The default root can be overridden with the ``WORKBENCH_DATA_ROOT`` environment
variable (future per-user data-root change goes through a single entry here).
A legacy ``%LOCALAPPDATA%\\ComfyUI-Prompt-Workbench`` root is detected and
migrated safely on first start; the old directory is never deleted.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_NAME = "ComfyUI-Prompt-Workbench"

# Managed sub-directories under the data root.
MANAGED_KEYS = (
    "database",
    "knowledge",
    "prompts",
    "workflows",
    "drafts",
    "history",
    "assets",
    "cache",
    "logs",
    "backups",
    "exports",
)

# Optional external ComfyUI directories; recorded as paths only, never copied
# into the workbench data root.
OPTIONAL_EXTERNAL_KEYS = ("checkpoint", "lora", "clip", "vae", "output", "comfy_workflows")

SETTINGS_REL = Path("settings") / "settings.json"
DATABASE_REL = Path("database") / "workbench.sqlite3"
NODE_TOKEN_REL = Path("settings") / "node_token.json"


def repo_root() -> Path:
    """Project repository root (parent of the ``backend`` directory)."""
    return Path(__file__).resolve().parent.parent


def default_data_root() -> Path:
    """Resolve the default data root.

    Order: ``WORKBENCH_DATA_ROOT`` env var -> ``<repo>/userdata``.
    """
    override = os.environ.get("WORKBENCH_DATA_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (repo_root() / "userdata").resolve()


def legacy_data_root() -> Path:
    """Previous default root under ``%LOCALAPPDATA%`` (kept for migration)."""
    local = os.environ.get(
        "LOCALAPPDATA",
        str(Path.home() / "AppData" / "Local"),
    )
    return Path(local) / APP_NAME


def managed_subdirs(root: Path) -> dict[str, Path]:
    return {key: root / key for key in MANAGED_KEYS}


def settings_path(root: Path) -> Path:
    return root / SETTINGS_REL


def database_path(root: Path) -> Path:
    return root / DATABASE_REL


def node_token_path(root: Path) -> Path:
    """Persistence location for the local ComfyUI-extension node token."""
    return root / NODE_TOKEN_REL


def load_or_create_node_token(root: Path) -> str:
    """Read the persisted extension node token, generating it on first use.

    ComfyUI custom nodes run in the integrated Python process and cannot see
    the launcher's in-memory bootstrap token. This shared-machine token is
    persisted under the data root (never committed) and reused across restarts
    so a running ComfyUI keeps working after the workbench restarts. It is a
    read-only credential: the backend rejects state-changing requests that
    present it.
    """
    path = node_token_path(root)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            token = data.get("token")
            if isinstance(token, str) and token.strip():
                return token.strip()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass  # corrupt/partial file -> regenerate below.

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "token": token,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = path.with_name(
        f"{path.name}.{datetime.now(timezone.utc).strftime('%H%M%S%f')}.tmp"
    )
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return token


def _has_real_data(path: Path) -> bool:
    """True if a directory holds any non-empty file (ignores empty dirs)."""
    if not path.is_dir():
        return False
    for entry in path.rglob("*"):
        if entry.is_file() and entry.stat().st_size > 0:
            return True
    return False


def _sqlite_integrity_ok(db_file: Path) -> bool:
    if not db_file.is_file():
        return True  # Nothing to check.
    # Only run the SQLite check against a real database file; other copied
    # files (settings, knowledge, drafts, ...) are not SQLite.
    if db_file.suffix.lower() not in {".sqlite3", ".db", ".sqlite"}:
        return True
    try:
        with sqlite3.connect(db_file, timeout=10) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(result) and result[0] == "ok"
    except sqlite3.Error:
        return False


def _data_files(root: Path) -> list[Path]:
    """Non-empty files under ``root``, excluding prior migration backups and
    the legacy root-level settings file (managed paths are rebuilt against
    the new root)."""
    files: list[Path] = []
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(root)
        if rel.parts and rel.parts[0] == "backups":
            continue
        if rel == Path("settings.json"):
            continue
        if entry.stat().st_size > 0:
            files.append(entry)
    return files


def _backup_old_root(old: Path, backup_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"legacy-migration-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for entry in _data_files(old):
        rel = entry.relative_to(old)
        target = backup / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, target)
    return backup


def migrate_if_needed(root: Path) -> dict[str, Any]:
    """Safely migrate legacy data into ``root`` when applicable.

    Returns a report describing what happened. The old directory is never
    deleted. Strategy:

      * Legacy root missing -> nothing to migrate.
      * New root already has real data -> do not overwrite; report conflict.
      * New root empty, legacy has data -> back up legacy first, copy managed
        files and the SQLite database (plus WAL/SHM sidecars if present), then
        run an integrity check. On any failure the old data stays intact.
      * Legacy present but empty -> no migration.
    """
    old = legacy_data_root()
    report: dict[str, Any] = {
        "legacy_root_exists": old.exists(),
        "legacy_has_data": _has_real_data(old),
        "target_has_data": _has_real_data(root),
        "action": "none",
        "details": "",
        "backup": None,
        "database_integrity": None,
    }

    if not old.exists():
        report["details"] = "Legacy data root does not exist; using the new root."
        return report

    if not report["legacy_has_data"]:
        report["details"] = "Legacy root exists but holds no real data; no migration."
        return report

    if report["target_has_data"]:
        report["action"] = "skipped-conflict"
        report["details"] = (
            "Both legacy and new roots hold data; left the new root untouched "
            "to avoid overwriting."
        )
        return report

    root.mkdir(parents=True, exist_ok=True)
    backup_dir = root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    try:
        backup = _backup_old_root(old, backup_dir)
        report["backup"] = str(backup.relative_to(root))
    except OSError as exc:
        report["action"] = "failed-backup"
        report["details"] = f"Backup failed before any copy; old data kept: {exc}"
        return report

    copied: list[str] = []
    try:
        for entry in _data_files(old):
            rel = entry.relative_to(old)
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, target)
            copied.append(str(rel))
    except OSError as exc:
        report["action"] = "failed-copy"
        report["details"] = f"Copy failed; old data kept: {exc}"
        return report

    db = database_path(root)
    report["database_integrity"] = _sqlite_integrity_ok(db)
    if not report["database_integrity"]:
        report["action"] = "failed-integrity"
        report["details"] = (
            "Database integrity check failed after copy; old data kept. "
            "Review the backup before retrying."
        )
        return report

    report["action"] = "migrated"
    report["details"] = (
        f"Migrated {len(copied)} file(s) from legacy root; old directory kept."
    )
    return report


def prepare_root(root: Path) -> dict[str, Any]:
    """Create managed directories and run legacy migration.

    Returns the migration report. Managed directories are created regardless of
    whether migration ran. The legacy data-file/DB migration and the settings
    migration are separate channels; the database is never re-migrated here.
    """
    root.mkdir(parents=True, exist_ok=True)
    for path in managed_subdirs(root).values():
        path.mkdir(parents=True, exist_ok=True)
    report = migrate_if_needed(root)
    report["settings_migration"] = migrate_legacy_settings(root)
    return report


def rewrite_settings_paths(settings: dict[str, Any], root: Path) -> dict[str, Any]:
    """Point managed storage paths at ``root``; keep external paths as-is."""
    storage = settings.get("storage", {})
    if not isinstance(storage, dict):
        storage = {}

    new_storage: dict[str, str] = {"root": str(root)}
    for key, path in managed_subdirs(root).items():
        new_storage[key] = str(path)
    for key in OPTIONAL_EXTERNAL_KEYS:
        value = storage.get(key, "")
        new_storage[key] = value if isinstance(value, str) else ""

    settings["storage"] = new_storage
    return settings


def default_settings(root: Path) -> dict[str, Any]:
    return {
        "version": 2,
        "storage": {
            "root": str(root),
            **{key: str(path) for key, path in managed_subdirs(root).items()},
            "checkpoint": "",
            "lora": "",
            "clip": "",
            "vae": "",
            "output": "",
            "comfy_workflows": "",
        },
        "connections": {
            "ollama_url": "http://127.0.0.1:11434",
            "comfyui_url": "http://127.0.0.1:8188",
        },
        "prompt": {
            "protected_terms": [],
        },
    }


def load_settings_file(root: Path) -> dict[str, Any] | None:
    file_path = settings_path(root)
    if not file_path.exists():
        return None
    loaded = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("Settings root must be an object")
    return loaded


def save_settings_file(root: Path, settings: dict[str, Any]) -> None:
    file_path = settings_path(root)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_name(
        f"{file_path.name}.{datetime.now(timezone.utc).strftime('%H%M%S%f')}.tmp"
    )
    try:
        tmp.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(file_path)
    finally:
        tmp.unlink(missing_ok=True)


# ---- Legacy settings migration (settings.json only; database is not touched) ----

LEGACY_SETTINGS_REL = Path("settings.json")

# Connection keys carried over verbatim from the old settings.
LEGACY_CONNECTION_KEYS = ("ollama_url", "comfyui_url")


def legacy_settings_path() -> Path:
    """Old root-level settings.json under the legacy AppData root."""
    return legacy_data_root() / LEGACY_SETTINGS_REL


def _backup_legacy_settings(old_file: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"legacy-settings-{stamp}.json"
    shutil.copy2(old_file, backup)
    return backup


def _merge_legacy_settings(
    legacy: dict[str, Any],
    current: dict[str, Any],
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Map legacy settings onto the current schema without overwriting kept values.

    Managed storage paths are rebuilt against ``root``; external ComfyUI paths
    and connection addresses are preserved. When a key already exists in the
    current settings, its existing user value wins and the conflict is recorded.
    Unknown safe-to-keep keys are carried under a reserved ``_legacy_extra``
    section so they are not silently dropped.
    """
    merged = {
        "version": legacy.get("version", current.get("version", 2)),
        "storage": dict(current.get("storage", {})),
        "connections": dict(current.get("connections", {})),
    }

    conflicts: list[str] = []
    unknown: dict[str, Any] = {}

    old_storage = legacy.get("storage")
    if isinstance(old_storage, dict):
        for key in OPTIONAL_EXTERNAL_KEYS:
            if key in old_storage and isinstance(old_storage[key], str):
                if key in merged["storage"] and merged["storage"][key]:
                    if merged["storage"][key] != old_storage[key]:
                        conflicts.append(f"storage.{key}（保留当前值）")
                else:
                    merged["storage"][key] = old_storage[key]

    old_connections = legacy.get("connections")
    if isinstance(old_connections, dict):
        for key in LEGACY_CONNECTION_KEYS:
            if key in old_connections and isinstance(old_connections[key], str):
                if key in merged["connections"] and merged["connections"][key]:
                    if merged["connections"][key] != old_connections[key]:
                        conflicts.append(f"connections.{key}（保留当前值）")
                else:
                    merged["connections"][key] = old_connections[key]

    # Preserve legacy top-level keys that are not part of the current schema,
    # except managed storage paths which must never point back at AppData.
    known_top = {"version", "storage", "connections"}
    for key, value in legacy.items():
        if key in known_top:
            continue
        unknown[key] = value

    # Preserve any legacy storage key that is neither managed nor external.
    if isinstance(old_storage, dict):
        for key, value in old_storage.items():
            if key in MANAGED_KEYS or key in OPTIONAL_EXTERNAL_KEYS:
                continue
            unknown.setdefault("storage", {})[key] = value

    preserved: list[str] = []
    if unknown:
        merged["_legacy_extra"] = unknown
        preserved = sorted(unknown.keys())

    report = {"conflicts": conflicts, "preserved_unknown": preserved}
    return merged, report


def migrate_legacy_settings(root: Path) -> dict[str, Any]:
    """Migrate the legacy root-level settings.json into the new settings file.

    This handles settings only; the database is not re-migrated and existing
    userdata data is never rebuilt or overwritten. The old AppData file is kept.
    """
    old_file = legacy_settings_path()
    report: dict[str, Any] = {
        "legacy_settings_exists": old_file.exists(),
        "action": "none",
        "details": "",
        "backup": None,
        "fields": {},
        "conflicts": [],
        "unknown_preserved": [],
    }

    if not old_file.exists():
        report["details"] = "Legacy settings.json not found; nothing to migrate."
        return report

    try:
        legacy = json.loads(old_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        report["action"] = "failed-corrupt"
        report["details"] = f"Legacy settings unreadable; left untouched: {exc}"
        return report

    if not isinstance(legacy, dict):
        report["action"] = "failed-invalid"
        report["details"] = "Legacy settings root is not an object; left untouched."
        return report

    backup = _backup_legacy_settings(old_file, root / "backups")
    report["backup"] = str(backup.relative_to(root))

    try:
        current = load_settings_file(root)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # A corrupt current settings file is left for main.py's own backup /
        # reset path; do not overwrite it from the legacy file here.
        report["action"] = "skipped-corrupt-current"
        report["details"] = "Current settings are corrupt; left untouched for backup/reset."
        return report

    if current is None:
        current = default_settings(root)
        report["action"] = "migrated"
        report["details"] = "No current settings; built defaults from legacy values."
    else:
        report["action"] = "merged"
        report["details"] = "Current settings exist; merged without overwriting."

    merged, merge_report = _merge_legacy_settings(legacy, current, root)
    merged = rewrite_settings_paths(merged, root)
    save_settings_file(root, merged)

    old_conn = legacy.get("connections", {}) if isinstance(legacy, dict) else {}
    old_storage = legacy.get("storage", {}) if isinstance(legacy, dict) else {}
    report["fields"] = {
        "ollama_url": merged["connections"].get("ollama_url", ""),
        "comfyui_url": merged["connections"].get("comfyui_url", ""),
        "checkpoint": merged["storage"].get("checkpoint", ""),
        "lora": merged["storage"].get("lora", ""),
        "legacy_ollama_url": old_conn.get("ollama_url", ""),
        "legacy_comfyui_url": old_conn.get("comfyui_url", ""),
        "legacy_checkpoint": old_storage.get("checkpoint", ""),
        "legacy_lora": old_storage.get("lora", ""),
    }
    report["conflicts"] = merge_report["conflicts"]
    report["unknown_preserved"] = merge_report["preserved_unknown"]
    return report
