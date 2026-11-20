"""Local file-based Prompt Profile registry (model-specific rule profiles).

The candidate system prompt is composed in three logical layers:

  1. **Core System Prompt** -- model-agnostic assistant rules, read from
     ``core/core_system_prompt.txt``.
  2. **Model-specific Rules** -- one matched Profile's ``rules.txt``, chosen
     from the imported drawing checkpoint / architecture (Auto) or picked
     manually.
  3. **Existing Workflow Rules** -- the unchanged ``rule_engine`` output for the
     imported workflow; this module never replaces that layer.

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

MANIFEST_NAME = "manifest.json"
CORE_REL = Path("core") / "core_system_prompt.txt"
MODELS_DIR = "models"
STAGING_DIR = "staging"
ARCHIVE_DIR = "archive"

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


def load_manifest() -> dict[str, Any]:
    """Read ``manifest.json``.

    A missing file yields an empty manifest; a corrupt file yields an empty
    manifest with an ``_error`` note rather than raising.
    """
    root = root_dir()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return {"version": 1, "profiles": []}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"version": 1, "profiles": [], "_error": str(exc)}
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
        return {"version": 1, "profiles": [], "_error": "manifest 结构无效"}
    data.setdefault("version", 1)
    return data


def _save_manifest(manifest: dict[str, Any]) -> None:
    _write_json(root_dir() / MANIFEST_NAME, manifest)


def _entry_errors(entry: dict[str, Any], seen_ids: set[str]) -> list[str]:
    """Validate one manifest entry; return a list of human-readable problems."""
    errors: list[str] = []
    profile_id = str(entry.get("id") or "").strip()
    if not profile_id:
        errors.append("缺少 id")
        return errors
    if not _ID_RE.match(profile_id):
        errors.append(f"id 非法：{profile_id}（仅允许小写字母/数字/-/_）")
    if profile_id in seen_ids:
        errors.append(f"重复 Profile id：{profile_id}")

    rules_file = str(entry.get("rules_file") or "").strip()
    if not rules_file:
        errors.append("缺少 rules_file")
    else:
        resolved = _safe_resolve(rules_file)
        if resolved is None:
            errors.append(f"rules_file 路径越权：{rules_file}")
        elif not resolved.is_file():
            errors.append(f"规则文件不存在：{rules_file}")
        else:
            try:
                text = _read_text(resolved)
                if not text.strip():
                    errors.append("规则文件为空")
                else:
                    actual_hash = _sha256_text(text)
                    recorded = str(entry.get("content_hash") or "").strip()
                    if recorded and recorded != actual_hash:
                        errors.append("content_hash 与规则文件不一致")
            except (OSError, UnicodeDecodeError) as exc:
                errors.append(f"规则文件读取失败：{exc}")

    if not str(entry.get("architecture") or "").strip():
        errors.append("缺少 architecture")
    return errors


def _section_warnings(text: str) -> list[str]:
    return [section for section in REQUIRED_SECTIONS if f"[{section}]" not in text]


def scan_profiles(force: bool = False) -> list[dict[str, Any]]:
    """Scan the manifest and validate every registered Profile.

    Results are cached per manifest mtime/size; ``force`` bypasses the cache.
    Each returned entry carries computed ``errors``, ``exists``,
    ``content_hash`` and ``auto_eligible`` fields.
    """
    root = root_dir()
    manifest_path = root / MANIFEST_NAME
    key = (
        manifest_path.stat().st_mtime_ns if manifest_path.is_file() else None,
        manifest_path.stat().st_size if manifest_path.is_file() else None,
    )
    with _lock:
        if not force and _cache["manifest_key"] == key and _cache["entries"]:
            return list(_cache["entries"])

        manifest = load_manifest()
        seen_ids: set[str] = set()
        entries: list[dict[str, Any]] = []
        for raw in manifest.get("profiles", []):
            if not isinstance(raw, dict):
                continue
            entry = dict(raw)
            errors = _entry_errors(entry, seen_ids)
            profile_id = str(entry.get("id") or "").strip()
            if profile_id:
                seen_ids.add(profile_id)

            rules_file = str(entry.get("rules_file") or "").strip()
            resolved = _safe_resolve(rules_file) if rules_file else None
            exists = bool(resolved and resolved.is_file())
            actual_hash = ""
            warnings: list[str] = []
            if exists:
                try:
                    text = _read_text(resolved)
                    actual_hash = _sha256_text(text)
                    warnings = _section_warnings(text)
                except (OSError, UnicodeDecodeError):
                    exists = False

            entry["exists"] = exists
            entry["content_hash"] = actual_hash or entry.get("content_hash", "")
            entry["section_warnings"] = warnings
            entry["errors"] = errors
            entry["auto_eligible"] = bool(
                entry.get("enabled", True) and exists and not errors
            )
            entries.append(entry)

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
    resolved = _safe_resolve(Path(STAGING_DIR) / profile_id)
    return resolved


def _model_dir(profile_id: str) -> Path | None:
    resolved = _safe_resolve(Path(MODELS_DIR) / profile_id)
    return resolved


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
        meta = child / "profile.json"
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
    meta_path = staging_dir / "profile.json"
    if not meta_path.is_file():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def publish_from_staging(profile_id: str) -> dict[str, Any]:
    """Promote a staged Profile into ``models/`` after local validation.

    The previous published version is archived before being overwritten. The
    manifest entry is created or updated with the new content hash and
    timestamp. Raises ``FileNotFoundError`` / ``ValueError`` for callers to map
    to HTTP errors.
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

    manifest = load_manifest()
    existing = next(
        (e for e in manifest.get("profiles", []) if e.get("id") == profile_id),
        None,
    )
    meta = _read_staging_meta(staging)

    # Archive the currently published version before overwriting it.
    model_dir = _model_dir(profile_id)
    if model_dir and model_dir.is_dir():
        archive_root = _archive_dir(profile_id)
        assert archive_root is not None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        archive_slot = archive_root / stamp
        archive_slot.mkdir(parents=True, exist_ok=True)
        old_rules = model_dir / "rules.txt"
        if old_rules.is_file():
            shutil.copy2(old_rules, archive_slot / "rules.txt")
        _write_json(
            archive_slot / "meta.json",
            {
                "id": profile_id,
                "archived_at": _now_iso(),
                "version": (existing or {}).get("version", ""),
                "content_hash": (existing or {}).get("content_hash", ""),
            },
        )

    target_dir = _model_dir(profile_id)
    assert target_dir is not None
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rules_src, target_dir / "rules.txt")

    previous_version = str((existing or {}).get("version") or "0.0.0")
    new_version = str(meta.get("version") or _bump_patch(previous_version))
    entry = dict(existing or {})
    entry.update(
        {
            "id": profile_id,
            "display_name": meta.get("display_name") or entry.get("display_name") or profile_id,
            "description": meta.get("description") or entry.get("description", ""),
            "enabled": True,
            "architecture": meta.get("architecture") or entry.get("architecture") or "通用",
            "checkpoint_patterns": meta.get("checkpoint_patterns")
            or entry.get("checkpoint_patterns", []),
            "aliases": meta.get("aliases") or entry.get("aliases", []),
            "rules_file": f"{MODELS_DIR}/{profile_id}/rules.txt",
            "version": new_version,
            "source": meta.get("source") or entry.get("source", "manual"),
            "created_at": entry.get("created_at") or _now_iso(),
            "updated_at": _now_iso(),
            "content_hash": content_hash,
            "trigger_words": meta.get("trigger_words") or entry.get("trigger_words", []),
            "incompatible_architectures": meta.get("incompatible_architectures")
            or entry.get("incompatible_architectures", []),
            "priority": int(meta.get("priority") or entry.get("priority", 0)),
            "author": meta.get("author") or entry.get("author", ""),
            "research_status": meta.get("research_status") or entry.get("research_status", "legacy"),
        }
    )

    if existing is None:
        manifest.setdefault("profiles", []).append(entry)
    else:
        index = manifest["profiles"].index(existing)
        manifest["profiles"][index] = entry
    _save_manifest(manifest)

    # Clear the staged copy after a successful publish.
    shutil.rmtree(staging, ignore_errors=True)
    _cache["manifest_key"] = None

    return {
        "id": profile_id,
        "version": new_version,
        "content_hash": content_hash,
        "section_warnings": missing,
        "archived_previous": bool(model_dir and model_dir.is_dir()),
    }


