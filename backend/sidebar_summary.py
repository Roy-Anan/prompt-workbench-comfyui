"""Read-only desensitized aggregate summary for the ComfyUI sidebar panel.

The external extension panel runs in the ComfyUI browser origin and cannot hold
the launcher's in-memory session ticket. It reaches the backend through the
extension's local read-only proxy (which carries the node token server-side).

This module builds a conservative, non-sensitive projection of the workbench
data: Prompt texts, draft notes, environment snapshots, absolute file paths and
image lists are intentionally omitted. Each section is capped to a small number
of items and reports its own total; a failure in one section degrades that
section alone instead of failing the whole request.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asset_library
import datadir
import generation_history
import model_catalog
import model_identity
import rule_store
from workflow_service import WorkflowStore, WorkflowError

# Keep the sidebar panel light: only a handful of rows per section are sent.
MAX_ITEMS = 20
MAX_TEXT = 120


def _clip(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _section(items: list[dict[str, Any]], total: int, error: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"total": total, "items": items[:MAX_ITEMS]}
    if error:
        payload["error"] = error
    return payload


def _rules_section(connection: sqlite3.Connection) -> dict[str, Any]:
    records = rule_store.list_rules(connection)
    items = [
        {
            "level": rec.get("level") or "",
            "scope_key": rec.get("scope_key") or "",
            "rule_key": rec.get("rule_key") or "",
            "value": _clip(rec.get("value")),
        }
        for rec in records
    ]
    return _section(items, len(records))


def _snapshot_file(nodes: Any) -> str:
    """Extract the first file name from a snapshot node list.

    Snapshot entries are ``{"node_id", "file"}`` dicts; older shapes may hold
    plain strings. Missing or malformed entries degrade to an empty string.
    """
    if not isinstance(nodes, list) or not nodes:
        return ""
    first = nodes[0]
    if isinstance(first, dict):
        return str(first.get("file") or "")
    return str(first or "")


def _history_section(connection: sqlite3.Connection) -> dict[str, Any]:
    records = generation_history.list_history(connection)
    items: list[dict[str, Any]] = []
    for rec in records:
        snapshot = rec.get("model_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        model = _snapshot_file(snapshot.get("checkpoints")) or _snapshot_file(
            snapshot.get("loras")
        )
        prompt_id = str(rec.get("prompt_id") or "")
        items.append(
            {
                "prompt_id": prompt_id[:8],
                "status": rec.get("task_status") or "",
                "created_at": rec.get("created_at") or "",
                "model": _clip(model, 80),
            }
        )
    return _section(items, len(records))


def _assets_section(connection: sqlite3.Connection, assets_dir: Path) -> dict[str, Any]:
    records = asset_library.list_items(connection, assets_dir)
    items = [
        {
            "name": rec.get("name") or "",
            "kind": rec.get("kind") or "",
            "group_name": rec.get("group_name") or "",
            "tags": list(rec.get("tags") or []),
        }
        for rec in records
    ]
    return _section(items, len(records))


def _workflows_and_drafts(store: WorkflowStore) -> tuple[dict[str, Any], dict[str, Any]]:
    workflows = store.list_workflows()
    wf_items = [
        {
            "id": wf.get("id") or "",
            "title": wf.get("filename") or "",
            "confirmed": bool(wf.get("confirmed", False)),
        }
        for wf in workflows
    ]
    workflows_section = _section(wf_items, len(workflows))

    draft_items: list[dict[str, Any]] = []
    draft_total = 0
    title_by_id = {wf.get("id"): wf.get("filename") or "" for wf in workflows}
    for wf in workflows:
        wf_id = wf.get("id") or ""
        drafts = store.list_drafts(wf_id)
        draft_total += len(drafts)
        for draft in drafts:
            draft_items.append(
                {
                    "workflow_id": wf_id,
                    "workflow_title": title_by_id.get(wf_id, ""),
                    "name": draft.get("name") or "",
                    "tags": list(draft.get("tags") or []),
                    "favored": bool(draft.get("favored", False)),
                    "pinned": bool(draft.get("pinned", False)),
                    "updated_at": draft.get("updated_at") or "",
                }
            )
    draft_items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    drafts_section = _section(draft_items, draft_total)
    return workflows_section, drafts_section


def _loras_section(settings: dict[str, Any]) -> dict[str, Any]:
    connections = settings.get("connections") or {}
    storage = settings.get("storage") or {}
    catalog = model_catalog.fetch_catalog(
        comfyui_url=connections.get("comfyui_url", ""),
        checkpoint_dir=storage.get("checkpoint", ""),
        lora_dir=storage.get("lora", ""),
        clip_dir=storage.get("clip", ""),
        vae_dir=storage.get("vae", ""),
    )
    loras = catalog.get("loras") or []
    items = [{"name": entry.get("name") or ""} for entry in loras if isinstance(entry, dict)]
    section = _section(items, len(loras))
    if not catalog.get("online", True):
        section["error"] = catalog.get("error") or "ComfyUI 离线"
    return section


def build_summary(data_root: Path, settings: dict[str, Any]) -> dict[str, Any]:
    """Assemble the desensitized sidebar summary.

    Each section is wrapped independently so one failing domain cannot take
    down the others. The returned payload never contains Prompt text, draft
    notes, snapshots, absolute paths or image file lists.
    """
    sections: dict[str, Any] = {}

    try:
        with model_identity.connect(datadir.database_path(data_root)) as connection:
            generation_history.ensure_schema(connection)
            rule_store.ensure_schema(connection)
            asset_library.ensure_schema(connection)
            sections["rules"] = _rules_section(connection)
            sections["history"] = _history_section(connection)
            sections["assets"] = _assets_section(connection, data_root / "assets")
    except Exception as exc:
        # Each section must degrade alone. A narrower catch let a TypeError
        # (e.g. a connection without row_factory) escape as a 500 that took the
        # whole sidebar down instead of marking one section failed.
        sections.setdefault("rules", _section([], 0, str(exc)))
        sections.setdefault("history", _section([], 0, str(exc)))
        sections.setdefault("assets", _section([], 0, str(exc)))

    try:
        store = WorkflowStore(data_root / "workflows")
        workflows_section, drafts_section = _workflows_and_drafts(store)
        sections["workflows"] = workflows_section
        sections["drafts"] = drafts_section
    except Exception as exc:
        sections.setdefault("workflows", _section([], 0, str(exc)))
        sections.setdefault("drafts", _section([], 0, str(exc)))

    try:
        sections["loras"] = _loras_section(settings)
    except Exception as exc:
        sections["loras"] = _section([], 0, str(exc))

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": sections,
    }
