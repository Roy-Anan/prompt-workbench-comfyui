"""Persona / genre / style template registry (file-based, local only).

This module complements :mod:`prompt_profiles`. It manages the selectable
identity and writing-layer templates under the profile root:

- ``personas/<kind>/<name>.txt`` -- selectable LLM identity / system
  prompt. ``kind`` is the parent folder (``prompt`` / ``writing``); the file
  stem is both the persona id and the display name shown in the dropdown.
  Dropping a ``.txt`` under the matching type folder is enough for it to
  appear in the list; no central manifest or per-file header metadata.
- ``genres/<题材名>.txt`` -- writing genre settings (writing class, third slot).
- ``styles/<文风名>.txt`` -- writing style templates (writing class, fourth slot).
  For both layers the file stem is both the id and the dropdown display name;
  Chinese stems are allowed, and no per-file header metadata is parsed.

Scope and boundaries mirror :mod:`prompt_profiles`:

* Pure local file I/O; no network calls, no model weight access.
* Every caller-supplied relative path is resolved inside the profile root;
  absolute paths and ``..`` traversal are rejected.
* A corrupt or missing template is reported as an empty / flagged entry
  rather than raising.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import prompt_profiles

PERSONAS_DIR = "personas"
GENRES_DIR = "genres"
STYLES_DIR = "styles"
# Sentinel / explicit empty value: selecting "none" means no identity layer.
NONE_VALUE = "none"

_KIND_VALUES = {"prompt", "writing"}
_TYPE_DIRS = _KIND_VALUES
# Characters forbidden in a layer file stem (path / shell unsafe).
_PERSONA_INVALID_CHARS = '/\\:*?"<>|'
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


def _dir_key(folder: Path) -> Any:
    if folder.is_dir():
        return tuple(
            (str(path.relative_to(folder)), path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(folder.rglob("*"))
            if path.is_file()
        )
    return None


def _cache_key() -> tuple[Any, Any]:
    root = root_dir()
    return _dir_key(root / PERSONAS_DIR), (
        _dir_key(root / GENRES_DIR),
        _dir_key(root / STYLES_DIR),
    )


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


def list_genres(force: bool = False) -> list[dict[str, Any]]:
    _ensure_cache(force)
    return list(_cache["genres"])


def list_styles(force: bool = False) -> list[dict[str, Any]]:
    _ensure_cache(force)
    return list(_cache["styles"])


def get_genre(genre_id: str) -> str:
    _ensure_cache()
    return _read_layer_text(GENRES_DIR, genre_id)


def get_style(style_id: str) -> str:
    _ensure_cache()
    return _read_layer_text(STYLES_DIR, style_id)


def _read_layer_text(directory: str, layer_id: str) -> str:
    layer_id = (layer_id or "").strip()
    if not layer_id or not _valid_persona_stem(layer_id):
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
        if not _valid_persona_stem(stem):
            continue
        text = ""
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            text = ""
        entries.append(
            {
                "id": stem,
                "display_name": stem,
                "exists": bool(text.strip()),
            }
        )
    return entries


def _valid_persona_stem(stem: str) -> bool:
    if not stem or stem.startswith("_") or stem.startswith("."):
        return False
    return not any(ch in stem for ch in _PERSONA_INVALID_CHARS)


def _scan_personas() -> list[dict[str, Any]]:
    folder = root_dir() / PERSONAS_DIR
    entries: list[dict[str, Any]] = []
    if not folder.is_dir():
        return entries
    for kind_dir in sorted(folder.iterdir()):
        if not kind_dir.is_dir() or kind_dir.name not in _TYPE_DIRS:
            continue
        kind = kind_dir.name
        for path in sorted(kind_dir.iterdir()):
            if not path.is_file() or path.suffix != ".txt":
                continue
            stem = path.stem
            if not _valid_persona_stem(stem):
                continue
            text = ""
            try:
                text = _read_text(path)
            except (OSError, UnicodeDecodeError):
                text = ""
            entries.append(
                {
                    "id": stem,
                    "display_name": stem,
                    "kind": kind,
                    "system_file": f"{PERSONAS_DIR}/{kind}/{stem}.txt",
                    "description": "",
                    "enabled": True,
                    "exists": bool(text.strip()),
                }
            )
    return entries


def _ensure_cache(force: bool = False) -> None:
    key = _cache_key()
    with _lock:
        if not force and _cache["key"] == key and _cache["personas"]:
            return
        _cache["key"] = key
        _cache["personas"] = _scan_personas()
        _cache["genres"] = _scan_text_layer(GENRES_DIR)
        _cache["styles"] = _scan_text_layer(STYLES_DIR)
