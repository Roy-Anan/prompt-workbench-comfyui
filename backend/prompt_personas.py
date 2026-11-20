"""Persona / genre / style template registry (file-based, local only).

This module complements :mod:`prompt_profiles`. It manages the selectable
identity and writing-layer templates under the profile root:

- ``personas/<id>/system.txt`` + ``personas/manifest.json`` -- selectable LLM
  identity / system prompt, tagged ``kind`` (``prompt`` / ``writing``).
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

import json
import re
import threading
from pathlib import Path
from typing import Any

import prompt_profiles

MANIFEST_NAME = "manifest.json"
PERSONAS_DIR = "personas"
GENRES_DIR = "genres"
STYLES_DIR = "styles"
# Sentinel / explicit empty value: selecting "none" means no identity layer.
NONE_VALUE = "none"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
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


def _cache_key() -> tuple[Any, Any, Any]:
    root = root_dir()
    parts: list[Any] = []
    for sub in (PERSONAS_DIR, GENRES_DIR, STYLES_DIR):
        folder = root / sub
        if folder.is_dir():
            stat = folder.stat()
            parts.append((stat.st_mtime_ns, stat.st_size))
        else:
            parts.append(None)
    manifest = root / PERSONAS_DIR / MANIFEST_NAME
    mkey = (
        manifest.stat().st_mtime_ns if manifest.is_file() else None,
        manifest.stat().st_size if manifest.is_file() else None,
    )
    return mkey, tuple(parts)


def _load_manifest() -> dict[str, Any]:
    manifest_path = root_dir() / PERSONAS_DIR / MANIFEST_NAME
    if not manifest_path.is_file():
        return {"personas": []}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"personas": [], "_error": "manifest 解析失败"}
    if not isinstance(data, dict) or not isinstance(data.get("personas"), list):
        return {"personas": [], "_error": "manifest 结构无效"}
    return data


def list_personas() -> list[dict[str, Any]]:
    """Public projection of registered personas for the dynamic list API."""
    _ensure_cache()
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


def _ensure_cache(force: bool = False) -> None:
    key = _cache_key()
    with _lock:
        if not force and _cache["key"] == key and _cache["personas"]:
            return
        manifest = _load_manifest()
        personas: list[dict[str, Any]] = []
        for raw in manifest.get("personas", []):
            if not isinstance(raw, dict):
                continue
            entry = dict(raw)
            system_file = str(entry.get("system_file") or "").strip()
            resolved = _safe_resolve(system_file) if system_file else None
            exists = bool(resolved and resolved.is_file())
            entry["exists"] = exists
            entry["enabled"] = bool(entry.get("enabled", True))
            entry.setdefault("kind", "writing")
            personas.append(entry)
        _cache["key"] = key
        _cache["personas"] = personas
        _cache["genres"] = _scan_text_layer(GENRES_DIR)
        _cache["styles"] = _scan_text_layer(STYLES_DIR)
