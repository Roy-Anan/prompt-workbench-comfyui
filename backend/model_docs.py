"""Base-model (Checkpoint) documentation management.

One Checkpoint gets one documentation folder under
``prompt_profiles/models/<id>/`` holding ``rules.txt`` (the long profile / prompt
rules body) and ``meta.json`` (card fields). This module adds the missing write
path: create a card from a Checkpoint name, fill it from the bundled template,
edit it, and delete it.

Boundaries:

* Local files only; the ComfyUI model directory is never written to. Deleting a
  card removes the workbench documentation folder (and its archived versions /
  staging copies) — the weight file itself is untouched.
* Representative images are stored as an ``asset_id`` reference into the local
  asset library; no image bytes are copied here.
* All ids are validated and resolved inside the profile root.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import prompt_profiles

TEMPLATE_REL = Path("templates") / "MODEL_RULE_TEMPLATE.txt"
ARCH_RE = re.compile(r"^[A-Za-z0-9 ._+/-]{0,40}$")
_SLUG_STRIP = re.compile(r"[^a-z0-9_-]+")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def template_text() -> str:
    """Return the bundled model-profile template body (read-only)."""
    path = prompt_profiles.root_dir() / TEMPLATE_REL
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _slugify(seed: str, fallback: str = "model") -> str:
    """Derive a lowercase ASCII folder id from a model name.

    Chinese-heavy names leave the slug empty, in which case a short stable hash
    suffix keeps the id readable enough (``model-<hash6>``) while staying
    filesystem- and regex-safe.
    """
    base = _SLUG_STRIP.sub("-", seed.lower()).strip("-")
    base = re.sub(r"-{2,}", "-", base)
    base = base[:48].strip("-")
    if base and base[0].isalnum():
        return base
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6]
    return f"{fallback}-{digest}"


def _unique_id(seed: str) -> str:
    """Pick an unused ``models/<id>`` folder name, suffixing ``-2``, ``-3`` ..."""
    root = prompt_profiles.root_dir() / prompt_profiles.MODELS_DIR
    candidate = _slugify(seed)
    if not (root / candidate).exists():
        return candidate
    index = 2
    while (root / f"{candidate}-{index}").exists():
        index += 1
    return f"{candidate}-{index}"


def _fill_template(text: str, display_name: str, filename: str, architecture: str) -> str:
    """Fill the three identity fields of the template, leaving the rest as-is."""
    if not text:
        text = "[IDENTITY]\nDisplay Name:\nExact Checkpoint Filename:\n\n[MODEL_ARCHITECTURE]\nBase Architecture:\n"
    replacements = (
        ("Display Name:", display_name),
        ("Exact Checkpoint Filename:", filename),
        ("Base Architecture:", architecture),
    )
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        for marker, value in replacements:
            if stripped == marker:
                lines[index] = f"{marker} {value}"
                break
    return "\n".join(lines).rstrip() + "\n"


def _clean_patterns(raw: Any, filename: str) -> list[str]:
    patterns: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            text = str(item or "").strip()
            if text and text not in patterns:
                patterns.append(text)
    if filename and filename not in patterns:
        patterns.insert(0, filename)
    return patterns


def _normalize_meta(payload: dict[str, Any], fallback_name: str) -> dict[str, Any]:
    """Build a clean meta dict from a form payload; unknown keys are dropped."""
    display_name = str(payload.get("display_name") or "").strip() or fallback_name
    filename = str(payload.get("checkpoint_file") or "").strip()
    architecture = str(payload.get("architecture") or "").strip()[:40]
    tags = payload.get("tags")
    if isinstance(tags, list):
        clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
    elif isinstance(tags, str):
        clean_tags = [part.strip() for part in re.split(r"[,，、]", tags) if part.strip()]
    else:
        clean_tags = []

    cover = payload.get("cover_asset_id")
    cover_asset_id: int | None
    if isinstance(cover, int):
        cover_asset_id = cover
    elif isinstance(cover, str) and cover.strip().isdigit():
        cover_asset_id = int(cover.strip())
    else:
        cover_asset_id = None

    return {
        "display_name": display_name,
        "description": str(payload.get("description") or "").strip()[:500],
        "architecture": architecture,
        "checkpoint_patterns": _clean_patterns(payload.get("checkpoint_patterns"), filename),
        "aliases": payload.get("aliases") if isinstance(payload.get("aliases"), list) else [],
        "priority": int(payload.get("priority") or 0),
        "enabled": bool(payload.get("enabled", True)),
        "version": str(payload.get("version") or "1.0.0").strip(),
        "trigger_words": payload.get("trigger_words")
        if isinstance(payload.get("trigger_words"), list)
        else [],
        "incompatible_architectures": payload.get("incompatible_architectures")
        if isinstance(payload.get("incompatible_architectures"), list)
        else [],
        "author": str(payload.get("author") or "").strip()[:120],
        "research_status": str(payload.get("research_status") or "").strip()[:40],
        "source": str(payload.get("source") or "manual").strip()[:200],
        "strengths": str(payload.get("strengths") or "").strip()[:300],
        "tags": clean_tags[:12],
        "cover_asset_id": cover_asset_id,
        "model_page": str(payload.get("model_page") or "").strip()[:300],
        "trust_level": str(payload.get("trust_level") or "").strip()[:20],
    }


def create_model_doc(payload: dict[str, Any]) -> dict[str, Any]:
    """Create one Checkpoint documentation folder from a form payload."""
    filename = str(payload.get("checkpoint_file") or "").strip()
    display_name = str(payload.get("display_name") or "").strip()
    if not display_name and not filename:
        raise ValueError("至少需要填写显示名或 Checkpoint 文件名")

    seed = display_name or Path(filename).stem
    profile_id = _unique_id(seed)
    model_dir = prompt_profiles.root_dir() / prompt_profiles.MODELS_DIR / profile_id
    model_dir.mkdir(parents=True, exist_ok=False)

    rules_text = payload.get("rules_text")
    if not isinstance(rules_text, str) or not rules_text.strip():
        rules_text = _fill_template(
            template_text(),
            display_name or Path(filename).stem,
            filename,
            str(payload.get("architecture") or "").strip(),
        )

    meta = _normalize_meta(payload, display_name or Path(filename).stem)
    (model_dir / "rules.txt").write_text(rules_text, encoding="utf-8")
    prompt_profiles._write_meta(model_dir, meta)
    prompt_profiles._cache["manifest_key"] = None

    entry = prompt_profiles.get_profile(profile_id)
    if entry is None:
        raise ValueError("资料写入后读取失败")
    return entry


def update_model_doc(profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Update card metadata and, when supplied, the rules body."""
    if not _ID_RE.match(profile_id or ""):
        raise ValueError(f"非法资料 id：{profile_id}")
    model_dir = prompt_profiles.root_dir() / prompt_profiles.MODELS_DIR / profile_id
    if not model_dir.is_dir():
        raise FileNotFoundError(f"资料不存在：{profile_id}")

    current = prompt_profiles._read_meta(model_dir)
    merged = {**current, **_normalize_meta({**current, **payload}, profile_id)}
    prompt_profiles._write_meta(model_dir, merged)

    rules_text = payload.get("rules_text")
    if isinstance(rules_text, str) and rules_text.strip():
        (model_dir / "rules.txt").write_text(rules_text, encoding="utf-8")

    prompt_profiles._cache["manifest_key"] = None
    entry = prompt_profiles.get_profile(profile_id)
    if entry is None:
        raise ValueError("资料写入后读取失败")
    return entry


def delete_model_doc(profile_id: str) -> dict[str, Any]:
    """Delete the documentation folder plus its archived versions and staging copy."""
    return prompt_profiles.delete_profile(profile_id, confirm=True)


def set_enabled(profile_id: str, enabled: bool) -> dict[str, Any]:
    return prompt_profiles.set_enabled(profile_id, enabled)
