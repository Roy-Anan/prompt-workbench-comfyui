"""Manual backfill of natively queued canvas generations into local history.

The workbench no longer submits prompts to ComfyUI. This module reads the
ComfyUI ``/history`` payload on demand, reverse-parses the API workflow stored
on each entry, and records missing generations locally. Ownership is matched to
an existing workbench workflow by checkpoint / LoRA / resolution signatures;
entries with no confident match stay unbound (``workflow_id = ""``), which the
UI labels as a directly generated canvas record.

Only local index rows are written; ComfyUI output images are never copied or
modified here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import comfy_tasks
import generation_history
from workflow_service import analyze_workflow


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prompt_array(entry: dict[str, Any]) -> list[Any] | None:
    prompt = entry.get("prompt")
    return prompt if isinstance(prompt, list) else None


def _prompt_api(entry: dict[str, Any]) -> dict[str, Any] | None:
    arr = _prompt_array(entry)
    if arr and len(arr) >= 3 and isinstance(arr[2], dict):
        return arr[2]
    return None


def _extra_data(entry: dict[str, Any]) -> dict[str, Any]:
    arr = _prompt_array(entry)
    if arr and len(arr) >= 4 and isinstance(arr[3], dict):
        return arr[3]
    return {}


def _created_at(entry: dict[str, Any]) -> str:
    create_time = _extra_data(entry).get("create_time")
    if isinstance(create_time, (int, float)) and create_time > 0:
        try:
            return datetime.fromtimestamp(
                create_time / 1000, timezone.utc
            ).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return _now()


def _status(entry: dict[str, Any]) -> tuple[str, str | None]:
    status = entry.get("status")
    status_str: str | None = None
    completed: Any = None
    error: str | None = None
    if isinstance(status, dict):
        raw_str = status.get("status_str")
        status_str = raw_str if isinstance(raw_str, str) else None
        completed = status.get("completed")
        messages = status.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if (
                    isinstance(message, list)
                    and message and message[0] == "execution_error"
                    and isinstance(message[1], dict)
                ):
                    detail = message[1].get("exception_message")
                    if isinstance(detail, str) and detail.strip():
                        error = detail.strip()
                        break
    return comfy_tasks.classify_history_status(status_str, completed), error


def _prompts_from_report(report: dict[str, Any]) -> tuple[str, str]:
    """Split report text nodes into positive / negative snapshot text.

    When sampler links are recognized, nodes are grouped by their traced role.
    Without recognizable sampler links, role-less text nodes are conservatively
    kept as positive; an empty negative is left empty rather than guessed.
    """
    positive: list[str] = []
    negative: list[str] = []
    nodes_by_id = {
        str(node.get("node_id")): node
        for node in report.get("prompt_nodes") or []
        if isinstance(node, dict)
    }
    groups = report.get("prompt_groups") or []
    linked = False
    for group in groups:
        if not isinstance(group, dict):
            continue
        for role, bucket in (("positive", positive), ("negative", negative)):
            for node_id in group.get(f"{role}_node_ids") or []:
                node = nodes_by_id.get(str(node_id))
                if node is None:
                    continue
                text = node.get("text")
                if isinstance(text, str) and text.strip():
                    bucket.append(text.strip())
                    linked = True
    if not linked:
        for node in report.get("prompt_nodes") or []:
            roles = node.get("roles")
            if roles:
                continue
            text = node.get("text")
            if isinstance(text, str) and text.strip():
                positive.append(text.strip())
    return " / ".join(positive), " / ".join(negative)


def _signature(report: dict[str, Any]) -> dict[str, set[str] | str | None]:
    snapshot = generation_history.extract_model_snapshot(report)
    checkpoints = {
        str(item["file"]).lower()
        for item in snapshot["checkpoints"]
        if isinstance(item, dict) and item.get("file")
    }
    loras = {
        str(item["file"]).lower()
        for item in snapshot["loras"]
        if isinstance(item, dict) and item.get("file")
    }
    return {
        "checkpoints": checkpoints,
        "loras": loras,
        "resolution": snapshot.get("resolution"),
    }


def _score(signature: dict[str, Any], ref: dict[str, Any]) -> int:
    if not signature["checkpoints"]:
        return 0
    ckpt_hit = len(signature["checkpoints"] & ref["checkpoints"])
    if ckpt_hit == 0:
        return 0
    lora_hit = len(signature["loras"] & ref["loras"])
    res_hit = (
        1
        if signature["resolution"]
        and signature["resolution"] == ref["resolution"]
        else 0
    )
    return ckpt_hit * 3 + lora_hit * 2 + res_hit


def _match_workflow(
    signature: dict[str, Any],
    references: list[tuple[str, dict[str, Any]]],
) -> str:
    """Return the closest matching workflow id, or ``""`` when no confident hit.

    A checkpoint file must overlap for any match; LoRA overlap and matching
    resolution raise the score. Ties resolve to the first listed workflow.
    """
    best_id = ""
    best_score = 0
    for workflow_id, report in references:
        ref = _signature(report)
        score = _score(signature, ref)
        if score > best_score:
            best_score = score
            best_id = workflow_id
    return best_id if best_score >= 3 else ""


def sync_from_history(
    connection: sqlite3.Connection,
    history_payload: dict[str, Any],
    workflow_reports: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Insert missing history rows from a raw ComfyUI ``/history`` payload.

    ``workflow_reports`` pairs ``(workflow_id, analysis report)`` for the
    locally stored workflows used for ownership matching. Existing local rows
    are left untouched; only absent prompt ids are backfilled.
    """
    references = list(workflow_reports)
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT prompt_id FROM generation_history"
        ).fetchall()
    }

    added = 0
    skipped = 0
    matched = 0
    unmatched = 0
    added_items: list[dict[str, Any]] = []

    for prompt_id, entry in history_payload.items():
        if not isinstance(prompt_id, str) or not isinstance(entry, dict):
            continue
        if prompt_id in existing:
            skipped += 1
            continue
        prompt_api = _prompt_api(entry)
        if not prompt_api:
            skipped += 1
            continue

        report = analyze_workflow(prompt_api)
        positive, negative = _prompts_from_report(report)
        snapshot = generation_history.extract_model_snapshot(report)
        signature = _signature(report)
        workflow_id = _match_workflow(signature, references)
        if workflow_id:
            matched += 1
        else:
            unmatched += 1
        status, error = _status(entry)
        images = generation_history.extract_images(entry.get("outputs"))

        connection.execute(
            """
            INSERT OR IGNORE INTO generation_history
                (prompt_id, workflow_id, created_at, positive_prompt,
                 negative_prompt, model_snapshot, task_status, images,
                 last_error, draft_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                prompt_id,
                workflow_id,
                _created_at(entry),
                positive or None,
                negative or None,
                json.dumps(snapshot, ensure_ascii=False),
                status,
                json.dumps(images, ensure_ascii=False) if images else None,
                error,
            ),
        )
        added += 1
        added_items.append(
            {
                "prompt_id": prompt_id,
                "workflow_id": workflow_id,
                "status": status,
                "images": images,
            }
        )

    connection.commit()
    return {
        "added": added,
        "skipped": skipped,
        "matched": matched,
        "unmatched": unmatched,
        "items": added_items,
    }


def rebind_workflow(
    connection: sqlite3.Connection, prompt_id: str, workflow_id: str
) -> dict[str, Any] | None:
    """Reassign a history record to another workflow (or unbind it)."""
    record = generation_history.get_by_prompt_id(connection, prompt_id)
    if record is None:
        return None
    clean_id = str(workflow_id or "").strip()
    connection.execute(
        "UPDATE generation_history SET workflow_id = ? WHERE prompt_id = ?",
        (clean_id, prompt_id),
    )
    connection.commit()
    return generation_history.get_by_prompt_id(connection, prompt_id)
