"""Local-LLM prompt candidate node: ask the workbench backend to produce one
prompt candidate for an imported workflow, straight from the canvas.

The backend resolves the rule context, existing prompts and protected terms
server-side for the given ``workflow_id``; this node does not hold that context
itself. The suggestion is returned to the canvas but never written back to a
draft, rule or setting. Draft persistence stays in the workbench web app.
"""

from __future__ import annotations

import json
from typing import Any

import backend_client

MODES = ["draft", "polish", "trim", "bilingual", "negative"]

MODE_LABELS = {
    "draft": "起草补全",
    "polish": "润色",
    "trim": "精简",
    "bilingual": "中英对照",
    "negative": "负向补全",
}

# Sentinel meaning "option not set"; the backend omits unset sampling options.
_UNSET = -1.0


class WorkbenchPromptCandidate:
    """Produce one local-LLM prompt candidate for a workflow via the backend."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # Combo seeded with an empty placeholder; the extension JS
                # fills the real workflow options from the backend list once the
                # canvas loads. Free-text entry stays possible as a fallback.
                "workflow_id": ([""], {"default": ""}),
                # Combo seeded with an empty placeholder; the extension JS fills
                # the local Ollama model names from the backend list once the
                # canvas loads. Free-text entry stays possible as a fallback.
                "model": ([""], {"default": ""}),
                "mode": (MODES, {"default": "draft"}),
                "task": ("STRING", {"default": "", "multiline": True}),
                "backend_url": ("STRING", {"default": ""}),
            },
            "optional": {
                "token": ("STRING", {"default": ""}),
                # "auto" lets the backend match a local Profile by checkpoint
                # / architecture; otherwise pick a backend Profile id from the
                # combo filled by the extension JS.
                "prompt_profile": (["auto"], {"default": "auto"}),
                "temperature": ("FLOAT", {"default": _UNSET, "min": -1.0, "max": 2.0, "step": 0.05}),
                "top_p": ("FLOAT", {"default": _UNSET, "min": -1.0, "max": 1.0, "step": 0.05}),
                "num_ctx": ("INT", {"default": -1, "min": -1, "max": 131_072}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("candidate_text", "raw_json")
    FUNCTION = "generate"
    CATEGORY = "Workbench/LLM"

    def generate(
        self,
        workflow_id,
        model,
        mode,
        task,
        backend_url,
        token="",
        temperature=_UNSET,
        top_p=_UNSET,
        num_ctx=-1,
        prompt_profile="auto",
    ):
        workflow_id = (workflow_id or "").strip()
        model = (model or "").strip()
        if not workflow_id:
            return ("[workbench] 未填写 workflow_id；可在工作台工作流页复制。", "{}")
        if not model:
            return ("[workbench] 未填写本地 Ollama 模型名。", "{}")

        body: dict[str, Any] = {"model": model, "mode": mode}
        if (task or "").strip():
            body["task"] = task.strip()
        profile_choice = (prompt_profile or "").strip()
        if profile_choice and profile_choice != "auto":
            body["prompt_profile"] = profile_choice
        if temperature >= 0:
            body["temperature"] = temperature
        if top_p >= 0:
            body["top_p"] = top_p
        if num_ctx >= 0:
            body["num_ctx"] = num_ctx

        data = backend_client.prompt_candidates(workflow_id, body, backend_url, token)
        if "error" in data:
            return (
                f"[workbench] 候选生成失败：{data['error']}",
                json.dumps(data, ensure_ascii=False),
            )

        candidates = data.get("candidates") or []
        if not candidates:
            detail = data.get("detail") or data.get("error") or "模型未返回候选"
            return (f"[workbench] 未生成候选：{detail}", json.dumps(data, ensure_ascii=False))

        mode_label = MODE_LABELS.get(mode, mode)
        header = f"[{mode_label}｜模型 {data.get('model') or model}]"
        profile_name = data.get("selected_profile_name")
        if profile_name:
            source = data.get("profile_match_source") or ""
            header += f"｜Profile {profile_name}"
            if source:
                header += f"（{source}）"
        elif data.get("fallback_reason"):
            header += "｜Profile 回退通用规则"
        return (f"{header}\n{candidates[0]}", json.dumps(data, ensure_ascii=False))


NODE_CLASS_MAPPINGS = {"WorkbenchPromptCandidate": WorkbenchPromptCandidate}
NODE_DISPLAY_NAME_MAPPINGS = {"WorkbenchPromptCandidate": "工作台提示词候选生成"}
