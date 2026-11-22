from __future__ import annotations
import copy
import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import generation_history
MAX_WORKFLOW_BYTES = 20 * 1024 * 1024
MAX_PROMPT_CHARS = 200_000
MAX_DRAFT_NAME_CHARS = 120
MAX_DRAFT_NOTE_CHARS = 1000
MAX_DRAFT_TAGS = 12
MAX_DRAFT_TAG_CHARS = 30
MAX_SOURCE_REF_CHARS = 64
TEXT_ENCODER_MARKERS = ("CLIPTextEncode", "TextEncode")
SAMPLER_MARKERS = ("KSampler", "SamplerCustom", "SamplerAdvanced")
LATENT_MARKERS = ("LatentImage",)
CLIP_SKIP_MARKERS = ("CLIPSetLastLayer",)
# 中转 conditioning 节点：自身不是文本编码器，但会把上游条件继续向下传。
CONDITIONING_PASSTHROUGH_MARKERS = (
    "ConditioningCombine",
    "ConditioningConcat",
    "ConditioningSetArea",
    "ConditioningSetAreaPercentage",
    "ConditioningSetAreaStrength",
    "ConditioningAverage",
    "ConditioningMask",
    "ConditioningZeroOut",
    "ConditioningDrop",
    "ConditioningBias",
)
# 这些输入名携带 conditioning 链路，追溯时沿它们继续向上。
CONDITIONING_INPUT_KEYS = (
    "conditioning",
    "positive",
    "negative",
    "conditioning1",
    "conditioning2",
    "cond",
)
MODEL_NODE_NAMES = {
    "CheckpointLoaderSimple",
    "CheckpointLoader",
    "UNETLoader",
    "DualCLIPLoader",
    "CLIPLoader",
    "VAELoader",
}
class WorkflowError(Exception):
    """A workflow cannot be safely processed."""
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
def fingerprint(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()
def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"无法读取工作流数据：{exc}") from exc


