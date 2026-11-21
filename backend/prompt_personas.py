"""Persona / genre / style template registry (file-based, local only).

This module complements :mod:`prompt_profiles`. It manages the selectable
identity and writing-layer templates under the profile root:

- ``personas/<kind>/<id>/system.txt`` -- selectable LLM identity / system
  prompt. The folder name is the persona id; the parent ``prompt`` / ``writing``
  folder determines the kind. A ``kind:`` line in the file header overrides the
  parent folder when present; a flat ``personas/<id>/system.txt`` layout is
  still accepted, with the kind parsed from the header (default ``writing``).
  The display name is parsed from a ``display_name:`` / ``name:`` header line.
  No central manifest is consulted: dropping a folder under ``personas/`` is
  enough for it to appear in the list.
- ``genres/*.txt`` -- writing genre settings (writing class, third slot).
- ``styles/*.txt`` -- writing style templates (writing class, fourth slot).

Scope and boundaries mirror :mod:`prompt_profiles`:

* Pure local file I/O; no network calls, no model weight access.
* Every caller-supplied relative path is resolved inside the profile root;
  absolute paths and ``..`` traversal are rejected.
* A corrupt or missing template is reported as an empty / flagged entry
  rather than raising.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import prompt_profiles

PERSONAS_DIR = "personas"
GENRES_DIR = "genres"
STYLES_DIR = "styles"
# Sentinel / explicit empty value: selecting "none" means no identity layer.
NONE_VALUE = "none"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_META_LINE_RE = re.compile(r"^\s*(display_name|name|kind)\s*[:：]\s*(.+?)\s*$")
_KIND_VALUES = {"prompt", "writing"}
# Top-level folders that group personas by kind in the nested layout.
_TYPE_DIRS = {"prompt", "writing"}
_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "personas": [], "genres": [], "styles": []}


def root_dir() -> Path:
    return prompt_profiles.root_dir()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _safe_resolve(rel: str | Path) -> Path | None:
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


def _is_none(value: str | None) -> bool:
    return (value or "").strip().lower() in ("", NONE_VALUE)


def _cache_key() -> tuple[Any, Any]:
    root = root_dir()
    personas_dir = root / PERSONAS_DIR
    persona_key: Any
    if personas_dir.is_dir():
        persona_key = tuple(
            (str(path.relative_to(personas_dir)), path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(personas_dir.rglob("*"))
            if path.is_file()
        )
    else:
        persona_key = None
    parts: list[Any] = []
    for sub in (GENRES_DIR, STYLES_DIR):
        folder = root / sub
        parts.append(
            (folder.stat().st_mtime_ns, folder.stat().st_size)
            if folder.is_dir()
            else None
        )
    return persona_key, tuple(parts)


def list_personas(force: bool = False) -> list[dict[str, Any]]:
    """Public projection of registered personas for the dynamic list API."""
    _ensure_cache(force)
    return list(_cache["personas"])


def get_persona(persona_id: str) -> dict[str, Any] | None:
    """Return one full persona entry plus its system text, or None."""
    _ensure_cache()
    for entry in _cache["personas"]:
        if entry.get("id") == persona_id:
            resolved = _safe_resolve(str(entry.get("system_file") or ""))
            system = ""
            if resolved and resolved.is_file():
                try:
                    system = _read_text(resolved).strip()
                except (OSError, UnicodeDecodeError):
                    system = ""
            result = dict(entry)
            result["system"] = system
            return result
    return None


def list_genres() -> list[dict[str, Any]]:
    _ensure_cache()
    return list(_cache["genres"])


def list_styles() -> list[dict[str, Any]]:
    _ensure_cache()
    return list(_cache["styles"])


def get_genre(genre_id: str) -> str:
    _ensure_cache()
    return _read_layer_text(GENRES_DIR, genre_id)


def get_style(style_id: str) -> str:
    _ensure_cache()
    return _read_layer_text(STYLES_DIR, style_id)


def _read_layer_text(directory: str, layer_id: str) -> str:
    layer_id = (layer_id or "").strip()
    if not layer_id or not _ID_RE.match(layer_id):
        return ""
    resolved = _safe_resolve(Path(directory) / f"{layer_id}.txt")
    if resolved and resolved.is_file():
        try:
            return _read_text(resolved).strip()
        except (OSError, UnicodeDecodeError):
            return ""
    return ""


def _scan_text_layer(directory: str) -> list[dict[str, Any]]:
    folder = root_dir() / directory
    entries: list[dict[str, Any]] = []
    if not folder.is_dir():
        return entries
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix != ".txt":
            continue
        stem = path.stem
        if stem.startswith("_") or stem.startswith("."):
            continue
        if not _ID_RE.match(stem):
            continue
        label = stem
        try:
            text = _read_text(path)
            label = _extract_label(text, stem)
        except (OSError, UnicodeDecodeError):
            text = ""
        entries.append(
            {
                "id": stem,
                "display_name": label,
                "exists": bool(text.strip()),
            }
        )
    return entries


def _extract_label(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        if line.startswith("##"):
            line = line.lstrip("#").strip()
        if "：" in line:
            line = line.split("：", 1)[1].strip()
        if line:
            return line[:40]
    return fallback


def _parse_persona_meta(text: str, fallback_id: str) -> tuple[str, str, bool]:
    """Pull ``kind`` and display name out of header ``key: value`` lines.

    Returns ``(kind, display, kind_explicit)``; ``kind_explicit`` is True only
    when the header carried a valid ``kind`` value, so the caller can tell a
    deliberate override apart from the default / folder-derived kind.
    """
    kind = "writing"
    kind_explicit = False
    display = ""
    for line in text.splitlines():
        match = _META_LINE_RE.match(line)
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2).strip()
        if key == "kind" and value.lower() in _KIND_VALUES:
            kind = value.lower()
            kind_explicit = True
        elif key in ("display_name", "name") and value:
            display = value[:40]
    if not display:
        display = _extract_label(text, fallback_id)
    return kind, display, kind_explicit


def _build_persona(persona_dir: Path, base_kind: str | None) -> dict[str, Any] | None:
    pid = persona_dir.name
    if pid.startswith("_") or pid.startswith(".") or not _ID_RE.match(pid):
        return None
    system_file = persona_dir / "system.txt"
    text = ""
    if system_file.is_file():
        try:
            text = _read_text(system_file)
        except (OSError, UnicodeDecodeError):
            text = ""
    header_kind, display_name, kind_explicit = _parse_persona_meta(text, pid)
    # Header kind wins as an explicit override; otherwise the parent type
    # folder; flat layouts fall back to the default writing kind.
    kind = header_kind if kind_explicit else (base_kind or "writing")
    if base_kind:
        rel = f"{PERSONAS_DIR}/{base_kind}/{pid}/system.txt"
    else:
        rel = f"{PERSONAS_DIR}/{pid}/system.txt"
    return {
        "id": pid,
        "display_name": display_name,
        "kind": kind,
        "system_file": rel,
        "description": "",
        "enabled": True,
        "exists": bool(text.strip()),
    }


def _scan_personas() -> list[dict[str, Any]]:
    folder = root_dir() / PERSONAS_DIR
    entries: list[dict[str, Any]] = []
    if not folder.is_dir():
        return entries
    for top in sorted(folder.iterdir()):
        if not top.is_dir():
            continue
        if top.name in _TYPE_DIRS:
            # Nested layout: personas/<kind>/<id>/system.txt
            for sub in sorted(top.iterdir()):
                if not sub.is_dir():
                    continue
                entry = _build_persona(sub, top.name)
                if entry:
                    entries.append(entry)
        else:
            # Legacy flat layout: personas/<id>/system.txt
            entry = _build_persona(top, None)
            if entry:
                entries.append(entry)
    return entries


def _ensure_cache(force: bool = False) -> None:
    key = _cache_key()
    with _lock:
        if not force and _cache["key"] == key and _cache["personas"]:
            return
            return
        _cache["key"] = key
        _cache["personas"] = _scan_personas()
        _cache["genres"] = _scan_text_layer(GENRES_DIR)
        _cache["styles"] = _scan_text_layer(STYLES_DIR)
