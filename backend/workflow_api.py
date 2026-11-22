from __future__ import annotations
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field, field_validator
import comfy_client
import datadir
import generation_history
import llm_client
import model_identity
import prompt_profiles
import prompt_personas
import rule_compare
import rule_prompt
import rule_store
import runtime_log
from workflow_service import WorkflowError, WorkflowStore
router = APIRouter(prefix="/api/workflows", tags=["workflows"])
system_router = APIRouter(prefix="/api/system", tags=["system"])

# Local 8B-class models may cold-start and prefill a long rule context well
# beyond the 30s client default; give the candidate round a generous ceiling.
CANDIDATE_LLM_TIMEOUT = 180.0
def store() -> WorkflowStore:
    return WorkflowStore(datadir.default_data_root() / "workflows")
def _database_path() -> Path:
    return datadir.database_path(datadir.default_data_root())
def _sampler_actuals(report: dict[str, Any]) -> dict[str, Any]:
    """Merge the first sampler group's scalar params and latent size for comparison.

    Multi-group workflows are reported conservatively: the top-level actuals
    keep the first group's params, while every group's params are listed under
    ``sampler_groups``. Later groups are never merged into the first group's
    verdict; a per-group comparison is a later slice.
    """
    actuals: dict[str, Any] = {}
    sampler_groups: list[dict[str, Any]] = []
    for group in report.get("prompt_groups", []):
        params = group.get("params")
        if not isinstance(params, dict):
            params = {}
        sampler_groups.append(
            {"sampler_node_id": group.get("sampler_node_id"), "params": params}
        )
        if params and not actuals:
            actuals.update(params)
    actuals["sampler_group_count"] = len(sampler_groups)
    actuals["sampler_groups"] = sampler_groups
    latent = report.get("latent")
    if isinstance(latent, dict):
        if latent.get("width") is not None:
            actuals["width"] = latent["width"]
        if latent.get("height") is not None:
            actuals["height"] = latent["height"]
    clip_skip = report.get("clip_skip")
    if isinstance(clip_skip, dict) and clip_skip.get("clip_skip") is not None:
        actuals["clip_skip"] = clip_skip["clip_skip"]

    latent_nodes = report.get("latent_nodes")
    if isinstance(latent_nodes, list):
        actuals["latent_node_count"] = len(latent_nodes)
        actuals["latent_nodes"] = [
            {
                "node_id": node.get("node_id"),
                "width": node.get("width"),
                "height": node.get("height"),
            }
            for node in latent_nodes
            if isinstance(node, dict)
        ]
    clip_nodes = report.get("clip_skip_nodes")
    if isinstance(clip_nodes, list):
        actuals["clip_skip_node_count"] = len(clip_nodes)
        actuals["clip_skip_nodes"] = [
            {
                "node_id": node.get("node_id"),
                "clip_skip": node.get("clip_skip"),
            }
            for node in clip_nodes
            if isinstance(node, dict)
        ]
    return actuals


def _checkpoint_filename(report: dict[str, Any]) -> str | None:
    """Extract the first checkpoint file name recognized in the workflow."""
    for node in report.get("model_nodes", []):
        inputs = node.get("inputs", {})
        for key in ("ckpt_name", "unet_name"):
            value = inputs.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None