def _clean_source_ref(raw: Any) -> str | None:
    """Normalize an optional back-reference to a generation history prompt_id."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()[:MAX_SOURCE_REF_CHARS]
    return value or None


def _extract_env_snapshot(report: Any) -> dict[str, Any]:
    """Freeze a read-only summary of the models and sampler parameters at save time.

    Only facts the workflow parser already recognized are recorded; missing or
    unrecognized inputs stay absent rather than being filled in. The working
    copy and ComfyUI workflow are never modified here.
    """
    snapshot: dict[str, Any] = {
        "checkpoints": [],
        "loras": [],
        "vaes": [],
        "clips": [],
        "resolution": None,
        "clip_skip": None,
        "samplers": [],
    }
    if not isinstance(report, dict):
        return snapshot

    model_snapshot = generation_history.extract_model_snapshot(report)
    for key in ("checkpoints", "loras", "vaes", "clips"):
        entries = model_snapshot.get(key)
        snapshot[key] = entries if isinstance(entries, list) else []

    env_params = generation_history.extract_env_params(report)
    snapshot["resolution"] = env_params["resolution"]
    snapshot["clip_skip"] = env_params["clip_skip"]
    snapshot["samplers"] = env_params["samplers"]
    return snapshot


def _clean_env_snapshot(raw: Any) -> dict[str, Any] | None:
    """Tolerant read-back normalization for a stored draft environment snapshot."""
    if not isinstance(raw, dict):
        return None
    snapshot: dict[str, Any] = {
        "checkpoints": [],
        "loras": [],
        "vaes": [],
        "clips": [],
        "resolution": None,
        "clip_skip": None,
        "samplers": [],
    }
    for key in ("checkpoints", "loras", "vaes", "clips", "samplers"):
        entries = raw.get(key)
        snapshot[key] = entries if isinstance(entries, list) else []
    resolution = raw.get("resolution")
    if isinstance(resolution, str) and resolution.strip():
        snapshot["resolution"] = resolution.strip()
    clip_skip = raw.get("clip_skip")
    if isinstance(clip_skip, int):
        snapshot["clip_skip"] = clip_skip
    return snapshot


def _clean_tag(raw: Any) -> str | None:
    """Whitelist-normalize one tag; return ``None`` when it is empty."""
    if not isinstance(raw, str):
        return None
    tag = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff ]", "_", raw.strip())
    tag = tag.strip(" .")[:MAX_DRAFT_TAG_CHARS]
    return tag or None


def _clean_tags(raw: Any) -> list[str]:
    """Strip, de-duplicate and cap draft tags; non-strings are skipped."""
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = _clean_tag(item)
        if tag is None or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= MAX_DRAFT_TAGS:
            break
    return tags
def _safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff ]", "_", name)
    name = name.strip(" .")[:100]
    return name or "workflow.json"
def _valid_api_nodes(nodes: Any) -> bool:
    return (
        isinstance(nodes, dict)
        and bool(nodes)
        and all(
            isinstance(node, dict)
            and isinstance(node.get("class_type"), str)
            and isinstance(node.get("inputs"), dict)
            for node in nodes.values()
        )
    )
def detect_format(workflow: Any) -> str:
    if not isinstance(workflow, dict):
        raise WorkflowError("工作流 JSON 根节点必须是对象")
    nodes = workflow.get("nodes")
    # ComfyUI API prompt 的兼容包装格式。
    if _valid_api_nodes(nodes):
        return "api"
    # ComfyUI 标准 API prompt：
    # {"1": {"class_type": "...", "inputs": {...}}, ...}
    if _valid_api_nodes(workflow):
        return "api"
    # ComfyUI UI workflow。
    if (
        isinstance(nodes, list)
        and isinstance(workflow.get("links"), list)
        and all(isinstance(node, dict) for node in nodes)
    ):
        return "ui"
    raise WorkflowError(
        "无法识别工作流格式。请导入 ComfyUI API workflow 或 UI workflow JSON。"
    )
def _api_nodes(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = workflow.get("nodes")
    if _valid_api_nodes(nodes):
        return {str(key): value for key, value in nodes.items()}
    if _valid_api_nodes(workflow):
        return {str(key): value for key, value in workflow.items()}
    raise WorkflowError("API workflow 节点结构无效")
def _is_text_encoder(class_type: str) -> bool:
    return any(marker in class_type for marker in TEXT_ENCODER_MARKERS)
def _is_conditioning_passthrough(class_type: str) -> bool:
    return any(
        marker in class_type
        for marker in CONDITIONING_PASSTHROUGH_MARKERS
    )
def _is_sampler(class_type: str, inputs: dict[str, Any]) -> bool:
    return (
        any(marker in class_type for marker in SAMPLER_MARKERS)
        or ("positive" in inputs and "negative" in inputs)
    )


_SAMPLER_PARAM_KEYS = ("steps", "cfg", "sampler_name", "scheduler", "denoise")


def _latent_size(inputs: dict[str, Any]) -> dict[str, Any] | None:
    """Extract width/height scalars from a latent-image node, if both exist."""
    width = inputs.get("width")
    height = inputs.get("height")
    if isinstance(width, bool) or isinstance(height, bool):
        return None
    if isinstance(width, (int, float)) and isinstance(height, (int, float)):
        return {"width": int(width), "height": int(height)}
    if isinstance(width, str) and isinstance(height, str):
        try:
            return {"width": int(width), "height": int(height)}
        except ValueError:
            return None
    return None


def _clip_skip(inputs: dict[str, Any]) -> dict[str, Any] | None:
    """Derive a positive clip-skip count from a CLIPSetLastLayer node.

    ComfyUI stores the stop layer as a negative index (``-1`` … ``-24``); the
    rule layer compares a positive skipped-layer count, so ``clip_skip`` is the
    negated value. Non-negative or non-integer values are left untouched and
    reported as None instead of guessing a conversion.
    """
    raw = inputs.get("stop_at_clip_layer")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        try:
            value = int(raw.strip())
        except ValueError:
            return None
    else:
        return None
    if value >= 0:
        return None
    return {"clip_skip": -value, "stop_at_clip_layer": value}


def _sampler_params(inputs: dict[str, Any]) -> dict[str, Any]:
    """Extract scalar sampler inputs the rule layer can compare against.

    Only known keys are copied; link arrays and unrelated inputs are left out.
    """
    params: dict[str, Any] = {}
    for key in _SAMPLER_PARAM_KEYS:
        value = inputs.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            params[key] = value
    return params
def _structure_diff(
    original: Any,
    candidate: Any,
    allowed_paths: set[str],
    prefix: str = "",
) -> list[str]:
    """Return structural differences; only `allowed_paths` may differ.

    Paths use a dot/array index prefix, e.g. ``"6.inputs.seed"``. Node-level
    whitelist paths are stored as ``"<node_id>.inputs.text"``.
    """
    diffs: list[str] = []
    if type(original) is not type(candidate):
        return [prefix or "<root>"]
    if isinstance(original, dict):
        if original.keys() != candidate.keys():
            diffs.append(prefix or "<root>")
        for key, original_value in original.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            diffs.extend(
                _structure_diff(
                    original_value,
                    candidate[key],
                    allowed_paths,
                    child,
                )
            )
        return diffs
    if isinstance(original, list):
        if len(original) != len(candidate):
            diffs.append(prefix or "<root>")
        for index, original_value in enumerate(original):
            child = f"{prefix}[{index}]"
            diffs.extend(
                _structure_diff(
                    original_value,
                    candidate[index],
                    allowed_paths,
                    child,
                )
            )
        return diffs
    if original != candidate:
        diffs.append(prefix or "<root>")
    return [item for item in diffs if item not in allowed_paths]
def _api_link_node_id(value: Any, nodes: dict[str, Any]) -> str | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    candidate = str(value[0])
    return candidate if candidate in nodes else None
def _trace_api_encoders(
    nodes: dict[str, dict[str, Any]],
    start_value: Any,
) -> list[str]:
    first = _api_link_node_id(start_value, nodes)
    if first is None:
        return []
    found: list[str] = []
    visited: set[str] = set()
    stack = [first]
    while stack:
        node_id = stack.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        node = nodes.get(node_id)
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", ""))
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        if _is_text_encoder(class_type):
            if isinstance(inputs.get("text"), str):
                found.append(node_id)
                continue
        # 文本编码器的 clip 输入不携带 conditioning，不向上追溯；
        # 中转 conditioning 节点沿其 conditioning 输入（含 conditioning_1/2 等编号键）继续追溯。
        if _is_conditioning_passthrough(class_type):
            trace_keys = [
                key
                for key in inputs
                if key in CONDITIONING_INPUT_KEYS
                or str(key).startswith("conditioning")
            ]
        else:
            trace_keys = tuple(inputs.keys())
        for key in trace_keys:
            value = inputs.get(key)
            upstream = _api_link_node_id(value, nodes)
            if upstream is not None:
                stack.append(upstream)
    return sorted(set(found), key=lambda item: (len(item), item))
def _parse_api(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes = _api_nodes(workflow)
    prompt_roles: dict[str, set[str]] = {}
    prompt_groups: list[dict[str, Any]] = []
    model_nodes: list[dict[str, Any]] = []
    lora_nodes: list[dict[str, Any]] = []
    latent: dict[str, Any] | None = None
    clip_skip: dict[str, Any] | None = None
    latent_nodes: list[dict[str, Any]] = []
    clip_skip_nodes: list[dict[str, Any]] = []
    for node_id, node in nodes.items():
        class_type = str(node.get("class_type", ""))
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}
        if any(marker in class_type for marker in LATENT_MARKERS):
            latent_size = _latent_size(inputs)
            if latent_size is not None:
                latent_entry = {"node_id": node_id, **latent_size}
                latent_nodes.append(latent_entry)
                if latent is None:
                    latent = latent_entry
        if any(marker in class_type for marker in CLIP_SKIP_MARKERS):
            derived = _clip_skip(inputs)
            if derived is not None:
                clip_entry = {"node_id": node_id, **derived}
                clip_skip_nodes.append(clip_entry)
                if clip_skip is None:
                    clip_skip = clip_entry
        if (
            class_type in MODEL_NODE_NAMES
            or ("Loader" in class_type and "Lora" not in class_type)
        ):
            model_nodes.append({
                "node_id": node_id,
                "class_type": class_type,
                "inputs": {
                    key: value
                    for key, value in inputs.items()
                    if isinstance(value, (str, int, float, bool))
                    or value is None
                },
            })
        if "LoraLoader" in class_type:
            lora_nodes.append({
                "node_id": node_id,
                "class_type": class_type,
                "inputs": {
                    key: value
                    for key, value in inputs.items()
                    if isinstance(value, (str, int, float, bool))
                    or value is None
                },
            })
        if not _is_sampler(class_type, inputs):
            continue
        group: dict[str, Any] = {
            "sampler_node_id": node_id,
            "sampler_class": class_type,
            "positive_node_ids": [],
            "negative_node_ids": [],
            "params": _sampler_params(inputs),
        }
        for role in ("positive", "negative"):
            ids = _trace_api_encoders(nodes, inputs.get(role))
            group[f"{role}_node_ids"] = ids
            for encoder_id in ids:
                prompt_roles.setdefault(encoder_id, set()).add(role)
        prompt_groups.append(group)
    prompt_nodes: list[dict[str, Any]] = []
    for node_id, roles in prompt_roles.items():
        node = nodes[node_id]
        inputs = node["inputs"]
        prompt_nodes.append({
            "node_id": node_id,
            "class_type": str(node.get("class_type", "")),
            "roles": sorted(roles),
            "text": inputs.get("text", ""),
            "editable": isinstance(inputs.get("text"), str),
        })
    # 没有找到采样器连接时，仅提供可确认的文本节点候选。
    if not prompt_nodes:
        for node_id, node in nodes.items():
            class_type = str(node.get("class_type", ""))
            inputs = node.get("inputs", {})
            if (
                _is_text_encoder(class_type)
                and isinstance(inputs, dict)
                and isinstance(inputs.get("text"), str)
            ):
                prompt_nodes.append({
                    "node_id": node_id,
                    "class_type": class_type,
                    "roles": [],
                    "text": inputs["text"],
                    "editable": True,
                })
    return {
        "format": "api",
        "node_count": len(nodes),
        "model_nodes": model_nodes,
        "lora_nodes": lora_nodes,
        "latent": latent,
        "clip_skip": clip_skip,
        "latent_nodes": latent_nodes,
        "clip_skip_nodes": clip_skip_nodes,
        "prompt_groups": prompt_groups,
        "prompt_nodes": sorted(
            prompt_nodes,
            key=lambda item: (len(item["node_id"]), item["node_id"]),
        ),
        "safe_prompt_write_supported": True,
        "notes": [],
    }
def _ui_link_map(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for link in workflow.get("links", []):
        if isinstance(link, list) and len(link) >= 6:
            result[str(link[0])] = {
                "origin_id": str(link[1]),
                "target_id": str(link[3]),
            }
        elif isinstance(link, dict):
            link_id = link.get("id")
            origin_id = link.get("origin_id", link.get("originId"))
            target_id = link.get("target_id", link.get("targetId"))
            if link_id is not None and origin_id is not None:
                result[str(link_id)] = {
                    "origin_id": str(origin_id),
                    "target_id": str(target_id) if target_id is not None else "",
                }
    return result
def _trace_ui_encoders(
    nodes: dict[str, dict[str, Any]],
    links: dict[str, dict[str, Any]],
    start_node: dict[str, Any],
    start_input_name: str,
) -> list[str]:
    start_input = next(
        (
            item
            for item in start_node.get("inputs", [])
            if isinstance(item, dict)
            and item.get("name") == start_input_name
        ),
        None,
    )
    if not isinstance(start_input, dict) or start_input.get("link") is None:
        return []
    link = links.get(str(start_input["link"]))
    if not link:
        return []
    stack = [link["origin_id"]]
    visited: set[str] = set()
    found: list[str] = []
    while stack:
        node_id = stack.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        node = nodes.get(node_id)
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type", ""))
        if _is_text_encoder(node_type):
            found.append(node_id)
            continue
        for item in node.get("inputs", []):
            if not isinstance(item, dict) or item.get("link") is None:
                continue
            upstream = links.get(str(item["link"]))
            if upstream:
                stack.append(upstream["origin_id"])
    return sorted(set(found))
# Widget order for common loaders, used to label read-only scalar values when
# the UI node exposes no per-input widget descriptor. Kept informational only;
# the UI workflow remains read-only and these values are never written back.
_UI_LOADER_WIDGET_ORDER: dict[str, tuple[str, ...]] = {
    "CheckpointLoaderSimple": ("ckpt_name",),
    "CheckpointLoader": ("ckpt_name",),
    "UNETLoader": ("unet_name",),
    "DualCLIPLoader": ("clip_name1", "clip_name2"),
    "CLIPLoader": ("clip_name",),
    "VAELoader": ("vae_name",),
    "LoraLoader": ("lora_name", "strength_model", "strength_clip"),
}


def _ui_widget_input_names(node: dict[str, Any]) -> list[str | None]:
    """Map ``widgets_values`` positions to input/control names for a UI node.

    Inputs already bound to a link hold no widget value; linked slots are
    skipped so scalar widgets stay aligned with their control names.
    """
    names: list[str | None] = []
    for item in node.get("inputs", []):
        if not isinstance(item, dict):
            continue
        if item.get("link") is not None:
            continue
        widget = item.get("widget")
        if isinstance(widget, dict) and widget.get("name"):
            names.append(str(widget["name"]))
        elif isinstance(item.get("name"), str):
            names.append(item["name"])
        else:
            names.append(None)

    if not names:
        node_type = str(node.get("type", ""))
        names = [None] * len(node.get("widgets_values", []))
        order = _UI_LOADER_WIDGET_ORDER.get(node_type, ())
        for index, field in enumerate(order[: len(names)]):
            names[index] = field
    return names


def _ui_inputs(node: dict[str, Any]) -> dict[str, Any]:
    """Extract read-only scalar widget values from a UI node."""
    widgets = node.get("widgets_values", [])
    if not isinstance(widgets, list):
        return {}
    names = _ui_widget_input_names(node)
    inputs: dict[str, Any] = {}
    for index, value in enumerate(widgets):
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            continue
        key = names[index] if index < len(names) else None
        if not key:
            key = f"widget_{index + 1}"
        inputs[key] = value
    return inputs


def _parse_ui(workflow: dict[str, Any]) -> dict[str, Any]:
    node_list = workflow.get("nodes", [])
    nodes = {
        str(node.get("id")): node
        for node in node_list
        if isinstance(node, dict) and node.get("id") is not None
    }
    links = _ui_link_map(workflow)
    prompt_roles: dict[str, set[str]] = {}
    prompt_groups: list[dict[str, Any]] = []
    model_nodes: list[dict[str, Any]] = []
    lora_nodes: list[dict[str, Any]] = []
    latent: dict[str, Any] | None = None
    clip_skip: dict[str, Any] | None = None
    latent_nodes: list[dict[str, Any]] = []
    clip_skip_nodes: list[dict[str, Any]] = []
    for node_id, node in nodes.items():
        node_type = str(node.get("type", ""))
        if any(marker in node_type for marker in LATENT_MARKERS):
            latent_size = _latent_size(_ui_inputs(node))
            if latent_size is not None:
                latent_entry = {"node_id": node_id, **latent_size}
                latent_nodes.append(latent_entry)
                if latent is None:
                    latent = latent_entry
        if any(marker in node_type for marker in CLIP_SKIP_MARKERS):
            derived = _clip_skip(_ui_inputs(node))
            if derived is not None:
                clip_entry = {"node_id": node_id, **derived}
                clip_skip_nodes.append(clip_entry)
                if clip_skip is None:
                    clip_skip = clip_entry
        if node_type in MODEL_NODE_NAMES or (
            "Loader" in node_type and "Lora" not in node_type
        ):
            model_nodes.append({
                "node_id": node_id,
                "class_type": node_type,
                "inputs": _ui_inputs(node),
            })
        if "LoraLoader" in node_type:
            lora_nodes.append({
                "node_id": node_id,
                "class_type": node_type,
                "inputs": _ui_inputs(node),
            })
        input_items = node.get("inputs", [])
        input_names = {
            item.get("name")
            for item in input_items
            if isinstance(item, dict)
        }
        if not (
            any(marker in node_type for marker in SAMPLER_MARKERS)
            or {"positive", "negative"} <= input_names
        ):
            continue
        group = {
            "sampler_node_id": node_id,
            "sampler_class": node_type,
            "positive_node_ids": [],
            "negative_node_ids": [],
            "params": _sampler_params(_ui_inputs(node)),
        }
        for role in ("positive", "negative"):
            ids = _trace_ui_encoders(nodes, links, node, role)
            group[f"{role}_node_ids"] = ids
            for encoder_id in ids:
                prompt_roles.setdefault(encoder_id, set()).add(role)
        prompt_groups.append(group)
    prompt_nodes: list[dict[str, Any]] = []
    for node_id, node in nodes.items():
        node_type = str(node.get("type", ""))
        if not _is_text_encoder(node_type):
            continue
        widgets = node.get("widgets_values", [])
        text_value = next(
            (value for value in widgets if isinstance(value, str)),
            "",
        )
        prompt_nodes.append({
            "node_id": node_id,
            "class_type": node_type,
            "roles": sorted(prompt_roles.get(node_id, set())),
            "text": text_value,
            "editable": False,
        })
    return {
        "format": "ui",
        "node_count": len(nodes),
        "model_nodes": model_nodes,
        "lora_nodes": lora_nodes,
        "latent": latent,
        "clip_skip": clip_skip,
        "latent_nodes": latent_nodes,
        "clip_skip_nodes": clip_skip_nodes,
        "prompt_groups": prompt_groups,
        "prompt_nodes": prompt_nodes,
        "safe_prompt_write_supported": False,
        "notes": [
            "UI workflow 当前仅支持只读解析。请从 ComfyUI 导出 API workflow 后再编辑 Prompt。"
        ],
    }
def analyze_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    workflow_format = detect_format(workflow)
    if workflow_format == "api":
        return _parse_api(workflow)
    return _parse_ui(workflow)
class WorkflowStore:
    """File-backed workflow storage under the workbench userdata directory."""
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
    def _folder(self, workflow_id: str) -> Path:
        try:
            normalized = str(uuid.UUID(workflow_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise WorkflowError("无效的工作流 ID") from exc
        folder = (self.root / normalized).resolve()
        try:
            folder.relative_to(self.root)
        except ValueError as exc:
            raise WorkflowError("工作流路径越界") from exc
        return folder
    def import_workflow(
        self,
        filename: str,
        workflow: dict[str, Any],
    ) -> dict[str, Any]:
        raw = _json_bytes(workflow)
        if len(raw) > MAX_WORKFLOW_BYTES:
            raise WorkflowError("工作流文件超过 20 MB 限制")
        workflow_format = detect_format(workflow)
        report = analyze_workflow(workflow)
        source_hash = fingerprint(workflow)
        existing = self._find_by_fingerprint(source_hash)
        if existing is not None:
            # Same source content already imported: reuse the existing record
            # instead of creating a second confirmed copy of the same workflow.
            return {
                "id": existing.get("id"),
                "filename": existing.get("filename"),
                "format": existing.get("format"),
                "source_fingerprint": source_hash,
                "report": report,
                "duplicate": True,
            }
        workflow_id = str(uuid.uuid4())
        folder = self._folder(workflow_id)
        manifest = {
            "id": workflow_id,
            "filename": _safe_filename(filename),
            "format": workflow_format,
            "imported_at": _now(),
            "source_fingerprint": source_hash,
            "working_fingerprint": source_hash,
            "confirmed": False,
            "whitelist": [],
            "last_written_at": None,
        }
        try:
            folder.mkdir(parents=True, exist_ok=False)
            _atomic_json(folder / "original.json", workflow)
            _atomic_json(folder / "working.json", workflow)
            _atomic_json(folder / "analysis.json", report)
            _atomic_json(folder / "manifest.json", manifest)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return {
            "id": workflow_id,
            "filename": manifest["filename"],
            "format": workflow_format,
            "source_fingerprint": source_hash,
            "report": report,
        }

    def _find_by_fingerprint(self, source_hash: str) -> dict[str, Any] | None:
        for folder in self.root.iterdir():
            if not folder.is_dir():
                continue
            manifest_file = folder / "manifest.json"
            if not manifest_file.is_file():
                continue
            try:
                manifest = _read_json(manifest_file)
            except WorkflowError:
                continue
            if manifest.get("source_fingerprint") == source_hash:
                return manifest
        return None

    def list_workflows(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for folder in self.root.iterdir():
            if not folder.is_dir():
                continue
            manifest_file = folder / "manifest.json"
            if not manifest_file.is_file():
                continue
            try:
                manifest = _read_json(manifest_file)
            except WorkflowError:
                continue
            items.append({
                "id": manifest.get("id"),
                "filename": manifest.get("filename"),
                "format": manifest.get("format"),
                "imported_at": manifest.get("imported_at"),
                "confirmed": manifest.get("confirmed", False),
            })
        return sorted(
            items,
            key=lambda item: item.get("imported_at") or "",
            reverse=True,
        )
    def inspect_storage(self) -> dict[str, Any]:
        """Read-only conservative inspection of the workflow storage tree.

        Counts corrupt draft/version JSON, corrupt manifests and orphan files
        that do not match the managed layout. This never repairs, deletes or
        rewrites anything; entries are reported by relative path so diagnostics
        can surface them conservatively.
        """
        known_root_files = {
            "manifest.json",
            "original.json",
            "working.json",
            "analysis.json",
        }
        known_root_dirs = {"drafts", "versions"}
        corrupt_manifests: list[str] = []
        corrupt_drafts: list[str] = []
        corrupt_versions: list[str] = []
        orphan_files: list[str] = []
        workflow_folders = 0

        if not self.root.is_dir():
            return {
                "workflow_folders": 0,
                "corrupt_manifest_count": 0,
                "corrupt_manifests": [],
                "corrupt_draft_count": 0,
                "corrupt_drafts": [],
                "corrupt_version_count": 0,
                "corrupt_versions": [],
                "orphan_file_count": 0,
                "orphan_files": [],
            }

        for folder in sorted(self.root.iterdir(), key=lambda item: item.name):
            if not folder.is_dir():
                orphan_files.append(folder.name)
                continue
            workflow_folders += 1
            manifest_file = folder / "manifest.json"
            if not manifest_file.is_file():
                corrupt_manifests.append(f"{folder.name}/manifest.json")
            else:
                try:
                    manifest = _read_json(manifest_file)
                    if not isinstance(manifest, dict):
                        corrupt_manifests.append(f"{folder.name}/manifest.json")
                except WorkflowError:
                    corrupt_manifests.append(f"{folder.name}/manifest.json")

            for entry in sorted(folder.iterdir(), key=lambda item: item.name):
                rel = f"{folder.name}/{entry.name}"
                if entry.is_dir():
                    if entry.name not in known_root_dirs:
                        orphan_files.append(rel)
                    continue
                if entry.name not in known_root_files:
                    orphan_files.append(rel)

            drafts_dir = folder / "drafts"
            if drafts_dir.is_dir():
                for file in sorted(drafts_dir.iterdir(), key=lambda item: item.name):
                    rel = f"{folder.name}/drafts/{file.name}"
                    if file.suffix.lower() != ".json":
                        orphan_files.append(rel)
                        continue
                    try:
                        record = _read_json(file)
                        if not isinstance(record, dict):
                            corrupt_drafts.append(rel)
                    except WorkflowError:
                        corrupt_drafts.append(rel)

            versions_dir = folder / "versions"
            if versions_dir.is_dir():
                for file in sorted(versions_dir.iterdir(), key=lambda item: item.name):
                    rel = f"{folder.name}/versions/{file.name}"
                    if file.suffix.lower() != ".json":
                        orphan_files.append(rel)
                        continue
                    try:
                        record = _read_json(file)
                        if not isinstance(record, dict):
                            corrupt_versions.append(rel)
                    except WorkflowError:
                        corrupt_versions.append(rel)

        return {
            "workflow_folders": workflow_folders,
            "corrupt_manifest_count": len(corrupt_manifests),
            "corrupt_manifests": corrupt_manifests,
            "corrupt_draft_count": len(corrupt_drafts),
            "corrupt_drafts": corrupt_drafts,
            "corrupt_version_count": len(corrupt_versions),
            "corrupt_versions": corrupt_versions,
            "orphan_file_count": len(orphan_files),
            "orphan_files": orphan_files,
        }

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        folder = self._folder(workflow_id)
        if not folder.is_dir():
            raise FileNotFoundError("工作流不存在")
        manifest = _read_json(folder / "manifest.json")
        report = _read_json(folder / "analysis.json")
        original = _read_json(folder / "original.json")
        working = _read_json(folder / "working.json")
        prompts = []
        nodes = (
            _api_nodes(working)
            if manifest.get("format") == "api"
            else {}
        )
        for item in manifest.get("whitelist", []):
            node_id = str(item["node_id"])
            role = item["role"]
            node = nodes.get(node_id, {})
            text = node.get("inputs", {}).get("text", "")
            prompts.append({
                "node_id": node_id,
                "role": role,
                "text": text,
            })
        return {
            "manifest": manifest,
            "report": report,
            "working_prompts": prompts,
            "original_fingerprint": fingerprint(original),
            "working_fingerprint": fingerprint(working),
        }
    def read_working(self, workflow_id: str) -> dict[str, Any]:
        """Return the raw working-copy workflow dict for canvas export."""
        folder = self._folder(workflow_id)
        if not folder.is_dir():
            raise FileNotFoundError("工作流不存在")
        return _read_json(folder / "working.json")

    def reset_working_prompts(self, workflow_id: str) -> dict[str, Any]:
        """Restore the working copy to the imported original.

        Drops any prompt text written this session while keeping the node
        structure intact. This is the manual "clear working copy" action after
        the workflow has been exported to the ComfyUI canvas.
        """
        folder = self._folder(workflow_id)
        if not folder.is_dir():
            raise FileNotFoundError("工作流不存在")
        manifest = _read_json(folder / "manifest.json")
        if manifest.get("format") != "api":
            raise WorkflowError("UI workflow 当前不支持清空工作副本")
        original = _read_json(folder / "original.json")
        restored = copy.deepcopy(original)
        _atomic_json(folder / "working.json", restored)
        manifest["working_fingerprint"] = fingerprint(restored)
        manifest["last_written_at"] = _now()
        _atomic_json(folder / "manifest.json", manifest)
        return self.get_workflow(workflow_id)

    def list_versions(self, workflow_id: str) -> list[dict[str, Any]]:
        """List saved pre-write snapshots under ``versions/``, newest first.

        Read-only: a snapshot exposes only the whitelist Prompt texts captured
        at save time. Corrupt snapshot files are skipped conservatively; no
        restore action is offered from this layer.
        """
        folder = self._folder(workflow_id)
        if not folder.is_dir():
            raise FileNotFoundError("工作流不存在")
        manifest = _read_json(folder / "manifest.json")
        versions_dir = folder / "versions"
        whitelist = manifest.get("whitelist", [])
        entries: list[dict[str, Any]] = []
        if not versions_dir.is_dir():
            return entries
        for file in sorted(versions_dir.glob("*.json"), reverse=True):
            stamp = file.stem
            try:
                snapshot = _read_json(file)
            except WorkflowError:
                continue
            nodes = (
                _api_nodes(snapshot)
                if manifest.get("format") == "api"
                else {}
            )
            prompts: list[dict[str, Any]] = []
            for item in whitelist:
                node_id = str(item.get("node_id", ""))
                node = nodes.get(node_id, {})
                prompts.append(
                    {
                        "node_id": node_id,
                        "role": item.get("role"),
                        "text": node.get("inputs", {}).get("text", ""),
                    }
                )
            entries.append({"stamp": stamp, "prompts": prompts})
        return entries

    def restore_version(self, workflow_id: str, stamp: str) -> dict[str, Any]:
        """Restore whitelist Prompt texts from a saved pre-write snapshot.

        The snapshot is read-only source: its whitelist texts are written back
        through the same guarded ``write_prompts`` path. New workflows no longer
        auto-create version snapshots; this path only restores snapshots that
        already exist in legacy data.
        Missing/corrupt snapshots, non-API workflows, unconfirmed whitelist
        and source-fingerprint drift all fail closed; no draft or multi-draft
        model is introduced here.
        """
        folder = self._folder(workflow_id)
        if not folder.is_dir():
            raise FileNotFoundError("工作流不存在")
        manifest = _read_json(folder / "manifest.json")
        if manifest.get("format") != "api":
            raise WorkflowError("UI workflow 当前不支持安全写入")
        if manifest.get("confirmed") is not True:
            raise WorkflowError("请先确认 Prompt 节点白名单")

        safe_stamp = Path(stamp).name
        if not safe_stamp or safe_stamp != stamp or ".." in safe_stamp:
            raise WorkflowError("无效的版本标识")
        versions_dir = folder / "versions"
        snapshot_path = (versions_dir / f"{safe_stamp}.json").resolve()
        try:
            snapshot_path.relative_to(versions_dir.resolve())
        except ValueError as exc:
            raise WorkflowError("版本快照路径越界") from exc
        if not snapshot_path.is_file():
            raise WorkflowError("指定的版本快照不存在")

        snapshot = _read_json(snapshot_path)
        snapshot_nodes = _api_nodes(snapshot)
        whitelist = manifest.get("whitelist", [])
        positive_text: str | None = None
        negative_text: str | None = None
        for item in whitelist:
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("node_id", ""))
            role = item.get("role")
            node = snapshot_nodes.get(node_id, {})
            text = node.get("inputs", {}).get("text", "")
            if not isinstance(text, str):
                continue
            if role == "positive" and positive_text is None:
                positive_text = text
            elif role == "negative" and negative_text is None:
                negative_text = text

        if positive_text is None and negative_text is None:
            raise WorkflowError("该版本快照没有可恢复的 Prompt 文本")

        return self.write_prompts(
            workflow_id,
            positive_text,
            negative_text,
        )

    def confirm_prompt_nodes(
        self,
        workflow_id: str,
        positive_node_ids: list[str],
        negative_node_ids: list[str],
    ) -> dict[str, Any]:
        folder = self._folder(workflow_id)
        if not folder.is_dir():
            raise FileNotFoundError("工作流不存在")
        manifest = _read_json(folder / "manifest.json")
        if manifest.get("format") != "api":
            raise WorkflowError(
                "当前只允许对 API workflow 确认可写 Prompt 白名单"
            )
        original = _read_json(folder / "original.json")
        report = _read_json(folder / "analysis.json")
        if fingerprint(original) != manifest.get("source_fingerprint"):
            raise WorkflowError("原始工作流指纹变化，必须重新导入")
        candidates = {
            str(item["node_id"])
            for item in report.get("prompt_nodes", [])
            if item.get("editable") is True
        }
        positive = {str(value) for value in positive_node_ids}
        negative = {str(value) for value in negative_node_ids}
        if positive & negative:
            raise WorkflowError(
                "同一个节点不能同时指定为正向和负向 Prompt"
            )
        if not positive and not negative:
            raise WorkflowError(
                "至少确认一个正向或负向 Prompt 节点"
            )
        selected = positive | negative
        if not selected <= candidates:
            raise WorkflowError(
                "选择了未识别或不可编辑的 Prompt 节点"
            )
        nodes = _api_nodes(original)
        whitelist: list[dict[str, Any]] = []
        for role, ids in (
            ("positive", positive),
            ("negative", negative),
        ):
            for node_id in sorted(ids):
                node = nodes.get(node_id)
                inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
                if not isinstance(inputs, dict) or not isinstance(
                    inputs.get("text"), str
                ):
                    raise WorkflowError(
                        f"节点 {node_id} 不存在可写 text 字段"
                    )
                whitelist.append({
                    "node_id": node_id,
                    "role": role,
                    "path": ["inputs", "text"],
                })
        manifest["confirmed"] = True
        manifest["whitelist"] = whitelist
        manifest["confirmed_at"] = _now()
        _atomic_json(folder / "manifest.json", manifest)
        return self.get_workflow(workflow_id)
    def write_prompts(
        self,
        workflow_id: str,
        positive_text: str | None,
        negative_text: str | None,
    ) -> dict[str, Any]:
        folder = self._folder(workflow_id)
        if not folder.is_dir():
            raise FileNotFoundError("工作流不存在")
        manifest = _read_json(folder / "manifest.json")
        if manifest.get("format") != "api":
            raise WorkflowError("UI workflow 当前不支持安全写入")
        if manifest.get("confirmed") is not True:
            raise WorkflowError("请先确认 Prompt 节点白名单")
        for value in (positive_text, negative_text):
            if value is not None and len(value) > MAX_PROMPT_CHARS:
                raise WorkflowError(
                    "Prompt 文本超过 200,000 字符限制"
                )
        if positive_text is None and negative_text is None:
            raise WorkflowError("没有提供需要写入的 Prompt")
        original = _read_json(folder / "original.json")
        working = _read_json(folder / "working.json")
        if fingerprint(original) != manifest.get("source_fingerprint"):
            raise WorkflowError(
                "原始工作流指纹变化，白名单已失效"
            )
        nodes_original = _api_nodes(original)
        nodes_working = _api_nodes(working)
        whitelist = manifest.get("whitelist", [])
        if not isinstance(whitelist, list) or not whitelist:
            raise WorkflowError("Prompt 白名单为空或格式无效")
        allowed: set[str] = set()
        for item in whitelist:
            if (
                not isinstance(item, dict)
                or item.get("role") not in {"positive", "negative"}
                or item.get("path") != ["inputs", "text"]
            ):
                raise WorkflowError("Prompt 白名单结构无效")
            node_id = str(item.get("node_id", ""))
            if node_id in allowed:
                raise WorkflowError("Prompt 白名单存在重复节点")
            allowed.add(node_id)
            if node_id not in nodes_original or node_id not in nodes_working:
                raise WorkflowError("白名单节点已不存在，拒绝写入")
            for nodes in (nodes_original, nodes_working):
                inputs = nodes[node_id].get("inputs")
                if (
                    not isinstance(inputs, dict)
                    or not isinstance(inputs.get("text"), str)
                ):
                    raise WorkflowError("白名单文本字段结构异常")
        # 检查当前工作副本：除白名单 text 字段外，逐节点逐字段必须与原始一致。
        allowed_paths = {f"{node_id}.inputs.text" for node_id in allowed}
        prewrite_diffs = _structure_diff(original, working, allowed_paths)
        if prewrite_diffs:
            raise WorkflowError(
                "工作副本存在非白名单变化，拒绝写入以保护工作流完整性"
            )
        # 在独立副本上写入，绝不直接修改读取到的原始对象。
        updated = copy.deepcopy(working)
        updated_nodes = _api_nodes(updated)
        changes: list[dict[str, Any]] = []
        for item in whitelist:
            node_id = str(item["node_id"])
            role = item["role"]
            replacement = (
                positive_text if role == "positive" else negative_text
            )
            if replacement is None:
                continue
            old_value = updated_nodes[node_id]["inputs"]["text"]
            updated_nodes[node_id]["inputs"]["text"] = replacement
            if old_value != replacement:
                changes.append({
                    "node_id": node_id,
                    "role": role,
                    "old_text": old_value,
                    "new_text": replacement,
                })
        # 写入后再次验证：还原全部白名单文本，结构化比对必须回到原始工作流。
        verified = copy.deepcopy(updated)
        verified_nodes = _api_nodes(verified)
        for node_id in allowed:
            verified_nodes[node_id]["inputs"]["text"] = (
                nodes_original[node_id]["inputs"]["text"]
            )
        postwrite_diffs = _structure_diff(original, verified, allowed_paths)
        if postwrite_diffs:
            raise WorkflowError(
                "写入后完整性校验失败，工作副本未更新"
            )
        # 只有所有验证完成后才替换工作副本。写回前版本快照已退役：
        # 工作副本用完由用户手动清空回退，不再自动留存 versions 快照。
        _atomic_json(folder / "working.json", updated)
        manifest["working_fingerprint"] = fingerprint(updated)
        manifest["last_written_at"] = _now()
        _atomic_json(folder / "manifest.json", manifest)
        return {
            "ok": True,
            "changes": changes,
            "working_fingerprint": manifest["working_fingerprint"],
            "workflow": self.get_workflow(workflow_id),
        }

    # ---- Named drafts (M5, multi-draft per workflow) ---------------------
    #
    # A draft is a user-saved Prompt combination kept apart from the single
    # working copy and the automatic ``versions/`` snapshots. Saving a draft
    # never touches ``working.json`` and never writes a version snapshot;
    # adopting a draft back into the working copy still goes through the
    # guarded ``write_prompts`` path.

    def _drafts_dir(self, workflow_id: str) -> Path:
        folder = self._folder(workflow_id)
        if not folder.is_dir():
            raise FileNotFoundError("工作流不存在")
        drafts_dir = (folder / "drafts").resolve()
        try:
            drafts_dir.relative_to(folder.resolve())
        except ValueError as exc:
            raise WorkflowError("草稿目录越界") from exc
        return drafts_dir

    def _draft_path(self, drafts_dir: Path, draft_id: str) -> Path:
        try:
            safe_id = str(uuid.UUID(str(draft_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise WorkflowError("无效的草稿标识") from exc
        path = (drafts_dir / f"{safe_id}.json").resolve()
        try:
            path.relative_to(drafts_dir)
        except ValueError as exc:
            raise WorkflowError("草稿路径越界") from exc
        return path

    def _read_draft_file(self, path: Path) -> dict[str, Any]:
        record = _read_json(path)
        if not isinstance(record, dict):
            raise WorkflowError("草稿记录结构无效")
        return record

    def list_drafts(
        self,
        workflow_id: str,
        include_trashed: bool = False,
    ) -> list[dict[str, Any]]:
        """List named drafts for one workflow, newest update first.

        Corrupt draft files are skipped conservatively; the list exposes the
        whitelist Prompt texts and note, not the working copy. Soft-deleted
        drafts are hidden unless ``include_trashed`` is set.
        """
        drafts_dir = self._drafts_dir(workflow_id)
        entries: list[dict[str, Any]] = []
        if not drafts_dir.is_dir():
            return entries
        for file in drafts_dir.glob("*.json"):
            try:
                record = self._read_draft_file(file)
            except WorkflowError:
                continue
            trashed = bool(record.get("trashed") or False)
            if trashed and not include_trashed:
                continue
            entries.append(
                {
                    "id": record.get("id") or file.stem,
                    "name": record.get("name") or "未命名草稿",
                    "note": record.get("note") or "",
                    "positive": record.get("positive") or "",
                    "negative": record.get("negative") or "",
                    "favored": bool(record.get("favored") or False),
                    "pinned": bool(record.get("pinned") or False),
                    "tags": _clean_tags(record.get("tags")),
                    "trashed": trashed,
                    "source_history_id": _clean_source_ref(
                        record.get("source_history_id")
                    ),
                    "env_snapshot": _clean_env_snapshot(
                        record.get("env_snapshot")
                    ),
                    "created_at": record.get("created_at"),
                    "updated_at": record.get("updated_at"),
                }
            )
        # Stable two-pass sort: newest update first within each group, then
        # pinned drafts float to the top ahead of unpinned ones.
        entries.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        entries.sort(key=lambda item: 0 if item["pinned"] else 1)
        return entries

    def rename_tag(self, old_tag: str, new_tag: str) -> dict[str, Any]:
        """Rename one draft tag to another across every workflow draft box.

        Drafts under both live and trashed boxes are covered; matching tags are
        replaced, then the whitelist clean and de-duplicate pass runs again so a
        merged tag does not appear twice. Other draft content is untouched.
        """
        old = _clean_tag(old_tag)
        new = _clean_tag(new_tag)
        if old is None:
            raise WorkflowError("原标签不能为空")
        if new is None:
            raise WorkflowError("新标签不能为空")
        if old == new:
            raise WorkflowError("新标签与原标签相同")

        renamed = 0
        for folder in self.root.iterdir():
            if not folder.is_dir():
                continue
            drafts_dir = folder / "drafts"
            if not drafts_dir.is_dir():
                continue
            for file in drafts_dir.glob("*.json"):
                try:
                    record = self._read_draft_file(file)
                except WorkflowError:
                    continue
                raw_tags = record.get("tags")
                if not isinstance(raw_tags, list) or old not in [
                    tag.strip() for tag in raw_tags if isinstance(tag, str)
                ]:
                    continue
                rebuilt: list[str] = []
                seen: set[str] = set()
                for item in raw_tags:
                    candidate = _clean_tag(new if item.strip() == old else item)
                    if candidate is None or candidate in seen:
                        continue
                    seen.add(candidate)
                    rebuilt.append(candidate)
                record["tags"] = rebuilt
                record["updated_at"] = _now()
                _atomic_json(file, record)
                renamed += 1

        return {"renamed": renamed, "old": old, "new": new}

    def save_draft(
        self,
        workflow_id: str,
        draft_id: str | None,
        name: str,
        positive: str | None,
        negative: str | None,
        note: str | None,
        tags: list[str] | None = None,
        source_history_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a named draft under this workflow's draft box.

        An existing ``draft_id`` updates that draft; otherwise a new draft is
        created. Duplicate names within the same workflow are refused. The
        working copy and version snapshots are left untouched.
        """
        drafts_dir = self._drafts_dir(workflow_id)
        drafts_dir.mkdir(parents=True, exist_ok=True)

        clean_name = (name or "").strip()
        if not clean_name:
            raise WorkflowError("草稿名称不能为空")
        if len(clean_name) > MAX_DRAFT_NAME_CHARS:
            raise WorkflowError("草稿名称超过 120 字符限制")
        clean_positive = positive or ""
        clean_negative = negative or ""
        if len(clean_positive) > MAX_PROMPT_CHARS or len(clean_negative) > MAX_PROMPT_CHARS:
            raise WorkflowError("Prompt 文本超过 200,000 字符限制")
        if not clean_positive.strip() and not clean_negative.strip():
            raise WorkflowError("草稿至少需要一段正向或负向 Prompt")
        clean_note = (note or "").strip()
        if len(clean_note) > MAX_DRAFT_NOTE_CHARS:
            raise WorkflowError("草稿备注超过 1000 字符限制")
        clean_tags = _clean_tags(tags)
        clean_source = _clean_source_ref(source_history_id)

        existing = {
            entry["id"]: entry
            for entry in self.list_drafts(workflow_id)
        }

        if draft_id:
            path = self._draft_path(drafts_dir, draft_id)
            if not path.is_file():
                raise WorkflowError("指定的草稿不存在")
            record = self._read_draft_file(path)
            record["name"] = clean_name
            record["positive"] = clean_positive
            record["negative"] = clean_negative
            record["note"] = clean_note
            record["tags"] = clean_tags
            record["source_history_id"] = clean_source
            # The environment snapshot is frozen at first save; a later prompt
            # edit does not re-freeze it from the current working copy.
            record["env_snapshot"] = (
                _clean_env_snapshot(record.get("env_snapshot"))
                or _extract_env_snapshot(None)
            )
            record.setdefault("favored", False)
            record.setdefault("pinned", False)
            record["updated_at"] = _now()
            record.setdefault("created_at", record.get("updated_at"))
        else:
            safe_id = str(uuid.uuid4())
            path = drafts_dir / f"{safe_id}.json"
            record = {
                "id": safe_id,
                "name": clean_name,
                "positive": clean_positive,
                "negative": clean_negative,
                "note": clean_note,
                "tags": clean_tags,
                "source_history_id": clean_source,
                "env_snapshot": self._current_env_snapshot(workflow_id),
                "favored": False,
                "pinned": False,
                "trashed": False,
                "created_at": _now(),
                "updated_at": _now(),
            }

        for other in existing.values():
            if other["id"] != record["id"] and other["name"] == clean_name:
                raise WorkflowError("已存在同名草稿，请更换名称")

        _atomic_json(path, record)
        return record

    def get_draft(self, workflow_id: str, draft_id: str) -> dict[str, Any]:
        drafts_dir = self._drafts_dir(workflow_id)
        path = self._draft_path(drafts_dir, draft_id)
        if not path.is_file():
            raise WorkflowError("指定的草稿不存在")
        return self._read_draft_file(path)

    def _current_env_snapshot(self, workflow_id: str) -> dict[str, Any]:
        """Read the current workflow report and extract a frozen env snapshot.

        A missing or unreadable report degrades to an empty snapshot rather
        than failing the draft save.
        """
        try:
            workflow = self.get_workflow(workflow_id)
            return _extract_env_snapshot(workflow.get("report"))
        except (WorkflowError, FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return _extract_env_snapshot(None)

    def find_linked_draft(
        self, workflow_id: str, source_history_id: str
    ) -> dict[str, Any] | None:
        """Return the active draft linked to one history record, if any.

        An unbound record (``workflow_id`` empty, e.g. a canvas-direct
        generation) has no draft folder to resolve, so it links to nothing.
        """
        if not str(workflow_id or "").strip():
            return None
        ref = _clean_source_ref(source_history_id)
        if not ref:
            return None
        for entry in self.list_drafts(workflow_id):
            if entry.get("source_history_id") == ref:
                return {"id": entry["id"], "name": entry["name"]}
        return None

    def delete_draft(self, workflow_id: str, draft_id: str) -> dict[str, Any]:
        """Soft-delete: move the draft to the recycle bin; recoverable via restore_draft.

        The record file is kept on disk; generation history and images are
        untouched.
        """
        drafts_dir = self._drafts_dir(workflow_id)
        path = self._draft_path(drafts_dir, draft_id)
        if not path.is_file():
            raise WorkflowError("指定的草稿不存在")
        record = self._read_draft_file(path)
        record["trashed"] = True
        _atomic_json(path, record)
        return {"ok": True, "id": draft_id, "trashed": True}

    def restore_draft(self, workflow_id: str, draft_id: str) -> dict[str, Any]:
        """Recover a soft-deleted draft back into the active draft box."""
        drafts_dir = self._drafts_dir(workflow_id)
        path = self._draft_path(drafts_dir, draft_id)
        if not path.is_file():
            raise WorkflowError("指定的草稿不存在")
        record = self._read_draft_file(path)
        record["trashed"] = False
        _atomic_json(path, record)
        return record

    def purge_draft(self, workflow_id: str, draft_id: str) -> dict[str, Any]:
        """Permanently remove a draft from the recycle bin. Irreversible."""
        drafts_dir = self._drafts_dir(workflow_id)
        path = self._draft_path(drafts_dir, draft_id)
        if not path.is_file():
            raise WorkflowError("指定的草稿不存在")
        path.unlink()
        return {"ok": True, "id": draft_id, "purged": True}

    def toggle_favored(self, workflow_id: str, draft_id: str) -> dict[str, Any]:
        """Flip the favored flag on one draft.

        Favored is a reversible marker only; it does not reorder the list and
        does not bump ``updated_at``. The working copy and snapshots are
        untouched.
        """
        drafts_dir = self._drafts_dir(workflow_id)
        path = self._draft_path(drafts_dir, draft_id)
        if not path.is_file():
            raise WorkflowError("指定的草稿不存在")
        record = self._read_draft_file(path)
        record["favored"] = not bool(record.get("favored") or False)
        _atomic_json(path, record)
        return record

    def toggle_pinned(self, workflow_id: str, draft_id: str) -> dict[str, Any]:
        """Flip the pinned flag on one draft.

        Pinned drafts sort ahead of unpinned ones; the marker is reversible and
        does not bump ``updated_at``. The working copy and snapshots are
        untouched.
        """
        drafts_dir = self._drafts_dir(workflow_id)
        path = self._draft_path(drafts_dir, draft_id)
        if not path.is_file():
            raise WorkflowError("指定的草稿不存在")
        record = self._read_draft_file(path)
        record["pinned"] = not bool(record.get("pinned") or False)
        _atomic_json(path, record)
        return record
