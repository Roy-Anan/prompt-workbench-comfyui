"""Injection-text management for the local LLM layers.

The workbench feeds the local LLM four optional text layers plus a core rule
document, all kept as flat ``.txt`` files under the profile root:

==========  =======================================  ==========================
layer       folder                                    id source
==========  =======================================  ==========================
persona      ``personas/prompt/<名称>.txt``             file stem
persona      ``personas/writing/<名称>.txt``            file stem
genre        ``genres/<名称>.txt``                      file stem
style        ``styles/<名称>.txt``                      file stem
mode         ``modes/<名称>.txt``                       file stem
core         ``core/core_system_prompt.txt``           fixed
==========  =======================================  ==========================

This module adds the missing CRUD path (create / read / update / delete / hide
toggle) on top of the existing scanners in :mod:`prompt_personas` and
:mod:`prompt_profiles`. Writes stay inside the profile root, use a temp file
plus replace, and reject absolute paths or ``..`` traversal.

Boundaries: injection text is untrusted data. Editing it never grants the
ability to change workflows, submit renders or touch the operating system;
program-level constraints (prompt whitelist, unknown-is-unknown) stay enforced
by the backend regardless of file content.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import prompt_personas
import prompt_profiles

MAX_NAME_CHARS = 60
MAX_BODY_CHARS = 200_000
INVALID_STEM_CHARS = '/\\:*?"<>|'

# Public layer keys -> (folder relative to the profile root, kind folder or "").
LAYERS: dict[str, tuple[str, str]] = {
    "prompt_persona": ("personas", "prompt"),
    "writing_persona": ("personas", "writing"),
    "genre": ("genres", ""),
    "style": ("styles", ""),
    "mode": ("modes", ""),
}


def _layer_folder(layer: str, kind: str) -> Path | None:
    """Resolve and create-check the folder for one layer, inside the root."""
    root = prompt_profiles.root_dir().resolve()
    candidate = root / layer
    if kind:
        candidate = candidate / kind
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _valid_stem(name: str) -> bool:
    return bool(name) and not name.startswith(("_", ".")) and not any(
        ch in name for ch in INVALID_STEM_CHARS
    )


def _stem_of(path: Path) -> str:
    """Hidden files are stored as ``___<name>.txt``; the display stem drops it."""
    stem = path.stem
    if stem.startswith("__"):
        return stem[2:]
    return stem


def _hidden_name(name: str) -> str:
    return f"__{name}"


def _list_layer(layer: str) -> list[dict[str, Any]]:
    spec = LAYERS.get(layer)
    if spec is None:
        raise ValueError(f"未知文本层：{layer}")
    folder = _layer_folder(spec[0], spec[1])
    items: list[dict[str, Any]] = []
    if folder is None or not folder.is_dir():
        return items
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix != ".txt":
            continue
        raw_stem = path.stem
        if raw_stem.startswith("."):
            continue
        hidden = raw_stem.startswith("__")
        name = _stem_of(path)
        if not _valid_stem(name):
            continue
        body = ""
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            body = ""
        items.append(
            {
                "layer": layer,
                "id": name,
                "name": name,
                "hidden": hidden,
                "exists": bool(body.strip()),
                "chars": len(body.strip()),
                "preview": body.strip().replace("\n", " ")[:80],
            }
        )
    return items


def list_layers() -> dict[str, Any]:
    """List every managed injection layer plus the core document state."""
    groups = {layer: _list_layer(layer) for layer in LAYERS}
    core_path = prompt_profiles.root_dir() / prompt_profiles.CORE_REL
    core = {"exists": core_path.is_file(), "chars": 0}
    if core_path.is_file():
        try:
            core["chars"] = len(core_path.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError):
            core["chars"] = 0
    return {"layers": groups, "core": core}


def read_text(layer: str, name: str) -> dict[str, Any]:
    """Read one text layer body (or the core document when ``layer == "core"``)."""
    if layer == "core":
        path = prompt_profiles.root_dir() / prompt_profiles.CORE_REL
        if not path.is_file():
            raise FileNotFoundError("核心规则文件不存在")
        return {
            "layer": "core",
            "id": "core",
            "name": "核心系统规则",
            "text": path.read_text(encoding="utf-8"),
        }

    spec = LAYERS.get(layer)
    if spec is None:
        raise ValueError(f"未知文本层：{layer}")
    folder = _layer_folder(spec[0], spec[1])
    if folder is None:
        raise ValueError("非法文本层路径")

    for candidate in (folder / f"{name}.txt", folder / f"{_hidden_name(name)}.txt"):
        resolved = candidate.resolve()
        try:
            resolved.relative_to(prompt_profiles.root_dir().resolve())
        except ValueError:
            raise ValueError("路径越界") from None
        if resolved.is_file():
            return {
                "layer": layer,
                "id": name,
                "name": name,
                "text": resolved.read_text(encoding="utf-8"),
                "hidden": resolved.stem.startswith("__"),
            }
    raise FileNotFoundError(f"文本不存在：{layer}/{name}")


def _check_body(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("正文必须是字符串")
    if len(text) > MAX_BODY_CHARS:
        raise ValueError("正文超过大小上限")
    return text


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def create_text(layer: str, name: str, text: str) -> dict[str, Any]:
    """Create a new layer file; refuses to overwrite an existing name."""
    name = str(name or "").strip()[:MAX_NAME_CHARS]
    if not _valid_stem(name):
        raise ValueError("名称不合法：不能为空、不能以 _ 或 . 开头、不能含路径字符")
    spec = LAYERS.get(layer)
    if spec is None:
        raise ValueError(f"未知文本层：{layer}")
    folder = _layer_folder(spec[0], spec[1])
    if folder is None:
        raise ValueError("非法文本层路径")
    path = folder / f"{name}.txt"
    if path.exists():
        raise ValueError(f"同名文本已存在：{name}")
    _write_text(path, _check_body(text))
    _invalidate(layer)
    return {"layer": layer, "id": name, "created": True}


def update_text(layer: str, name: str, text: str) -> dict[str, Any]:
    """Overwrite the body of an existing layer file (core included)."""
    body = _check_body(text)
    if layer == "core":
        path = prompt_profiles.root_dir() / prompt_profiles.CORE_REL
        _write_text(path, body)
        return {"layer": "core", "id": "core", "updated": True}

    spec = LAYERS.get(layer)
    if spec is None:
        raise ValueError(f"未知文本层：{layer}")
    folder = _layer_folder(spec[0], spec[1])
    if folder is None:
        raise ValueError("非法文本层路径")
    for candidate in (folder / f"{name}.txt", folder / f"{_hidden_name(name)}.txt"):
        if candidate.is_file():
            _write_text(candidate, body)
            _invalidate(layer)
            return {"layer": layer, "id": name, "updated": True}
    raise FileNotFoundError(f"文本不存在：{layer}/{name}")


def delete_text(layer: str, name: str) -> dict[str, Any]:
    """Delete one layer file (custom or built-in; the UI warns, the API obeys)."""
    spec = LAYERS.get(layer)
    if spec is None:
        raise ValueError(f"未知文本层：{layer}")
    folder = _layer_folder(spec[0], spec[1])
    if folder is None:
        raise ValueError("非法文本层路径")
    for candidate in (folder / f"{name}.txt", folder / f"{_hidden_name(name)}.txt"):
        resolved = candidate.resolve()
        try:
            resolved.relative_to(prompt_profiles.root_dir().resolve())
        except ValueError:
            raise ValueError("路径越界") from None
        if resolved.is_file():
            resolved.unlink()
            _invalidate(layer)
            return {"layer": layer, "id": name, "deleted": True}
    raise FileNotFoundError(f"文本不存在：{layer}/{name}")


def set_hidden(layer: str, name: str, hidden: bool) -> dict[str, Any]:
    """Toggle visibility by renaming to/from the ``__`` hidden prefix."""
    spec = LAYERS.get(layer)
    if spec is None:
        raise ValueError(f"未知文本层：{layer}")
    folder = _layer_folder(spec[0], spec[1])
    if folder is None:
        raise ValueError("非法文本层路径")
    shown = folder / f"{name}.txt"
    hidden_path = folder / f"{_hidden_name(name)}.txt"
    # Hiding renames shown -> "__name"; showing renames back.
    source, target = (shown, hidden_path) if hidden else (hidden_path, shown)
    if not source.is_file():
        raise FileNotFoundError(f"文本不存在：{layer}/{name}")
    target.unlink(missing_ok=True)
    source.rename(target)
    _invalidate(layer)
    return {"layer": layer, "id": name, "hidden": bool(hidden)}


def _invalidate(layer: str) -> None:
    """Drop the scanner cache so the change shows up on the next list call."""
    prompt_personas._cache["key"] = None


def list_modes_with_flags() -> list[dict[str, Any]]:
    """Modes expose code-bound behaviour flags; kept for the UI summary."""
    return prompt_personas.list_modes(force=True)
