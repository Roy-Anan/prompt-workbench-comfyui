"""HTTP routes for the three management pages.

Kept thin on purpose: request/response shaping only. All rules (id validation,
path containment, metadata cleaning, delete semantics) live in
``workflow_manage`` / ``model_docs`` / ``injection_docs`` so the same behaviour is
reusable from tests and the ComfyUI extension proxy.

Routers:
  /api/workflow-library  -- imported workflow management cards
  /api/model-docs        -- base-model (Checkpoint) documentation cards
  /api/injection-docs    -- LLM injection text layers
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import injection_docs
import model_docs
import prompt_profiles
import workflow_manage

workflow_library_router = APIRouter(
    prefix="/api/workflow-library", tags=["workflow-library"]
)
model_docs_router = APIRouter(prefix="/api/model-docs", tags=["model-docs"])
injection_router = APIRouter(prefix="/api/injection-docs", tags=["injection-docs"])


# --------------------------------------------------------------------------- #
# Workflow library
# --------------------------------------------------------------------------- #


class WorkflowMetaUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = None
    favored: bool | None = None
    pinned: bool | None = None


class WorkflowDuplicate(BaseModel):
    title: str = Field(default="", max_length=120)
    with_drafts: bool = False


@workflow_library_router.get("")
def list_library(include_archived: bool = True):
    return {"items": workflow_manage.list_workflows(include_archived=include_archived)}


@workflow_library_router.get("/{workflow_id}")
def get_library_item(workflow_id: str):
    try:
        return workflow_manage.get_workflow(workflow_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@workflow_library_router.post("/{workflow_id}/meta")
def update_library_meta(workflow_id: str, payload: WorkflowMetaUpdate):
    patch = payload.model_dump(exclude_none=True)
    try:
        return workflow_manage.update_meta(workflow_id, patch)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc


@workflow_library_router.post("/{workflow_id}/duplicate")
def duplicate_library_item(workflow_id: str, payload: WorkflowDuplicate):
    try:
        return workflow_manage.duplicate(
            workflow_id, title=payload.title, with_drafts=payload.with_drafts
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc


@workflow_library_router.post("/{workflow_id}/archive")
def archive_library_item(workflow_id: str, archived: bool = True):
    try:
        return workflow_manage.set_archived(workflow_id, archived)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@workflow_library_router.delete("/{workflow_id}")
def delete_library_item(workflow_id: str):
    """Permanently delete the workflow folder; drafts and snapshots go with it."""
    try:
        return workflow_manage.delete_workflow(workflow_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc


# --------------------------------------------------------------------------- #
# Base-model documentation
# --------------------------------------------------------------------------- #


class ModelDocPayload(BaseModel):
    display_name: str = Field(default="", max_length=120)
    checkpoint_file: str = Field(default="", max_length=200)
    checkpoint_patterns: list[str] | None = None
    architecture: str = Field(default="", max_length=40)
    strengths: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=500)
    tags: list[str] | None = None
    cover_asset_id: int | None = None
    model_page: str = Field(default="", max_length=300)
    author: str = Field(default="", max_length=120)
    trust_level: str = Field(default="", max_length=20)
    trigger_words: list[str] | None = None
    version: str = Field(default="1.0.0", max_length=40)
    enabled: bool = True
    rules_text: str | None = None


@model_docs_router.get("")
def list_model_docs():
    return {"items": prompt_profiles.list_profiles()}


@model_docs_router.get("/template")
def get_model_template():
    return {"text": model_docs.template_text()}


@model_docs_router.get("/{profile_id}")
def get_model_doc(profile_id: str):
    entry = prompt_profiles.get_profile(profile_id)
    if entry is None:
        raise HTTPException(404, f"资料不存在：{profile_id}")
    return entry


@model_docs_router.post("")
def create_model_doc(payload: ModelDocPayload):
    try:
        return model_docs.create_model_doc(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc


@model_docs_router.post("/{profile_id}")
def update_model_doc(profile_id: str, payload: ModelDocPayload):
    patch: dict[str, Any] = payload.model_dump(exclude_none=True)
    try:
        return model_docs.update_model_doc(profile_id, patch)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc


@model_docs_router.delete("/{profile_id}")
def delete_model_doc(profile_id: str):
    try:
        return model_docs.delete_model_doc(profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc


# --------------------------------------------------------------------------- #
# Injection text
# --------------------------------------------------------------------------- #


class InjectionTextPayload(BaseModel):
    name: str = Field(default="", max_length=60)
    text: str = ""


class InjectionUpdatePayload(BaseModel):
    text: str = ""


@injection_router.get("")
def list_injection():
    return injection_docs.list_layers()


@injection_router.get("/modes")
def list_injection_modes():
    return {"items": injection_docs.list_modes_with_flags()}


@injection_router.get("/{layer}/{name}")
def read_injection_text(layer: str, name: str):
    try:
        return injection_docs.read_text(layer, name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@injection_router.post("/{layer}")
def create_injection_text(layer: str, payload: InjectionTextPayload):
    try:
        return injection_docs.create_text(layer, payload.name, payload.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc


@injection_router.post("/{layer}/{name}")
def update_injection_text(layer: str, name: str, payload: InjectionUpdatePayload):
    try:
        return injection_docs.update_text(layer, name, payload.text)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc


@injection_router.post("/{layer}/{name}/hidden")
def toggle_injection_hidden(layer: str, name: str, hidden: bool = True):
    try:
        return injection_docs.set_hidden(layer, name, hidden)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc


@injection_router.delete("/{layer}/{name}")
def delete_injection_text(layer: str, name: str):
    try:
        return injection_docs.delete_text(layer, name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc
