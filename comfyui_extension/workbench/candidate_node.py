"""Local-LLM prompt candidate node: ask the workbench backend to produce one
prompt candidate, straight from the canvas.

The backend resolves the rule context, existing prompts and protected terms
server-side; this node does not hold that context itself. The suggestion is
returned to the canvas but never written back to a draft, rule or setting.
Draft persistence stays in the workbench web app.

Identity model (see ``THREE_SLICE_SPEC.md``):

- ``persona`` selects the LLM identity layer; empty / ``none`` means bare
  model (no system identity). ``genre`` / ``style`` are writing-class layers.
- ``workflow_id`` is optional: leave it empty for a standalone bare-model or
  writing round. When a workflow is bound the backend pre-selects a default
  rule profile, but the profile slot still has its own three states.
- The node exposes three outputs: ``positive_text``, ``negative_text`` and
  ``raw_json``. A writing / bare-model result lands in ``positive_text``; a
  negative-mode result lands in ``negative_text``.
"""

from __future__ import annotations

import json
from typing import Any

import backend_client

MODES = ["draft", "polish", "trim", "bilingual", "negative", "none"]

MODE_LABELS = {
    "draft": "起草补全",
    "polish": "润色",
    "trim": "精简",
    "bilingual": "中英对照",
    "negative": "负向补全",
    "none": "无",
}

# Sentinel meaning "option not set"; the backend omits unset sampling options.
_UNSET = -1.0
# Sentinel meaning an optional text slot was deliberately left empty.
_NONE = "none"


def _clean(value: Any) -> str:
    return str(value or "").strip()


class WorkbenchPromptCandidate:
    """Produce one local-LLM prompt candidate via the backend."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # Combo seeded with an empty placeholder; the extension JS fills
                # the real workflow options from the backend list once the canvas
                # loads. Empty means a standalone round (no bound workflow).
                "workflow_id": ([""], {"default": ""}),
                # Combo seeded with an empty placeholder; the extension JS fills
                # the local Ollama model names from the backend list.
                "model": ([""], {"default": ""}),
                "mode": (MODES, {"default": "draft"}),
                "task": ("STRING", {"default": "", "multiline": True}),
                "backend_url": ("STRING", {"default": ""}),
            },
            "optional": {
                "token": ("STRING", {"default": ""}),
                # Identity layer; the extension JS fills registered personas.
                # Empty / "none" = bare model, no identity system prompt.
                "persona": ([""], {"default": ""}),
                # "auto" matches a local Profile by checkpoint / architecture;
                # "" / "none" skips the profile layer; otherwise pick a backend
                # Profile id from the combo filled by the extension JS.
                "prompt_profile": (["auto"], {"default": "auto"}),
                # Writing-class layers; ignored by the backend for prompt-class
                # identities. Empty means the layer is not injected.
                "genre": ([""], {"default": ""}),
                "style": ([""], {"default": ""}),
                # Existing prompts optionally fed into the candidate round.
                "positive_in": ("STRING", {"default": "", "multiline": True}),
                "negative_in": ("STRING", {"default": "", "multiline": True}),
                "temperature": ("FLOAT", {"default": _UNSET, "min": -1.0, "max": 2.0, "step": 0.05}),
                "top_p": ("FLOAT", {"default": _UNSET, "min": -1.0, "max": 1.0, "step": 0.05}),
                "num_ctx": ("INT", {"default": -1, "min": -1, "max": 131_072}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive_text", "negative_text", "raw_json")
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
        persona="",
        prompt_profile="auto",
        genre="",
        style="",
        positive_in="",
        negative_in="",
        temperature=_UNSET,
        top_p=_UNSET,
        num_ctx=-1,
    ):
        workflow_id = _clean(workflow_id)
        model = _clean(model)
        if not model:
            return (
                "[workbench] 未填写本地 Ollama 模型名。",
                "",
                "{}",
            )

        body: dict[str, Any] = {"model": model}

        mode_value = _clean(mode)
        body["mode"] = _NONE if mode_value in ("", _NONE) else mode_value

        if _clean(task):
            body["task"] = _clean(task)

        # Identity slot: empty / none = bare model (explicitly sent so the
        # backend does not fall back to the legacy Core generic rules).
        persona_value = _clean(persona)
        body["persona"] = _NONE if not persona_value else persona_value

        # Profile three states: auto (default, omitted), explicit id, skip.
        profile_choice = _clean(prompt_profile)
        if profile_choice in ("", _NONE):
            body["prompt_profile"] = _NONE
        elif profile_choice != "auto":
            body["prompt_profile"] = profile_choice

        if _clean(genre):
            body["genre"] = _clean(genre)
        if _clean(style):
            body["style"] = _clean(style)
        if _clean(positive_in):
            body["positive_in"] = _clean(positive_in)
        if _clean(negative_in):
            body["negative_in"] = _clean(negative_in)

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
                "",
                json.dumps(data, ensure_ascii=False),
            )

        candidates = data.get("candidates") or []
        if not candidates:
            detail = data.get("detail") or data.get("error") or "模型未返回候选"
            return (
                f"[workbench] 未生成候选：{detail}",
                "",
                json.dumps(data, ensure_ascii=False),
            )

        mode_label = MODE_LABELS.get(mode_value, mode_value)
        header = f"[{mode_label}｜模型 {data.get('model') or model}]"

        persona_name = data.get("persona_name")
        if persona_name:
            header += f"｜身份 {persona_name}"
        profile_name = data.get("selected_profile_name")
        if profile_name:
            source = data.get("profile_match_source") or ""
            header += f"｜Profile {profile_name}"
            if source:
                header += f"（{source}）"
        elif data.get("fallback_reason"):
            header += "｜Profile 回退通用规则"
        if data.get("genre"):
            header += f"｜题材 {data['genre']}"
        if data.get("style"):
            header += f"｜文风 {data['style']}"

        candidate = f"{header}\n{candidates[0]}"
        # Negative-mode output goes to the negative port; everything else to
        # the positive port. The negative port stays empty for writing/bare
        # rounds.
        if mode_value == "negative":
            positive_text, negative_text = "", candidate
        else:
            positive_text, negative_text = candidate, ""
        return (
            positive_text,
            negative_text,
            json.dumps(data, ensure_ascii=False),
        )


NODE_CLASS_MAPPINGS = {"WorkbenchPromptCandidate": WorkbenchPromptCandidate}
NODE_DISPLAY_NAME_MAPPINGS = {"WorkbenchPromptCandidate": "工作台提示词候选生成"}
