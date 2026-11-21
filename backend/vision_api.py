"""Standalone local vision description (workbench only; not the ComfyUI node).

This module lets the workbench ask a local Ollama vision (VL) model to describe
an uploaded picture. It talks only to the configured local Ollama instance;
there is no cloud fallback, and a failed/empty response is a conservative error
with no fabricated description.

Boundaries mirror :mod:`llm_client`:

* Images arrive as base64 (an optional ``data:image/...;base64,`` prefix is
  stripped), are sniffed by magic bytes and size-limited before being passed on.
* Only models reported locally by ``/api/tags`` are accepted; non-VL models are
  rejected instead of sending images a text-only model cannot read.
* The result is returned to the caller and is never written into a workflow,
  draft or history.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request as UrlRequest, urlopen

import comfy_client
import datadir
import llm_client
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/api/vision", tags=["vision"])

MAX_IMAGES = 2
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_DESCRIPTION_CHARS = 20_000
VISION_LLM_TIMEOUT = 180.0
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}

DEFAULT_VISION_PROMPT = (
    "请描述这张图片：主体与人数、可见外貌特征、姿态动作与空间关系、"
    "服装配饰、场景背景道具、构图视角镜头、光线色彩材质与整体风格。"
)

# Appended to the user prompt only when the caller asks for suggested tags.
# The model is told to emit one extra trailing line; it is split off the
# description afterwards so the saved description stays clean.
TAG_INSTRUCTION = (
    "\n\n描述写完后必须另起一行输出标签，不要省略。格式严格为："
    "标签: A, B, C（3-6 个简短中文标签，每个 2-6 字，英文逗号分隔，"
    "不加引号、不加序号、不写解释）。示例：\n标签: 人物, 半身, 冷色调"
)
TAG_LINE_PREFIXES = ("标签:", "标签：", "tags:", "Tags:")
TAG_TRIM_CHARS = "。.、;；,，'\"“”‘’*#`>-_ " 
MAX_TAGS = 8
MAX_TAG_CHARS = 20


def _split_tags(description: str) -> tuple[str, list[str]]:
    """Split an optional trailing ``标签: a, b`` line off the description.

    The line is looked up in the last few rows, since models sometimes append
    a closing remark after it. Markdown emphasis around the label is ignored.
    """
    lines = description.rstrip().splitlines()
    if not lines:
        return description, []
    for index in range(len(lines) - 1, max(-1, len(lines) - 4), -1):
        cleaned = lines[index].strip().strip(TAG_TRIM_CHARS).strip()
        for prefix in TAG_LINE_PREFIXES:
            if not cleaned.lower().startswith(prefix.lower()):
                continue
            raw = cleaned[len(prefix) :]
            seen: list[str] = []
            for entry in raw.replace("，", ",").replace("、", ",").split(","):
                name = entry.strip().strip(TAG_TRIM_CHARS)
                if not name or len(name) > MAX_TAG_CHARS:
                    continue
                if name not in seen:
                    seen.append(name)
            body = "\n".join(lines[:index] + lines[index + 1 :]).strip()
            return body, seen[:MAX_TAGS]
    return description, []


class VisionRequest(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    prompt: str | None = Field(default=None, max_length=2000)
    images: list[str] = Field(min_length=1, max_length=MAX_IMAGES)
    want_tags: bool = False

    @field_validator("images")
    @classmethod
    def _images_non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("images 不能为空")
        return cleaned


def _ollama_url() -> str:
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


def _local_models() -> dict[str, dict[str, Any]]:
    """Read-only local Ollama model map ``{name: details}`` via ``/api/tags``."""
    target = _ollama_url() + "/api/tags"
    request = UrlRequest(target)
    request.add_header("User-Agent", llm_client.APP_NAME)
    try:
        with urlopen(request, timeout=2.5) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=502,
            detail={"online": False, "error": f"无法连接本地 Ollama：{exc}"},
        )
    entries = data.get("models") if isinstance(data, dict) else None
    models: dict[str, dict[str, Any]] = {}
    if isinstance(entries, list):
        for item in entries:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                details = item.get("details")
                models[item["name"]] = details if isinstance(details, dict) else {}
    return models


def _show_model(ollama_url: str, name: str) -> dict[str, Any]:
    """Read-only ``/api/show`` detail; an unreachable/garbled reply returns {}."""
    target = ollama_url.rstrip("/") + "/api/show"
    body = json.dumps({"name": name}).encode("utf-8")
    request = UrlRequest(target, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", llm_client.APP_NAME)
    try:
        with urlopen(request, timeout=2.5) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return {}


def classify_vision(
    details: dict[str, Any], show: dict[str, Any]
) -> tuple[bool, str]:
    """Decide whether a local model can actually read images.

    Returns ``(capable, reason)``; ``reason`` is ``"projector"`` (a real vision
    projector/mmproj is attached) or ``"no_projector"`` (no projector layer is
    reported). A VL-looking family tag alone is not trusted: Ollama omits the
    ``projector_info`` field entirely when the model has no vision projector,
    and a text-only GGUF may still carry a ``qwen3vl``-style family label.
    """
    projector = show.get("projector_info")
    if isinstance(projector, dict) and projector:
        return True, "projector"
    if isinstance(projector, list) and projector:
        return True, "projector"
    return False, "no_projector"


def probe_vision_map(
    ollama_url: str, models: dict[str, dict[str, Any]]
) -> dict[str, bool]:
    """Per-model accurate vision capability for the model-list endpoint."""
    result: dict[str, bool] = {}
    for name, details in models.items():
        show = _show_model(ollama_url, name)
        capable, _ = classify_vision(details, show)
        result[name] = capable
    return result


def _sniff_mime(blob: bytes) -> str | None:
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    return None


def _decode_image(item: str) -> str:
    """Strip an optional data-URI prefix, validate bytes, return plain base64."""
    raw = item.strip()
    if raw.startswith("data:"):
        _, _, raw = raw.partition(",")
    try:
        blob = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="图片 base64 解码失败")
    if len(blob) == 0:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(blob) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"单张图片不能超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MB",
        )
    mime = _sniff_mime(blob)
    if mime not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail="仅支持 PNG / JPEG / WebP / GIF 图片")
    return raw


@router.post("/describe")
def describe_image(payload: VisionRequest) -> dict[str, Any]:
    """Ask a local VL model to describe the uploaded image(s)."""
    models = _local_models()
    if payload.model not in models:
        raise HTTPException(
            status_code=400, detail=f"本地未安装模型：{payload.model}"
        )
    show = _show_model(_ollama_url(), payload.model)
    capable, reason = classify_vision(models[payload.model], show)
    if not capable:
        if reason == "no_projector":
            detail = (
                "所选模型未挂载视觉投影器（mmproj），当前不能读取图片；"
                "请为该模型补充投影器权重后再使用视觉识别。"
            )
        else:
            detail = "所选模型不是视觉（VL）模型，无法读取图片；请换用本地 VL 模型。"
        raise HTTPException(status_code=400, detail=detail)

    images = [_decode_image(item) for item in payload.images]

    # Reuse the LLM/ComfyUI mutex gate: do not load the VL model into VRAM
    # while a ComfyUI task is in flight.
    queue = comfy_client.queue_has_active_task(_comfyui_url())
    if queue["active"]:
        raise HTTPException(
            status_code=409,
            detail={
                "online": True,
                "error": "ComfyUI 队列有待执行或运行中任务，请完成后再使用本地视觉模型。",
            },
        )

    prompt = (payload.prompt or "").strip() or DEFAULT_VISION_PROMPT
    if payload.want_tags:
        prompt = prompt + TAG_INSTRUCTION
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": prompt, "images": images}
    ]
    result = llm_client.chat(
        _ollama_url(),
        payload.model,
        messages,
        options={"num_predict": 1024},
        timeout=VISION_LLM_TIMEOUT,
    )
    if not result["ok"]:
        raise HTTPException(
            status_code=502,
            detail={"online": False, "error": result["error"]},
        )

    description = result["content"].strip()
    if not description:
        raise HTTPException(
            status_code=502,
            detail={"online": True, "error": "模型返回内容为空，未生成描述。"},
        )
    tags: list[str] = []
    if payload.want_tags:
        description, tags = _split_tags(description)
        if not description:
            raise HTTPException(
                status_code=502,
                detail={"online": True, "error": "模型只返回了标签，未生成描述。"},
            )
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise HTTPException(
            status_code=502,
            detail={"online": True, "error": "模型返回描述超出长度上限。"},
        )

    return {
        "model": result["model"],
        "description": description,
        "image_count": len(images),
        "tags": tags,
    }
