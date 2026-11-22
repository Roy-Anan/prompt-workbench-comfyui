"""Workflow library management (metadata, copy, archive, delete).

This module is a thin, local-only management layer over the existing
``userdata/workflows/<id>/`` tree managed by :class:`workflow_service.WorkflowStore`.
It never rewrites ``original.json`` / ``working.json`` / ``analysis.json`` and
never touches prompt whitelists: it only maintains the sidecar
``manifest.json`` metadata (title / note / tags / favored / pinned / archived)
and performs whole-folder copy or delete.

Boundaries:

* Pure local file I/O; no network calls.
* Every id is validated as a UUID and resolved inside the workflow root, so
  path traversal is impossible.
* Deleting a workflow removes its draft box and version snapshots with it
  (drafts belong to the workflow). Generation history rows are *not* deleted:
  history keeps its own snapshot and simply loses the workflow link.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import datadir

MAX_TITLE_CHARS = 120
MAX_NOTE_CHARS = 1000
MAX_TAGS = 12
MAX_TAG_CHARS = 30
DEFAULT_TAG = "未分组"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root_dir() -> Path:
    """Workflow storage root, matching ``workflow_service.store()``."""
    return datadir.default_data_root() / "workflows"


def _folder(workflow_id: str) -> Path:
    root = root_dir().resolve()
    try:
        normalized = str(uuid.UUID(str(workflow_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("无效的工作流 ID") from exc
    folder = (root / normalized).resolve()
    try:
        folder.relative_to(root)
    except ValueError as exc:
        raise ValueError("工作流路径越界") from exc
    return folder


def _read_manifest(folder: Path) -> dict[str, Any]:
    path = folder / "manifest.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(folder: Path, manifest: dict[str, Any]) -> None:
    path = folder / "manifest.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _clean_title(raw: Any, fallback: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return fallback
    return text[:MAX_TITLE_CHARS]


def _clean_note(raw: Any) -> str:
    return str(raw or "").strip()[:MAX_NOTE_CHARS]


def _clean_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = str(item or "").strip()[:MAX_TAG_CHARS]
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) >= MAX_TAGS:
            break
    return tags


def _count_files(folder: Path, suffix: str = ".json") -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for item in folder.iterdir() if item.is_file() and item.suffix == suffix)


def _count_dirs(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for item in folder.iterdir() if item.is_dir())


def _to_card(folder: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    filename = manifest.get("filename") or ""
    return {
        "id": manifest.get("id") or folder.name,
        "filename": filename,
        "title": _clean_title(manifest.get("title"), filename),
        "note": _clean_note(manifest.get("note")),
        "tags": _clean_tags(manifest.get("tags")),
        "favored": bool(manifest.get("favored", False)),
        "pinned": bool(manifest.get("pinned", False)),
        "archived": bool(manifest.get("archived", False)),
        "format": manifest.get("format") or "",
        "confirmed": bool(manifest.get("confirmed", False)),
        "imported_at": manifest.get("imported_at") or "",
        "updated_at": manifest.get("updated_at") or manifest.get("imported_at") or "",
        "last_written_at": manifest.get("last_written_at"),
        "draft_count": _count_files(folder / "drafts"),
        "version_count": _count_dirs(folder / "versions"),
    }


def list_workflows(include_archived: bool = True, force_meta: bool = False) -> list[dict[str, Any]]:
    """List imported workflows as management cards.

    ``archived`` entries are returned too unless ``include_archived`` is False;
    the caller (UI) decides whether to show the recycle bin. Ordering is
    pinned -> favored -> updated_at descending, so the library stays predictable
    without mutating updated_at when toggling favored/pinned.
    """
    root = root_dir()
    cards: list[dict[str, Any]] = []
    if not root.is_dir():
        return cards
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        manifest = _read_manifest(folder)
        if not manifest:
            continue
        if not manifest.get("id"):
            manifest["id"] = folder.name
        card = _to_card(folder, manifest)
        if not include_archived and card["archived"]:
            continue
        cards.append(card)
    cards.sort(
        key=lambda item: (
            item["pinned"],
            item["favored"],
            item["updated_at"] or "",
        ),
        reverse=True,
    )
    return cards


def get_workflow(workflow_id: str) -> dict[str, Any]:
    folder = _folder(workflow_id)
    manifest = _read_manifest(folder)
    if not manifest:
        raise FileNotFoundError("工作流不存在")
    manifest.setdefault("id", folder.name)
    return _to_card(folder, manifest)


def update_meta(workflow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Update user-facing manifest metadata; unknown keys are ignored."""
    folder = _folder(workflow_id)
    manifest = _read_manifest(folder)
    if not manifest:
        raise FileNotFoundError("工作流不存在")

    if "title" in payload:
        manifest["title"] = _clean_title(payload.get("title"), manifest.get("filename") or "")
    if "note" in payload:
        manifest["note"] = _clean_note(payload.get("note"))
    if "tags" in payload:
        manifest["tags"] = _clean_tags(payload.get("tags"))
    if "favored" in payload:
        manifest["favored"] = bool(payload.get("favored"))
    if "pinned" in payload:
        manifest["pinned"] = bool(payload.get("pinned"))

    manifest["updated_at"] = _now()
    _write_manifest(folder, manifest)
    return _to_card(folder, manifest)


