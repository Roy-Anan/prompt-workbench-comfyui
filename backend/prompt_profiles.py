"""Local file-based Prompt Profile registry (model-specific rule profiles).

The candidate system prompt is composed in three logical layers:

  1. **Core System Prompt** -- model-agnostic assistant rules, read from
     ``core/core_system_prompt.txt``.
  2. **Model-specific Rules** -- one matched Profile's ``rules.txt``, chosen
     from the imported drawing checkpoint / architecture (Auto) or picked
     manually.
  3. **Existing Workflow Rules** -- the unchanged ``rule_engine`` output for the
     imported workflow; this module never replaces that layer.

Storage layout (directory traversal, no central manifest):

  ``models/<id>/rules.txt``   required rule body; header ``key: value`` lines
                              provide display name / architecture / checkpoint
                              filename as fallbacks.
  ``models/<id>/meta.json``   optional structured override (patterns, aliases,
                              priority, enabled, version, ...).
  ``staging/<id>/``           unpublished drafts awaiting publish.
  ``archive/<id>/<stamp>/``   archived previous versions.

Scope and boundaries
---------------------
* Pure local file I/O under the profile root. No network calls, no model
  weight access, no remote identity research.
* Profile files and any hash are supplied manually; this module does not
  compute checkpoint hashes or query external model databases.
* Every caller-supplied relative path is resolved and confined to the profile
  root; absolute paths, ``..`` traversal and symlink escape are rejected.
* A corrupt or malformed Profile is reported with an ``error`` field instead
  of raising, so one bad note cannot take the backend down.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import architecture_normalize

META_NAME = "meta.json"
STAGING_META_NAME = "profile.json"
CORE_REL = Path("core") / "core_system_prompt.txt"
MODELS_DIR = "models"
STAGING_DIR = "staging"
ARCHIVE_DIR = "archive"

# Structured fields persisted in a Profile's optional ``meta.json``. Derived
# values (rules path, content hash, timestamps) are computed on read and are
# not stored here.
META_FIELDS = (
    "display_name",
    "description",
    "architecture",
    "checkpoint_patterns",
    "aliases",
    "priority",
    "enabled",
    "version",
    "trigger_words",
    "incompatible_architectures",
    "author",
    "research_status",
    "source",
)

# Sections a rules file is expected to carry. Missing sections are recorded as
# warnings, not fatal errors: a partially researched profile stays visible so
# its gaps can be reviewed.
REQUIRED_SECTIONS = (
    "IDENTITY",
    "MODEL_ARCHITECTURE",
    "PROMPT_SYNTAX",
    "PROMPT_ORDER",
    "NEGATIVE_PROMPT",
    "CONFIDENCE",
    "SOURCES",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_AUTO = "auto"

# Header keys recognised inside ``rules.txt`` when ``meta.json`` omits a value.
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "display_name": ("display name", "名称", "display_name"),
    "architecture": ("base architecture", "模型架构", "architecture"),
    "checkpoint_file": ("exact checkpoint filename", "checkpoint file", "文件名"),
}
_BRACKET_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
_KV_RE = re.compile(r"^([^:：]+)[:：]\s*(.*)$")

_lock = threading.Lock()
_cache: dict[str, Any] = {"manifest_key": None, "entries": []}


def root_dir() -> Path:
    """Resolve the profile root.

    Defaults to ``<repo>/prompt_profiles``; the ``WORKBENCH_PROFILE_ROOT`` env
    var overrides it so tests can point at a temporary directory.
    """
    override = os.environ.get("WORKBENCH_PROFILE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "prompt_profiles"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_resolve(rel: str | Path) -> Path | None:
    """Resolve ``rel`` inside the profile root; return None on escape attempt.

    Absolute paths and any ``..`` that leaves the root are rejected. The
    resolved path must still be a descendant of the root after normalization.
    """
    root = root_dir().resolve()
    candidate = Path(rel)
    if candidate.is_absolute():
        return None
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _read_meta(model_dir: Path) -> dict[str, Any]:
    return _read_json_file(model_dir / META_NAME)


def _write_meta(model_dir: Path, meta: dict[str, Any]) -> None:
    clean = {k: v for k, v in meta.items() if k in META_FIELDS}
    _write_json(model_dir / META_NAME, clean)


def _parse_rules_header(text: str) -> dict[str, str]:
    """Pull display name / architecture / checkpoint file from ``key: value``
    header lines. Bracket markers such as ``[已核实]`` are stripped from the
    value; unknown lines are ignored."""
    parsed: dict[str, str] = {"display_name": "", "architecture": "", "checkpoint_file": ""}
    for line in text.splitlines():
        match = _KV_RE.match(line.strip())
        if not match:
            continue
        key = match.group(1).strip().lower()
        value = _BRACKET_PREFIX.sub("", match.group(2).strip()).strip()
        for field, aliases in _HEADER_ALIASES.items():
            if key in aliases:
                parsed[field] = value
    return parsed


def _section_warnings(text: str) -> list[str]:
    return [section for section in REQUIRED_SECTIONS if f"[{section}]" not in text]


def _build_entry(profile_id: str, model_dir: Path, seen_ids: set[str]) -> dict[str, Any]:
    """Assemble one Profile entry from its ``rules.txt`` + optional ``meta.json``."""
    rules_path = model_dir / "rules.txt"
    exists = rules_path.is_file()
    text = ""
    content_hash = ""
    warnings: list[str] = []
    if exists:
        try:
            text = _read_text(rules_path)
            if text.strip():
                content_hash = _sha256_text(text)
                warnings = _section_warnings(text)
            else:
                exists = False
        except (OSError, UnicodeDecodeError):
            exists = False

    meta = _read_meta(model_dir)
    header = _parse_rules_header(text) if text else {}

    checkpoint_file = header.get("checkpoint_file", "")
    patterns = meta.get("checkpoint_patterns")
    if not isinstance(patterns, list):
        patterns = []
    patterns = [str(p) for p in patterns if str(p).strip()]
    if not patterns and checkpoint_file:
        patterns = [checkpoint_file]

    aliases = meta.get("aliases")
    if not isinstance(aliases, list):
        aliases = []
    aliases = [str(a) for a in aliases if str(a).strip()]

    trigger_words = meta.get("trigger_words")
    if not isinstance(trigger_words, list):
        trigger_words = []
    incompatible = meta.get("incompatible_architectures")
    if not isinstance(incompatible, list):
        incompatible = []

    entry: dict[str, Any] = {
        "id": profile_id,
        "display_name": str(meta.get("display_name") or header.get("display_name") or profile_id),
        "description": str(meta.get("description") or ""),
        "architecture": str(meta.get("architecture") or header.get("architecture") or ""),
        "checkpoint_patterns": patterns,
        "aliases": aliases,
        "rules_file": f"{MODELS_DIR}/{profile_id}/rules.txt",
        "version": str(meta.get("version") or ""),
        "source": str(meta.get("source") or "manual"),
        "enabled": bool(meta.get("enabled", True)),
        "priority": int(meta.get("priority") or 0),
        "author": str(meta.get("author") or ""),
        "research_status": str(meta.get("research_status") or ""),
        "trigger_words": trigger_words,
        "incompatible_architectures": incompatible,
        "exists": exists,
        "content_hash": content_hash,
        "section_warnings": warnings,
    }

    errors: list[str] = []
    if profile_id in seen_ids:
        errors.append(f"重复 Profile id：{profile_id}")
    seen_ids.add(profile_id)
    if not exists:
        errors.append("规则文件缺失或为空：rules.txt")
    if not entry["architecture"]:
        errors.append("缺少 architecture")
    entry["errors"] = errors
    entry["auto_eligible"] = bool(entry["enabled"] and exists and not errors)
    return entry


def scan_profiles(force: bool = False) -> list[dict[str, Any]]:
    """Scan ``models/`` and validate every Profile directory.

    Results are cached per model directory mtime/size; ``force`` bypasses the
    cache. Each returned entry carries computed ``errors``, ``exists``,
    ``content_hash`` and ``auto_eligible`` fields.
    """
    root = root_dir()
    models_dir = root / MODELS_DIR
    key: Any
    if models_dir.is_dir():
        key = tuple(
            (sub.name, sub.stat().st_mtime_ns, sub.stat().st_size)
            for sub in sorted(models_dir.iterdir())
            if sub.is_dir()
        )
    else:
        key = None

    with _lock:
        if not force and _cache["manifest_key"] == key and _cache["entries"]:
            return list(_cache["entries"])

        entries: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        if models_dir.is_dir():
            for sub in sorted(models_dir.iterdir()):
                if not sub.is_dir():
                    continue
                pid = sub.name
                if pid.startswith("_") or pid.startswith(".") or not _ID_RE.match(pid):
                    continue
                entries.append(_build_entry(pid, sub, seen_ids))

        _cache["manifest_key"] = key
        _cache["entries"] = entries
        return list(entries)


def list_profiles() -> list[dict[str, Any]]:
    """Public projection for the dynamic Profile list API."""
    projection: list[dict[str, Any]] = []
    for entry in scan_profiles():
        projection.append(
            {
                "id": entry.get("id"),
                "display_name": entry.get("display_name") or entry.get("id"),
                "description": entry.get("description", ""),
                "architecture": entry.get("architecture"),
                "enabled": bool(entry.get("enabled", True)),
                "version": entry.get("version", ""),
                "auto_eligible": entry.get("auto_eligible", False),
                "errors": entry.get("errors", []),
                "section_warnings": entry.get("section_warnings", []),
                "priority": entry.get("priority", 0),
                "aliases": entry.get("aliases", []),
                "checkpoint_patterns": entry.get("checkpoint_patterns", []),
                "research_status": entry.get("research_status", ""),
            }
        )
    return projection


def get_profile(profile_id: str) -> dict[str, Any] | None:
    """Return one full entry plus its rules text, or None when absent."""
    for entry in scan_profiles():
        if entry.get("id") == profile_id:
            resolved = _safe_resolve(str(entry.get("rules_file") or ""))
            rules = ""
            if resolved and resolved.is_file():
                try:
                    rules = _read_text(resolved)
                except (OSError, UnicodeDecodeError):
                    rules = ""
            result = dict(entry)
            result["rules"] = rules
            return result
    return None


def core_prompt_text() -> str:
    """Read the Core System Prompt; empty string when the file is missing."""
    resolved = _safe_resolve(str(CORE_REL))
    if resolved and resolved.is_file():
        try:
            return _read_text(resolved).strip()
        except (OSError, UnicodeDecodeError):
            return ""
    return ""


def _arch_family(label: str | None) -> str:
    return architecture_normalize.normalize(label).get("canonical") or ""


def _arch_compatible(profile_arch: str | None, workflow_label: str | None) -> bool:
    """Conservative architecture compatibility check.

    Same canonical family via :mod:`architecture_normalize` counts as
    compatible; a direct raw-label substring match also counts. Unknown sides
    are treated as incompatible rather than guessed.
    """
    if not profile_arch or not workflow_label:
        return False
    if architecture_normalize.same_family(profile_arch, workflow_label):
        return True
    return profile_arch.strip().lower() in workflow_label.strip().lower()


def _checkpoint_matches(
    entry: dict[str, Any], checkpoint: str | None
) -> tuple[str | None, int]:
    """Score one Profile against a checkpoint filename.

    Returns ``(match_source, score)``; score 0 means no checkpoint match.
    """
    if not checkpoint:
        return None, 0
    name = checkpoint.strip().lower()
    patterns = [str(p).lower() for p in entry.get("checkpoint_patterns", []) if p]
    aliases = [str(a).lower() for a in entry.get("aliases", []) if a]

    for pattern in patterns:
        if pattern == name:
            return "exact", 5
    for pattern in patterns:
        if pattern and (pattern in name or name in pattern):
            return "pattern", 4
    for alias in aliases:
        if alias and alias in name:
            return "alias", 3
    return None, 0


def match_profile(
    checkpoint: str | None,
    architecture_label: str | None,
    architecture_family: str | None,
    manual: str | None = None,
) -> dict[str, Any]:
    """Choose a Profile for one workflow.

    ``manual`` (non-empty, non-``auto``) wins when it resolves to an enabled
    Profile; an architecture mismatch is surfaced as a warning but does not
    silently drop the manual choice. With ``auto`` the chain is exact name ->
    filename pattern -> alias -> architecture -> generic fallback. Ties break
    by priority then stable id; selection is never random.

    Result keys: ``profile_id``, ``display_name``, ``profile`` (entry or
    None), ``match_source``, ``fallback_reason``, ``warning``,
    ``architecture``, ``checkpoint``.
    """
    entries = [e for e in scan_profiles() if e.get("enabled", True)]
    manual_id = (manual or "").strip()

    if manual_id and manual_id != _AUTO:
        chosen = next((e for e in entries if e["id"] == manual_id), None)
        if chosen is None:
            known = next((e for e in scan_profiles() if e["id"] == manual_id), None)
            warning = "手动选择的 Profile 不存在" if known is None else "手动选择的 Profile 已停用"
            return _fallback(checkpoint, architecture_label, warning=warning)
        compatible = _arch_compatible(chosen.get("architecture"), architecture_label)
        warning = None if compatible else "手动选择的 Profile 与工作流架构可能不兼容"
        return {
            "profile_id": chosen["id"],
            "display_name": chosen.get("display_name") or chosen["id"],
            "profile": chosen,
            "match_source": "manual",
            "fallback_reason": None,
            "warning": warning,
            "architecture": chosen.get("architecture"),
            "checkpoint": checkpoint,
        }

    candidates: list[tuple[int, int, str, dict[str, Any], str | None]] = []
    for entry in entries:
        if not entry.get("auto_eligible", False):
            continue
        source, score = _checkpoint_matches(entry, checkpoint)
        if score == 0:
            if _arch_compatible(entry.get("architecture"), architecture_label):
                source, score = "architecture", 2
            else:
                continue
        candidates.append(
            (score, int(entry.get("priority", 0)), str(entry["id"]), entry, source)
        )

    if not candidates:
        return _fallback(
            checkpoint,
            architecture_label,
            fallback_reason="未匹配到可用 Profile，使用通用规则回退",
        )

    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    score, _priority, _id, chosen, source = candidates[0]
    return {
        "profile_id": chosen["id"],
        "display_name": chosen.get("display_name") or chosen["id"],
        "profile": chosen,
        "match_source": source,
        "fallback_reason": None,
        "warning": None,
        "architecture": chosen.get("architecture"),
        "checkpoint": checkpoint,
    }


def _fallback(
    checkpoint: str | None,
    architecture_label: str | None,
    fallback_reason: str | None = None,
    warning: str | None = None,
) -> dict[str, Any]:
    return {
        "profile_id": None,
        "display_name": None,
        "profile": None,
        "match_source": "fallback",
        "fallback_reason": fallback_reason,
        "warning": warning,
        "architecture": architecture_label,
        "checkpoint": checkpoint,
    }


def compose_system_prompt(
    core_text: str,
    profile_rules: str | None,
    workflow_rules_text: str,
) -> str:
    """Assemble the three-layer system prompt in Core -> Model -> Workflow order."""
    layers: list[str] = []
    if core_text.strip():
        layers.append("【第一层 · 通用核心规则】\n" + core_text.strip())
    if profile_rules and profile_rules.strip():
        layers.append("【第二层 · 模型专用规则】\n" + profile_rules.strip())
    if workflow_rules_text.strip():
        layers.append("【第三层 · 工作流动态规则】\n" + workflow_rules_text.strip())
    return "\n\n".join(layers)


# ---- Staging, publishing, versions and rollback (local file operations) ----


def _staging_dir(profile_id: str) -> Path | None:
    return _safe_resolve(Path(STAGING_DIR) / profile_id)


def _model_dir(profile_id: str) -> Path | None:
    return _safe_resolve(Path(MODELS_DIR) / profile_id)


def _archive_dir(profile_id: str) -> Path | None:
    return _safe_resolve(Path(ARCHIVE_DIR) / profile_id)


def list_staging() -> list[dict[str, Any]]:
    """List directories waiting in ``staging/`` with a quick validity note."""
    root = root_dir()
    staging = root / STAGING_DIR
    items: list[dict[str, Any]] = []
    if not staging.is_dir():
        return items
    for child in sorted(staging.iterdir()):
        if not child.is_dir():
            continue
        rules = child / "rules.txt"
        meta = child / STAGING_META_NAME
        entry: dict[str, Any] = {
            "id": child.name,
            "has_rules": rules.is_file(),
            "has_meta": meta.is_file(),
            "errors": [],
        }
        if not rules.is_file():
            entry["errors"].append("缺少 rules.txt")
        else:
            try:
                text = _read_text(rules)
                if not text.strip():
                    entry["errors"].append("规则文件为空")
                else:
                    entry["content_hash"] = _sha256_text(text)
                    entry["section_warnings"] = _section_warnings(text)
            except (OSError, UnicodeDecodeError) as exc:
                entry["errors"].append(f"读取失败：{exc}")
        items.append(entry)
    return items


def _read_staging_meta(staging_dir: Path) -> dict[str, Any]:
    return _read_json_file(staging_dir / STAGING_META_NAME)


def publish_from_staging(profile_id: str) -> dict[str, Any]:
    """Promote a staged Profile into ``models/`` after local validation.

    The previous published version is archived before being overwritten. The
    target ``meta.json`` merges the existing live meta with the staging meta;
    the version is taken from staging or auto patch-bumped. Raises
    ``FileNotFoundError`` / ``ValueError`` for callers to map to HTTP errors.
    """
    profile_id = profile_id.strip()
    if not _ID_RE.match(profile_id):
        raise ValueError(f"非法 Profile id：{profile_id}")

    staging = _staging_dir(profile_id)
    if staging is None or not staging.is_dir():
        raise FileNotFoundError(f"staging 中不存在 Profile：{profile_id}")
    rules_src = staging / "rules.txt"
    if not rules_src.is_file():
        raise ValueError("staging 规则文件 rules.txt 缺失，无法发布")

    rules_text = _read_text(rules_src)
    if not rules_text.strip():
        raise ValueError("规则文件为空，无法发布")
    missing = _section_warnings(rules_text)
    content_hash = _sha256_text(rules_text)

    model_dir = _model_dir(profile_id)
    assert model_dir is not None
    existing_meta = _read_meta(model_dir) if model_dir.is_dir() else {}
    staging_meta = _read_staging_meta(staging)
    had_live = model_dir.is_dir() and (model_dir / "rules.txt").is_file()

    # Archive the currently published version before overwriting it.
    if had_live:
        archive_root = _archive_dir(profile_id)
        assert archive_root is not None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        archive_slot = archive_root / stamp
        archive_slot.mkdir(parents=True, exist_ok=True)
        old_rules = model_dir / "rules.txt"
        old_hash = ""
        if old_rules.is_file():
            shutil.copy2(old_rules, archive_slot / "rules.txt")
            try:
                old_hash = _sha256_text(_read_text(old_rules))
            except (OSError, UnicodeDecodeError):
                old_hash = ""
        _write_json(
            archive_slot / "meta.json",
            {
                "id": profile_id,
                "archived_at": _now_iso(),
                "version": existing_meta.get("version", ""),
                "content_hash": old_hash,
            },
        )

    model_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rules_src, model_dir / "rules.txt")

    previous_version = str(existing_meta.get("version") or "0.0.0")
    new_version = str(staging_meta.get("version") or _bump_patch(previous_version))
    merged = dict(existing_meta)
    for field in META_FIELDS:
        if field in staging_meta:
            merged[field] = staging_meta[field]
    merged["version"] = new_version
    merged["enabled"] = True
    _write_meta(model_dir, merged)

    # Clear the staged copy after a successful publish.
    shutil.rmtree(staging, ignore_errors=True)
    _cache["manifest_key"] = None

    return {
        "id": profile_id,
        "version": new_version,
        "content_hash": content_hash,
        "section_warnings": missing,
        "archived_previous": had_live,
    }


def _bump_patch(version: str) -> str:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
    if not match:
        return "1.0.0"
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def set_enabled(profile_id: str, enabled: bool) -> dict[str, Any]:
    model_dir = _model_dir(profile_id)
    if model_dir is None or not model_dir.is_dir():
        raise FileNotFoundError(f"Profile 不存在：{profile_id}")
    meta = _read_meta(model_dir)
    meta["enabled"] = bool(enabled)
    _write_meta(model_dir, meta)
    _cache["manifest_key"] = None
    return {"id": profile_id, "enabled": bool(enabled)}


def list_versions(profile_id: str) -> list[dict[str, Any]]:
    archive_root = _archive_dir(profile_id)
    versions: list[dict[str, Any]] = []
    if archive_root and archive_root.is_dir():
        for slot in sorted(archive_root.iterdir(), reverse=True):
            if not slot.is_dir():
                continue
            meta = _read_json_file(slot / "meta.json")
            versions.append(
                {
                    "stamp": slot.name,
                    "version": meta.get("version", ""),
                    "content_hash": meta.get("content_hash", ""),
                    "archived_at": meta.get("archived_at", ""),
                }
            )
    return versions


def rollback(profile_id: str, stamp: str) -> dict[str, Any]:
    """Restore one archived version over the current published Profile."""
    if not _ID_RE.match(profile_id):
        raise ValueError(f"非法 Profile id：{profile_id}")
    archive_slot = _safe_resolve(Path(ARCHIVE_DIR) / profile_id / stamp)
    if archive_slot is None or not archive_slot.is_dir():
        raise FileNotFoundError(f"历史版本不存在：{profile_id}@{stamp}")
    rules_src = archive_slot / "rules.txt"
    if not rules_src.is_file():
        raise ValueError("归档版本缺少 rules.txt，无法回滚")

    # Back up the live version before restoring.
    model_dir = _model_dir(profile_id)
    assert model_dir is not None
    if model_dir.is_dir():
        archive_root = _archive_dir(profile_id)
        assert archive_root is not None
        backup_slot = archive_root / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        backup_slot.mkdir(parents=True, exist_ok=True)
        live_rules = model_dir / "rules.txt"
        if live_rules.is_file():
            shutil.copy2(live_rules, backup_slot / "rules.txt")

    model_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rules_src, model_dir / "rules.txt")
    rules_text = _read_text(rules_src)
    content_hash = _sha256_text(rules_text)

    archived_meta = _read_json_file(archive_slot / "meta.json")
    # Restore the archived meta where present; keep other live meta fields.
    live_meta = _read_meta(model_dir)
    for field in META_FIELDS:
        if field in archived_meta:
            live_meta[field] = archived_meta[field]
    live_meta["version"] = archived_meta.get("version", live_meta.get("version", ""))
    _write_meta(model_dir, live_meta)
    _cache["manifest_key"] = None
    return {"id": profile_id, "restored": stamp, "version": live_meta.get("version", "")}


def delete_profile(profile_id: str, confirm: bool) -> dict[str, Any]:
    """Remove a Profile's model dir, archive entries and staging copy.

    Refuses unless ``confirm`` is true. The Core prompt file is never a Profile
    and cannot be deleted through this function.
    """
    if not confirm:
        raise ValueError("删除 Profile 需要二次确认（confirm=true）")
    if not _ID_RE.match(profile_id):
        raise ValueError(f"非法 Profile id：{profile_id}")

    model_dir = _model_dir(profile_id)
    exists = bool(model_dir and model_dir.is_dir())
    if not exists:
        raise FileNotFoundError(f"Profile 不存在：{profile_id}")

    shutil.rmtree(model_dir, ignore_errors=True)
    archive_root = _archive_dir(profile_id)
    if archive_root and archive_root.is_dir():
        shutil.rmtree(archive_root, ignore_errors=True)
    staging_dir = _staging_dir(profile_id)
    if staging_dir and staging_dir.is_dir():
        shutil.rmtree(staging_dir, ignore_errors=True)

    _cache["manifest_key"] = None
    return {"id": profile_id, "deleted": True}