def _bump_patch(version: str) -> str:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
    if not match:
        return "1.0.0"
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def set_enabled(profile_id: str, enabled: bool) -> dict[str, Any]:
    manifest = load_manifest()
    for entry in manifest.get("profiles", []):
        if entry.get("id") == profile_id:
            entry["enabled"] = bool(enabled)
            entry["updated_at"] = _now_iso()
            _save_manifest(manifest)
            _cache["manifest_key"] = None
            return {"id": profile_id, "enabled": bool(enabled)}
    raise FileNotFoundError(f"Profile 不存在：{profile_id}")


def list_versions(profile_id: str) -> list[dict[str, Any]]:
    archive_root = _archive_dir(profile_id)
    versions: list[dict[str, Any]] = []
    if archive_root and archive_root.is_dir():
        for slot in sorted(archive_root.iterdir(), reverse=True):
            if not slot.is_dir():
                continue
            meta_path = slot / "meta.json"
            meta: dict[str, Any] = {}
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    meta = {}
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
    if model_dir and model_dir.is_dir():
        archive_root = _archive_dir(profile_id)
        assert archive_root is not None
        backup_slot = archive_root / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        backup_slot.mkdir(parents=True, exist_ok=True)
        live_rules = model_dir / "rules.txt"
        if live_rules.is_file():
            shutil.copy2(live_rules, backup_slot / "rules.txt")

    target_dir = _model_dir(profile_id)
    assert target_dir is not None
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rules_src, target_dir / "rules.txt")
    rules_text = _read_text(rules_src)
    content_hash = _sha256_text(rules_text)

    meta_path = archive_slot / "meta.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            meta = {}

    manifest = load_manifest()
    for entry in manifest.get("profiles", []):
        if entry.get("id") == profile_id:
            entry["version"] = meta.get("version", entry.get("version", ""))
            entry["content_hash"] = content_hash
            entry["updated_at"] = _now_iso()
            _save_manifest(manifest)
            _cache["manifest_key"] = None
            return {"id": profile_id, "restored": stamp, "version": entry["version"]}
    raise FileNotFoundError(f"Profile 不存在：{profile_id}")


def delete_profile(profile_id: str, confirm: bool) -> dict[str, Any]:
    """Remove a Profile's model dir, archive entries and manifest entry.

    Refuses unless ``confirm`` is true. The Core prompt file is never a Profile
    and cannot be deleted through this function.
    """
    if not confirm:
        raise ValueError("删除 Profile 需要二次确认（confirm=true）")
    if not _ID_RE.match(profile_id):
        raise ValueError(f"非法 Profile id：{profile_id}")

    manifest = load_manifest()
    existing = next(
        (e for e in manifest.get("profiles", []) if e.get("id") == profile_id),
        None,
    )
    if existing is None:
        raise FileNotFoundError(f"Profile 不存在：{profile_id}")

    model_dir = _model_dir(profile_id)
    if model_dir and model_dir.is_dir():
        shutil.rmtree(model_dir, ignore_errors=True)
    archive_root = _archive_dir(profile_id)
    if archive_root and archive_root.is_dir():
        shutil.rmtree(archive_root, ignore_errors=True)

    manifest["profiles"] = [
        e for e in manifest.get("profiles", []) if e.get("id") != profile_id
    ]
    _save_manifest(manifest)
    _cache["manifest_key"] = None
    return {"id": profile_id, "deleted": True}
