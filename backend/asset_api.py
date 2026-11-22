"""HTTP routes for the local image asset library.

All routes sit under ``/api/assets`` and inherit the session/CSRF middleware in
``main.py``. Image bytes are served only through the protected ``/image`` proxy;
there is no open local-file reader.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import asset_library
import datadir
import generation_history

router = APIRouter(prefix="/api/assets", tags=["assets"])


def _database_path() -> Path:
    return datadir.database_path(datadir.default_data_root())


def _assets_dir() -> Path:
    return datadir.default_data_root() / "assets"


def _connect():
    connection = asset_library.connect(_database_path())
    asset_library.ensure_schema(connection)
    return connection


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# ---- Request models ----

class GroupCreate(BaseModel):
    name: str


class GroupRename(BaseModel):
    name: str


class ImportFile(BaseModel):
    name: str
    data: str


class CopyImport(BaseModel):
    files: list[ImportFile]
    group_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class ReferenceImport(BaseModel):
    paths: list[str]
    group_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class HistoryImport(BaseModel):
    filename: str
    subfolder: str = ""
    note: str = ""


class ItemUpdate(BaseModel):
    name: str | None = None
    group_id: int | None = None
    tags: list[str] | None = None
    note: str | None = None


class ItemDelete(BaseModel):
    delete_copy: bool = False


# ---- Groups ----

@router.get("/groups")
def list_groups():
    with _connect() as connection:
        return {"groups": asset_library.list_groups(connection)}


@router.post("/groups")
def create_group(payload: GroupCreate):
    try:
        with _connect() as connection:
            group = asset_library.create_group(connection, payload.name)
        return group
    except ValueError as exc:
        raise _fail(exc) from exc


@router.post("/groups/{group_id}/rename")
def rename_group(group_id: int, payload: GroupRename):
    try:
        with _connect() as connection:
            asset_library.rename_group(connection, group_id, payload.name, _assets_dir())
        return {"ok": True}
    except (ValueError, LookupError) as exc:
        raise _fail(exc) from exc


@router.delete("/groups/{group_id}")
def delete_group(group_id: int):
    try:
        with _connect() as connection:
            asset_library.delete_group(connection, group_id, _assets_dir())
        return {"ok": True}
    except (ValueError, LookupError) as exc:
        raise _fail(exc) from exc


# ---- Tags ----

@router.get("/tags")
def list_tags():
    with _connect() as connection:
        return {"tags": asset_library.list_tags(connection)}


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int):
    with _connect() as connection:
        asset_library.delete_tag(connection, tag_id)
    return {"ok": True}


# ---- Items ----

@router.get("/items")
def list_items(
    group_id: int | None = None,
    tags: str | None = None,
    keyword: str = "",
):
    tag_names = [entry for entry in (tags or "").split(",") if entry.strip()]
    with _connect() as connection:
        items = asset_library.list_items(
            connection,
            _assets_dir(),
            group_id=group_id,
            tag_names=tag_names,
            keyword=keyword,
        )
    return {"items": items}


@router.post("/import-copy")
def import_copy(payload: CopyImport):
    if not payload.files:
        raise HTTPException(status_code=400, detail="未选择要导入的图片")
    try:
        with _connect() as connection:
            created = asset_library.import_copies(
                connection,
                _assets_dir(),
                [entry.model_dump() for entry in payload.files],
                group_id=payload.group_id,
                tag_names=payload.tags,
                note=payload.note,
            )
        return {"items": created}
    except (ValueError, LookupError, OSError) as exc:
        raise _fail(exc) from exc


@router.post("/import-reference")
def import_reference(payload: ReferenceImport):
    if not payload.paths:
        raise HTTPException(status_code=400, detail="未填写引用路径")
    try:
        with _connect() as connection:
            created = asset_library.import_references(
                connection,
                payload.paths,
                group_id=payload.group_id,
                tag_names=payload.tags,
                note=payload.note,
            )
        return {"items": created}
    except (ValueError, LookupError) as exc:
        raise _fail(exc) from exc


@router.post("/import-history")
def import_history_image(payload: HistoryImport):
    """Register a recorded ComfyUI output image as a library reference.

    The file stays in the configured output directory (no bytes are copied);
    only a reference record is added so it can be reused as a model cover or
    other library thumbnail. Missing / escaped / non-image names are 404.
    """
    settings = datadir.load_settings_file(datadir.default_data_root()) or {}
    output_dir = (settings.get("storage") or {}).get("output", "")
    path = generation_history.resolve_image_path(
        output_dir, payload.filename, payload.subfolder
    )
    if path is None:
        raise HTTPException(status_code=404, detail="历史图片不存在或不可访问")
    try:
        with _connect() as connection:
            created = asset_library.import_references(
                connection, [str(path)], note=payload.note
            )
        return {"items": created}
    except (ValueError, LookupError, OSError) as exc:
        raise _fail(exc) from exc


@router.post("/items/{asset_id}")
def update_item(asset_id: int, payload: ItemUpdate):
    try:
        with _connect() as connection:
            record = asset_library.update_item(
                connection,
                asset_id,
                _assets_dir(),
                name=payload.name,
                group_id=payload.group_id,
                tag_names=payload.tags,
                note=payload.note,
            )
        if record is None:
            raise HTTPException(status_code=404, detail="素材不存在")
        return record
    except (ValueError, LookupError) as exc:
        raise _fail(exc) from exc


@router.delete("/items/{asset_id}")
def delete_item(asset_id: int, payload: ItemDelete):
    try:
        with _connect() as connection:
            asset_library.delete_item(
                connection,
                _assets_dir(),
                asset_id,
                delete_copy=payload.delete_copy,
            )
        return {"ok": True}
    except LookupError as exc:
        raise _fail(exc) from exc


@router.get("/image")
def asset_image(asset_id: int = Query(...)):
    with _connect() as connection:
        path = asset_library.resolve_image_path(connection, _assets_dir(), asset_id)
    if path is None:
        raise HTTPException(status_code=404, detail="图片不存在或已失效")
    return FileResponse(path)


def _open_fixed_dir(directory: Path) -> dict[str, Any]:
    """Open the fixed asset directory in the host file manager.

    Only this fixed directory is ever opened; no caller-supplied path is
    accepted. The directory is created if absent so a fresh library can still
    be browsed.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(directory))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(directory)], check=True)
    else:
        subprocess.run(["xdg-open", str(directory)], check=True)
    return {"path": str(directory)}


@router.post("/open-dir")
def open_assets_dir():
    """Open the local asset storage folder in the system file manager."""
    try:
        return _open_fixed_dir(_assets_dir())
    except FileNotFoundError as exc:
        raise HTTPException(404, f"素材目录不存在: {exc}") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, f"无法打开素材目录: {exc}") from exc