def duplicate(workflow_id: str, title: str = "", with_drafts: bool = False) -> dict[str, Any]:
    """Deep-copy one workflow folder under a fresh UUID.

    The copy keeps the same graph, whitelist and confirmed state (identical
    content), but takes a fresh id and its own draft box. Drafts are copied
    only when explicitly requested.
    """
    source = _folder(workflow_id)
    source_manifest = _read_manifest(source)
    if not source_manifest:
        raise FileNotFoundError("工作流不存在")

    new_id = str(uuid.uuid4())
    target = _folder(new_id)
    target.mkdir(parents=True, exist_ok=False)
    try:
        for name in ("original.json", "working.json", "analysis.json"):
            src = source / name
            if src.is_file():
                shutil.copy2(src, target / name)
        if with_drafts and (source / "drafts").is_dir():
            shutil.copytree(source / "drafts", target / "drafts")

        manifest = dict(source_manifest)
        manifest["id"] = new_id
        manifest["title"] = _clean_title(
            title or f"{source_manifest.get('title') or source_manifest.get('filename') or '工作流'} 副本",
            source_manifest.get("filename") or "",
        )
        manifest["note"] = _clean_note(source_manifest.get("note"))
        manifest["favored"] = False
        manifest["pinned"] = False
        manifest["archived"] = False
        manifest["imported_at"] = _now()
        manifest["updated_at"] = _now()
        _write_manifest(target, manifest)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return _to_card(target, manifest)


def set_archived(workflow_id: str, archived: bool) -> dict[str, Any]:
    """Soft-delete (recycle bin) or restore one workflow."""
    folder = _folder(workflow_id)
    manifest = _read_manifest(folder)
    if not manifest:
        raise FileNotFoundError("工作流不存在")
    manifest["archived"] = bool(archived)
    manifest["updated_at"] = _now()
    _write_manifest(folder, manifest)
    return _to_card(folder, manifest)


def delete_workflow(workflow_id: str) -> dict[str, Any]:
    """Permanently remove the workflow folder (drafts and version snapshots too).

    Generation history is intentionally untouched: history rows keep their own
    prompt/model snapshot and merely lose the workflow link.
    """
    folder = _folder(workflow_id)
    if not folder.is_dir():
        raise FileNotFoundError("工作流不存在")
    manifest = _read_manifest(folder)
    shutil.rmtree(folder, ignore_errors=True)
    return {
        "id": workflow_id,
        "deleted": True,
        "filename": manifest.get("filename") or "",
    }