def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, WorkflowError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail="工作流处理失败") from exc
class WorkflowImport(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    workflow: dict[str, Any]
class PromptNodeConfirmation(BaseModel):
    positive_node_ids: list[str] = Field(default_factory=list)
    negative_node_ids: list[str] = Field(default_factory=list)
class PromptWrite(BaseModel):
    positive_text: str | None = None
    negative_text: str | None = None


class PromptCandidateRequest(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    task: str | None = Field(default=None, max_length=2000)
    mode: str = "draft"
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    num_ctx: int | None = Field(default=None, ge=1024, le=131_072)
    locked_fragments: list[str] | None = Field(default=None)
    prompt_profile: str | None = Field(default=None, max_length=120)
    # Selectable identity / system prompt. None (absent) keeps the legacy
    # Core fallback for old callers; "" / "none" means bare model (no identity
    # layer); any other value resolves to a registered persona.
    persona: str | None = Field(default=None, max_length=120)
    # Writing-class layers; ignored for prompt-class identities.
    genre: str | None = Field(default=None, max_length=120)
    style: str | None = Field(default=None, max_length=120)
    # Explicit existing prompts passed from the node / panel; take precedence
    # over the working copy when present.
    positive_in: str | None = Field(default=None, max_length=2000)
    negative_in: str | None = Field(default=None, max_length=2000)

    @field_validator("locked_fragments")
    @classmethod
    def _locked_fragments_known(cls, value: Any) -> list[str]:
        return _clean_locked_fragments(value)

    @field_validator("model")
    @classmethod
    def _model_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must not be blank")
        return value.strip()

    @field_validator("mode")
    @classmethod
    def _mode_known(cls, value: str) -> str:
        normalized = prompt_personas.resolve_mode(value)
        if normalized and not prompt_personas.get_mode(normalized):
            raise ValueError(f"unknown mode: {normalized}")
        return normalized


class TagRename(BaseModel):
    old_tag: str = Field(min_length=1, max_length=30)
    new_tag: str = Field(min_length=1, max_length=30)


class DraftSave(BaseModel):
    draft_id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    positive: str | None = None
    negative: str | None = None
    note: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list)
    source_history_id: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("草稿名称不能为空")
        return value.strip()


def _ollama_url() -> str:
    """Read the configured local Ollama URL, falling back to the default."""
    root = datadir.default_data_root()
    settings = datadir.default_settings(root)
    loaded = datadir.load_settings_file(root)
    if isinstance(loaded, dict):
        connections = loaded.get("connections")
        if isinstance(connections, dict) and isinstance(
            connections.get("ollama_url"), str
        ):
            return connections["ollama_url"].rstrip("/")
    return settings["connections"]["ollama_url"].rstrip("/")


def _comfyui_url() -> str:
    """Read the configured local ComfyUI URL, falling back to the default."""
    root = datadir.default_data_root()
    settings = datadir.default_settings(root)
    loaded = datadir.load_settings_file(root)
    if isinstance(loaded, dict):
        connections = loaded.get("connections")
        if isinstance(connections, dict) and isinstance(
            connections.get("comfyui_url"), str
        ):
            return connections["comfyui_url"].rstrip("/")
    return settings["connections"]["comfyui_url"].rstrip("/")


def _protected_terms() -> list[str]:
    """Read the local protected-term list from settings (fail-open to empty)."""
    root = datadir.default_data_root()
    loaded = datadir.load_settings_file(root)
    if isinstance(loaded, dict):
        prompt = loaded.get("prompt")
        if isinstance(prompt, dict):
            terms = prompt.get("protected_terms")
            if isinstance(terms, list):
                return [
                    str(term).strip()
                    for term in terms
                    if isinstance(term, str) and term.strip()
                ]
    return []


MAX_LOCKED_FRAGMENTS = 30
MAX_LOCKED_FRAGMENT_CHARS = 60


def _clean_locked_fragments(raw: Any) -> list[str]:
    """Whitelist-normalize request-level locked fragments.

    Only plain strings are accepted; blank/duplicate entries are dropped and the
    list is capped. Anything else raises ``ValueError`` rather than being
    silently ignored. Locked fragments are a per-request hint; they are not
    persisted to the working copy or drafts.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("locked_fragments 必须是列表")
    fragments: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("locked_fragments 条目必须是字符串")
        fragment = item.strip()
        if not fragment or fragment in seen:
            continue
        seen.add(fragment)
        fragments.append(fragment[:MAX_LOCKED_FRAGMENT_CHARS])
        if len(fragments) >= MAX_LOCKED_FRAGMENTS:
            break
    return fragments


_FENCE_RE = re.compile(r"^\s*```[^\n`]*\n?(.*?)\n?```\s*$", re.DOTALL)


def _clean_candidate_content(content: str) -> str:
    """Conservatively strip an outer markdown code fence from a candidate.

    Only the outermost fence wrapping the whole reply is removed; inner lines,
    labels and wording are left untouched. Nothing is rewritten or invented.
    """
    text = content.strip()
    match = _FENCE_RE.match(text)
    if match:
        text = match.group(1).strip()
    return text


def _dedupe_prompt_tags(text: str) -> str:
    """Deduplicate comma-separated tags within each line, keeping the first
    occurrence and its original wording. Comparison is case-insensitive;
    prose and line breaks are otherwise left untouched. This guards against a
    local model drifting into repeating the same tag (e.g. ``no lens flare``)
    dozens of times.
    """
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        seen: set[str] = set()
        kept: list[str] = []
        for part in line.split(","):
            tag = part.strip()
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            kept.append(tag)
        cleaned_lines.append(", ".join(kept))
    return "\n".join(cleaned_lines)


def _unregistered_context() -> dict[str, Any]:
    return {
        "status": "unregistered",
        "checkpoint": None,
        "architecture": None,
        "architecture_family": None,
        "architecture_label": None,
        "checkpoint_rules": [],
        "architecture_rules": [],
        "global_rules": [],
    }


def _rules_system_message(rule_payload: dict[str, Any]) -> str:
    lines: list[str] = [rule_payload.get("instruction", "")]
    fields = rule_payload.get("fields", [])
    if fields:
        lines.append("已解析规则：")
        for entry in fields:
            lines.append(
                f"- {entry.get('key')}: {entry.get('value')}（{entry.get('status')}）"
            )
    if rule_payload.get("conflicts"):
        lines.append("存在 conflict 字段，需人工核对，不要替模型选择。")
    if rule_payload.get("unknown"):
        lines.append("存在 unknown 字段，不要补默认值或猜测。")
    return "\n".join(line for line in lines if line)


def _user_message(
    working_prompts: list[dict[str, Any]],
    task: str | None,
    mode: str = "draft",
    protected_terms: list[str] | None = None,
    locked_fragments: list[str] | None = None,
    positive_in: str | None = None,
    negative_in: str | None = None,
) -> str:
    by_role: dict[str, list[str]] = {"positive": [], "negative": []}
    for item in working_prompts:
        role = item.get("role")
        text = item.get("text")
        if role in by_role and isinstance(text, str) and text.strip():
            by_role[role].append(text.strip())
    if positive_in and positive_in.strip():
        by_role["positive"].append(positive_in.strip())
    if negative_in and negative_in.strip():
        by_role["negative"].append(negative_in.strip())

    lines: list[str] = []
    mode_entry = prompt_personas.get_mode(mode) if mode else None
    # 空白直出模式不携带现有 Prompt，只按规则与任务要求从零生成。
    include_existing = not (mode_entry and mode_entry.get("skip_existing"))
    if include_existing and (by_role["positive"] or by_role["negative"]):
        lines.append("当前工作流现有 Prompt：")
        if by_role["positive"]:
            lines.append("正向：" + " / ".join(by_role["positive"]))
        if by_role["negative"]:
            lines.append("负向：" + " / ".join(by_role["negative"]))
    if task and task.strip():
        lines.append(f"任务要求：{task.strip()}")
    if protected_terms:
        lines.append(
            "以下术语必须保留原写法，不得翻译、改写、删除或替换："
            + "、".join(protected_terms)
        )
    if locked_fragments:
        lines.append(
            "以下片段必须原样保留，不得改写、删除、翻译或替换："
            + "、".join(locked_fragments)
        )
    if mode_entry and mode_entry.get("instruction"):
        lines.append(mode_entry["instruction"])
    return "\n".join(lines)
@router.get("")
def list_workflows():
    return {"items": store().list_workflows()}
@router.post("/tags/rename")
def rename_draft_tag(payload: TagRename):
    """Rename one draft tag across every workflow draft box (read-write)."""
    try:
        return store().rename_tag(payload.old_tag, payload.new_tag)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.post("/import")
def import_workflow(payload: WorkflowImport):
    try:
        return store().import_workflow(payload.filename, payload.workflow)
    except (WorkflowError, OSError) as exc:
        _raise_api_error(exc)
@router.get("/{workflow_id}")
def get_workflow(workflow_id: str):
    try:
        return store().get_workflow(workflow_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.get("/{workflow_id}/versions")
def list_versions(workflow_id: str):
    """Read-only list of saved pre-write snapshots, newest first.

    Each entry exposes only the whitelist Prompt texts captured at save time;
    no restore action is offered here.
    """
    try:
        return {"items": store().list_versions(workflow_id)}
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.post("/{workflow_id}/versions/{stamp}/restore")
def restore_version(workflow_id: str, stamp: str):
    """Restore whitelist Prompt texts from a saved version snapshot.

    Reuses the guarded write path; the current working copy is first saved as
    a new version snapshot, so restoring an old version produces a new version.
    """
    try:
        return store().restore_version(workflow_id, stamp)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


def _normalize_prompt_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.lower().split())


def _attach_draft_images(
    connection: sqlite3.Connection,
    workflow_id: str,
    drafts: list[dict[str, Any]],
    output_dir: str,
) -> None:
    """Attach output images to each draft, mutating the draft records in place.

    Records submitted against a draft link directly by ``draft_id``. Older
    history rows without a draft link fall back to a normalized positive-prompt
    match; negative prompts are not used for the fallback because they are often
    generic or empty.
    """
    history = generation_history.list_history(connection, workflow_id=workflow_id)
    by_draft: dict[str, list[dict[str, Any]]] = {}
    text_fallback: list[dict[str, Any]] = []
    for record in history:
        images = generation_history.annotate_exists(
            record.get("images") or [], output_dir
        )
        if not images:
            continue
        slim = [
            {
                "filename": img.get("filename"),
                "subfolder": img.get("subfolder") or "",
                "exists": img.get("exists"),
                "prompt_id": record.get("prompt_id"),
            }
            for img in images
        ]
        draft_ref = str(record.get("draft_id") or "").strip()
        if draft_ref:
            by_draft.setdefault(draft_ref, []).extend(slim)
        else:
            text_fallback.append(
                {
                    "positive": _normalize_prompt_text(
                        record.get("positive_prompt")
                    ),
                    "images": slim,
                }
            )

    for draft in drafts:
        linked = list(by_draft.get(draft["id"], []))
        if not linked:
            draft_pos = _normalize_prompt_text(draft.get("positive"))
            if draft_pos:
                for entry in text_fallback:
                    hist_pos = entry["positive"]
                    if hist_pos and (
                        hist_pos == draft_pos
                        or draft_pos in hist_pos
                        or hist_pos in draft_pos
                    ):
                        linked.extend(entry["images"])
        draft["images"] = linked


@router.get("/{workflow_id}/drafts")
def list_drafts(workflow_id: str, include_trashed: bool = False):
    """List the named drafts saved for this workflow, newest update first.

    Each draft carries its linked output images (direct draft-id link, with a
    prompt-text fallback for pre-existing history rows).
    """
    try:
        drafts = store().list_drafts(workflow_id, include_trashed)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)

    root = datadir.default_data_root()
    settings = datadir.load_settings_file(root) or {}
    output_dir = (settings.get("storage") or {}).get("output", "")
    with model_identity.connect(_database_path()) as connection:
        generation_history.ensure_schema(connection)
        _attach_draft_images(connection, workflow_id, drafts, output_dir)
    return {"items": drafts}


@router.post("/{workflow_id}/drafts")
def save_draft(workflow_id: str, payload: DraftSave):
    """Create or update a named draft. Does not touch the working copy."""
    try:
        return store().save_draft(
            workflow_id,
            payload.draft_id,
            payload.name,
            payload.positive,
            payload.negative,
            payload.note,
            payload.tags,
            payload.source_history_id,
        )
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.get("/{workflow_id}/drafts/{draft_id}")
def get_draft(workflow_id: str, draft_id: str):
    """Read one named draft in full."""
    try:
        return store().get_draft(workflow_id, draft_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.delete("/{workflow_id}/drafts/{draft_id}")
def delete_draft(workflow_id: str, draft_id: str):
    """Soft-delete one named draft into the recycle bin. History and images untouched."""
    try:
        return store().delete_draft(workflow_id, draft_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.post("/{workflow_id}/drafts/{draft_id}/restore")
def restore_draft(workflow_id: str, draft_id: str):
    """Recover a soft-deleted draft back into the active draft box."""
    try:
        return store().restore_draft(workflow_id, draft_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.post("/{workflow_id}/drafts/{draft_id}/purge")
def purge_draft(workflow_id: str, draft_id: str):
    """Permanently remove a draft from the recycle bin. Irreversible."""
    try:
        return store().purge_draft(workflow_id, draft_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.post("/{workflow_id}/drafts/{draft_id}/favorite")
def toggle_draft_favored(workflow_id: str, draft_id: str):
    """Flip the favored marker on one draft. Does not reorder or touch the working copy."""
    try:
        return store().toggle_favored(workflow_id, draft_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.post("/{workflow_id}/drafts/{draft_id}/pin")
def toggle_draft_pinned(workflow_id: str, draft_id: str):
    """Flip the pinned marker on one draft. Pinned drafts sort first; working copy untouched."""
    try:
        return store().toggle_pinned(workflow_id, draft_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.post("/{workflow_id}/confirm")
def confirm_prompt_nodes(
    workflow_id: str,
    payload: PromptNodeConfirmation,
):
    try:
        return store().confirm_prompt_nodes(
            workflow_id,
            payload.positive_node_ids,
            payload.negative_node_ids,
        )
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)
@router.post("/{workflow_id}/write")
def write_prompts(workflow_id: str, payload: PromptWrite):
    try:
        return store().write_prompts(
            workflow_id,
            payload.positive_text,
            payload.negative_text,
        )
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.get("/{workflow_id}/export")
def export_workflow(workflow_id: str):
    """Return the working API-format workflow for download to the canvas.

    The workbench no longer submits prompts itself; this hands the edited
    workflow over so the user can drag it into the ComfyUI canvas and queue it
    there. Only the API-format dict and a suggested file name are returned.
    """
    try:
        workflow = store().get_workflow(workflow_id)
        working = store().read_working(workflow_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)
    manifest = workflow.get("manifest", {})
    filename = str(manifest.get("filename") or workflow_id)
    stem = Path(filename).stem or workflow_id
    return {"filename": f"{stem}.json", "workflow": working}


@router.post("/{workflow_id}/reset")
def reset_workflow_copy(workflow_id: str):
    """Clear prompt text written to the working copy, restore the original."""
    try:
        return store().reset_working_prompts(workflow_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)


@router.get("/{workflow_id}/rules")
def workflow_rule_context(workflow_id: str):
    """Return only the rules relevant to this workflow's checkpoint context.

    The recognized checkpoint filename is matched exactly against stored
    checkpoint rules; architecture and global rules are attached for programmatic
    inheritance. Unrelated stored rules are never returned. A workflow with no
    recognized checkpoint, or a checkpoint with no stored rule, is reported
    ``unregistered`` with an empty rule set; the engine never fabricates a rule.
    """
    try:
        workflow = store().get_workflow(workflow_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)

    report = workflow.get("report", {})
    checkpoint = _checkpoint_filename(report)
    if not checkpoint:
        return {
            "status": "unregistered",
            "reason": "no_checkpoint_recognized",
            "checkpoint": None,
            "architecture": None,
            "architecture_family": None,
            "architecture_label": None,
            "checkpoint_rules": [],
            "architecture_rules": [],
            "global_rules": [],
        }

    with model_identity.connect(_database_path()) as connection:
        rule_store.ensure_schema(connection)
        context = rule_store.context_for_checkpoint(connection, checkpoint)
    return context


@router.get("/{workflow_id}/rules/prompt")
def workflow_rule_prompt(workflow_id: str):
    """Assemble the trimmed, LLM-ready rule prompt payload for this workflow.

    Resolves the R17 context through the engine's programmatic inheritance and
    keeps the winning source, provenance and confidence per field. Conflicts
    stay marked ``conflict`` and uncovered fields ``unknown``; the downstream
    model is never asked to guess a value or inheritance priority. Unregistered
    workflows return an empty payload with a no-guidance instruction.
    """
    try:
        workflow = store().get_workflow(workflow_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)

    report = workflow.get("report", {})
    checkpoint = _checkpoint_filename(report)
    if not checkpoint:
        return rule_prompt.build_prompt_context(
            {
                "status": "unregistered",
                "checkpoint": None,
                "architecture": None,
                "architecture_family": None,
                "architecture_label": None,
                "checkpoint_rules": [],
                "architecture_rules": [],
                "global_rules": [],
            }
        )

    with model_identity.connect(_database_path()) as connection:
        rule_store.ensure_schema(connection)
        context = rule_store.context_for_checkpoint(connection, checkpoint)
    return rule_prompt.build_prompt_context(context)


def _resolve_identity(payload: PromptCandidateRequest) -> tuple[str, dict[str, Any] | None, str]:
    """Resolve the identity (system prompt) layer.

    Returns ``(identity_text, persona_meta, persona_kind)``.

    - ``persona`` absent (None): legacy fallback to the bundled Core generic
      rules, treated as prompt class (keeps old callers working).
    - ``""`` / ``"none"``: bare model, no identity layer injected.
    - other value: resolve a persona txt under ``personas/<kind>/``.
    """
    raw = payload.persona
    if raw is None:
        return prompt_profiles.core_prompt_text(), None, "prompt"
    cleaned = raw.strip()
    if cleaned == "" or cleaned.lower() == "none":
        return "", None, "generic"
    entry = prompt_personas.get_persona(cleaned)
    if entry is None:
        return "", {"id": cleaned, "found": False}, "generic"
    meta = {
        "id": entry["id"],
        "display_name": entry.get("display_name") or entry["id"],
        "kind": entry.get("kind", "prompt"),
    }
    return entry.get("system", "") or "", meta, entry.get("kind", "prompt")


def _generate_candidate(
    workflow_id: str | None, payload: PromptCandidateRequest
) -> dict[str, Any]:
    """Assemble layers, call the local LLM and return one candidate.

    The candidate is returned to the caller but never written back to the
    workflow or draft. A local-model failure returns a conservative 502 with
    no fabricated candidate; there is no cloud fallback.
    """
    workflow: dict[str, Any] | None = None
    checkpoint: str | None = None
    context = _unregistered_context()
    rule_payload = rule_prompt.build_prompt_context(context)
    working_prompts: list[dict[str, Any]] = []

    if workflow_id and workflow_id.strip():
        try:
            workflow = store().get_workflow(workflow_id.strip())
        except (WorkflowError, FileNotFoundError, OSError) as exc:
            _raise_api_error(exc)
        report = workflow.get("report", {})
        checkpoint = _checkpoint_filename(report)
        if checkpoint:
            with model_identity.connect(_database_path()) as connection:
                rule_store.ensure_schema(connection)
                context = rule_store.context_for_checkpoint(connection, checkpoint)
        rule_payload = rule_prompt.build_prompt_context(context)
        working_prompts = workflow.get("working_prompts", [])

    identity_text, persona_meta, persona_kind = _resolve_identity(payload)

    # Profile / model-specific rules: prompt class only. Three states:
    # empty / "none" = skip, "auto" = match by checkpoint/architecture,
    # other value = explicit profile id.
    profile_choice: dict[str, Any] | None = None
    profile_rules = ""
    profile_meta: dict[str, Any] | None = None
    profile_raw = (payload.prompt_profile or "auto").strip()
    skip_profile = (
        persona_kind == "writing"
        or profile_raw == ""
        or profile_raw.lower() == "none"
    )
    if not skip_profile:
        manual = None if profile_raw == "auto" else profile_raw
        profile_choice = prompt_profiles.match_profile(
            checkpoint,
            context.get("architecture_label"),
            context.get("architecture_family"),
            manual=manual,
        )
        if profile_choice["profile_id"]:
            full = prompt_profiles.get_profile(profile_choice["profile_id"])
            if full:
                profile_rules = full.get("rules", "") or ""
                profile_meta = {
                    "id": full.get("id"),
                    "display_name": full.get("display_name"),
                    "version": full.get("version"),
                    "content_hash": full.get("content_hash"),
                    "architecture": full.get("architecture"),
                }

    # Writing-class layers (genre / style); harmless when empty.
    genre_text = prompt_personas.get_genre(payload.genre) if payload.genre else ""
    style_text = prompt_personas.get_style(payload.style) if payload.style else ""
    # Normalize a legacy slug to the canonical Chinese stem so history records
    # and the response carry the registry id, not the old English alias.
    canonical_mode = prompt_personas.resolve_mode(payload.mode)
    mode_entry = prompt_personas.get_mode(canonical_mode) if canonical_mode else None

    workflow_rules_text = _rules_system_message(rule_payload) if workflow_id else ""

    system_content = prompt_profiles.compose_system_prompt(
        identity_text, profile_rules, workflow_rules_text
    )
    extra_layers: list[str] = []
    if genre_text.strip():
        extra_layers.append("【题材设定】\n" + genre_text.strip())
    if style_text.strip():
        extra_layers.append("【文风】\n" + style_text.strip())
    if extra_layers:
        system_content = "\n\n".join(
            [part for part in (system_content, "\n\n".join(extra_layers)) if part.strip()]
        )

    messages: list[dict[str, str]] = []
    if system_content.strip():
        messages.append({"role": "system", "content": system_content})
    messages.append(
        {
            "role": "user",
            "content": _user_message(
                working_prompts,
                payload.task,
                canonical_mode,
                _protected_terms(),
                payload.locked_fragments,
                payload.positive_in,
                payload.negative_in,
            ),
        }
    )

    queue = comfy_client.queue_has_active_task(_comfyui_url())
    if queue["active"]:
        raise HTTPException(
            status_code=409,
            detail={
                "online": True,
                "error": "ComfyUI 队列有待执行或运行中任务，请完成后再使用本地 LLM。",
            },
        )
    options: dict[str, Any] = {}
    if payload.temperature is not None:
        options["temperature"] = payload.temperature
    if payload.top_p is not None:
        options["top_p"] = payload.top_p
    if payload.num_ctx is not None:
        options["num_ctx"] = payload.num_ctx
    result = llm_client.chat(
        _ollama_url(),
        payload.model,
        messages,
        options=options or None,
        timeout=CANDIDATE_LLM_TIMEOUT,
    )
    if not result["ok"]:
        runtime_log.logger().error(
            "候选生成 LLM 调用失败 model=%s: %s",
            payload.model,
            result["error"],
        )
        raise HTTPException(
            status_code=502,
            detail={"online": False, "error": result["error"]},
        )

    cleaned = _clean_candidate_content(result["content"])
    cleaned = _dedupe_prompt_tags(cleaned)
    if not cleaned:
        runtime_log.logger().warning(
            "候选生成模型返回空内容 model=%s", payload.model
        )
        raise HTTPException(
            status_code=502,
            detail={"online": True, "error": "模型返回内容为空，未生成候选。"},
        )

    return {
        "model": result["model"],
        "mode": canonical_mode,
        "mode_negative_output": bool(mode_entry and mode_entry.get("negative_output")),
        "candidates": [cleaned],
        "rule_status": rule_payload["status"],
        "conflicts": rule_payload["conflicts"],
        "unknown": rule_payload["unknown"],
        "persona_id": persona_meta["id"] if persona_meta else None,
        "persona_name": persona_meta.get("display_name") if persona_meta else None,
        "persona_kind": persona_kind,
        "genre": payload.genre if genre_text.strip() else None,
        "style": payload.style if style_text.strip() else None,
        "selected_profile_id": profile_choice["profile_id"] if profile_choice else None,
        "selected_profile_name": profile_choice["display_name"] if profile_choice else None,
        "profile_match_source": profile_choice["match_source"] if profile_choice else None,
        "profile_version": profile_meta["version"] if profile_meta else None,
        "profile_content_hash": profile_meta["content_hash"] if profile_meta else None,
        "checkpoint_name": checkpoint,
        "architecture": context.get("architecture_label"),
        "fallback_reason": profile_choice["fallback_reason"] if profile_choice else None,
        "profile_warning": profile_choice["warning"] if profile_choice else None,
        "system_prompt_layers": {
            "core": bool(identity_text.strip()),
            "profile": bool(profile_rules.strip()),
            "workflow": bool(workflow_rules_text.strip()),
            "genre": bool(genre_text.strip()),
            "style": bool(style_text.strip()),
        },
    }


@router.post("/prompt/candidates")
def prompt_candidates_standalone(payload: PromptCandidateRequest):
    """Candidate generation without a bound workflow (bare model / writing)."""
    return _generate_candidate(None, payload)


@router.post("/{workflow_id}/prompt/candidates")
def prompt_candidates(workflow_id: str, payload: PromptCandidateRequest):
    """Ask the local Ollama model for a prompt candidate for this workflow."""
    return _generate_candidate(workflow_id, payload)



@system_router.post("/release-vram")
def release_vram():
    """Manually unload resident Ollama models and free ComfyUI model VRAM.

    Best-effort: returns per-target results instead of raising on a single
    unreachable target.
    """
    return comfy_client.release_resources(_ollama_url(), _comfyui_url())


@router.get("/{workflow_id}/rules/compare")
def workflow_rule_compare(workflow_id: str):
    """Compare the resolved rule payload against this workflow's sampler actuals.

    Only facts readable on both sides get a verdict; non-numeric actuals,
    non-structured rules and unrelated fields stay ``unknown`` rather than being
    guessed. Unregistered workflows return an empty comparison set.
    """
    try:
        workflow = store().get_workflow(workflow_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        _raise_api_error(exc)

    report = workflow.get("report", {})
    checkpoint = _checkpoint_filename(report)
    actuals = _sampler_actuals(report)
    if not checkpoint:
        payload = rule_prompt.build_prompt_context(
            {
                "status": "unregistered",
                "checkpoint": None,
                "architecture": None,
                "architecture_family": None,
                "architecture_label": None,
                "checkpoint_rules": [],
                "architecture_rules": [],
                "global_rules": [],
            }
        )
        return rule_compare.compare_workflow(payload, actuals)

    with model_identity.connect(_database_path()) as connection:
        rule_store.ensure_schema(connection)
        context = rule_store.context_for_checkpoint(connection, checkpoint)
    payload = rule_prompt.build_prompt_context(context)
    return rule_compare.compare_workflow(payload, actuals)
