"""Local-LLM prompt candidate node: ask the workbench backend to produce one
prompt candidate, straight from the canvas.

This node is workflow-unbound by design: it sits inside the ComfyUI graph and
targets the canvas directly, so it does not take a backend ``workflow_id`` and
does not pull workflow rules. The caller picks three layers on the node itself:
a real local Ollama ``model``, an LLM ``persona`` (identity / system prompt) and
a ``prompt_profile`` (model prompt rules); ``genre`` / ``style`` remain as
optional writing-class layers. The suggestion is returned to the canvas but
never written back to a draft, rule or setting.

Identity model (see ``THREE_SLICE_SPEC.md``):

- ``persona`` selects the LLM identity layer; empty / ``none`` means bare
  model (no system identity). ``genre`` / ``style`` are writing-class layers.
- The node exposes five outputs: ``positive_text`` / ``negative_text`` carry
  the human-readable header plus the prompt body; ``raw_json`` carries the
  backend payload; ``clean_positive`` / ``clean_negative`` carry only the
  prompt body so they can be wired straight into a CLIPTextEncode. A writing /
  bare-model result lands in the positive ports; a negative-mode result lands
  in the negative ports.
"""

from __future__ import annotations

import json
from typing import Any

import backend_client

# Legacy English slugs kept only to label old saved graphs; the real mode list
# lives on the workbench backend (prompt_profiles/modes/*.txt) and is filled
# into the combo by the extension JS. New canonical ids are Chinese file
# stems and pass through unchanged.
MODE_LABELS = {
    "draft": "起草",
    "polish": "润色",
    "trim": "精简",
    "continue": "续写",
    "fresh": "空白直出",
    "bilingual": "中英对照",
    "negative": "负向词",
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
                # Real local Ollama model name. The combo is seeded with an
                # empty placeholder; the extension JS fills the real list from
                # /api/ollama/models, and VALIDATE_INPUTS opts this field out
                # of the static enum check below.
                "model": ([""], {"default": ""}),
                "mode": ([""], {"default": ""}),
                "task": ("STRING", {"default": "", "multiline": True}),
                "backend_url": ("STRING", {"default": ""}),
            },
            "optional": {
                "token": ("STRING", {"default": ""}),
                # Identity layer. Empty / "none" = bare model, no identity
                # system prompt; other values resolve a registered persona.
                "persona": (["none"], {"default": "none"}),
                # Model prompt rules: "auto" picks a default Profile (without a
                # bound workflow it falls back to the Core rules), "none" skips
                # the layer, otherwise a registered Profile id.
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

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "positive_text",
        "negative_text",
        "raw_json",
        "clean_positive",
        "clean_negative",
    )
    FUNCTION = "generate"
    CATEGORY = "Workbench/LLM"

    @classmethod
    def VALIDATE_INPUTS(
        cls,
        model=None,
        mode=None,
        persona=None,
        prompt_profile=None,
        genre=None,
        style=None,
    ):
        """Let the dynamic picker fields accept backend-filled combo values.

        The real option lists live on the workbench backend and are filled into
        the combo widgets by the extension JS after the canvas loads, so the
        static Python placeholders above can never enumerate them. ComfyUI
        skips its default enum/min/max check for every input named in this
        method's signature, so returning ``True`` keeps the picker fields
        free-form.

        Every parameter carries a default: graphs saved before ``genre`` /
        ``style`` / ``mode`` existed simply do not pass them, and ComfyUI calls
        this hook with only the inputs the instance actually has.
        """
        return True

    def generate(
        self,
        model,
        mode,
        task,
        backend_url,
        token="",
        persona="none",
        prompt_profile="auto",
        genre="",
        style="",
        positive_in="",
        negative_in="",
        temperature=_UNSET,
        top_p=_UNSET,
        num_ctx=-1,
    ):
        model = _clean(model)
        if not model:
            return (
                "[workbench] 未填写本地 Ollama 模型名。",
                "",
                "{}",
                "",
                "",
            )

        body: dict[str, Any] = {"model": model}

        mode_value = _clean(mode)
        # Empty / "none" omits the slot so the backend falls back to its
        # default draft mode.
        if mode_value and mode_value != _NONE:
            body["mode"] = mode_value

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

        # The node is workflow-unbound: always hit the standalone candidate
        # route (empty workflow id) so the three pickers drive the request.
        data = backend_client.prompt_candidates("", body, backend_url, token)
        if "error" in data:
            return (
                f"[workbench] 候选生成失败：{data['error']}",
                "",
                json.dumps(data, ensure_ascii=False),
                "",
                "",
            )

        candidates = data.get("candidates") or []
        if not candidates:
            detail = data.get("detail") or data.get("error") or "模型未返回候选"
            return (
                f"[workbench] 未生成候选：{detail}",
                "",
                json.dumps(data, ensure_ascii=False),
                "",
                "",
            )

        mode_label = MODE_LABELS.get(mode_value, mode_value)
        if not mode_label:
            mode_label = "起草"
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
        body_text = candidates[0]
        raw = json.dumps(data, ensure_ascii=False)
        # Negative-mode output goes to the negative ports; everything else to
        # the positive ports. The backend flags the mode's output side via
        # ``mode_negative_output``; the legacy slug ``negative`` is kept as a
        # fallback for old saved graphs. The *_text ports keep the
        # human-readable header; the clean_* ports carry only the prompt body,
        # never a header or an error message, so they are safe to wire into a
        # CLIPTextEncode or copy back for a workbench safe write.
        is_negative = bool(data.get("mode_negative_output")) or mode_value == "negative"
        if is_negative:
            positive_text, negative_text = "", candidate
            clean_positive, clean_negative = "", body_text
        else:
            positive_text, negative_text = candidate, ""
            clean_positive, clean_negative = body_text, ""
        return (
            positive_text,
            negative_text,
            raw,
            clean_positive,
            clean_negative,
        )


NODE_CLASS_MAPPINGS = {"WorkbenchPromptCandidate": WorkbenchPromptCandidate}
NODE_DISPLAY_NAME_MAPPINGS = {"WorkbenchPromptCandidate": "工作台提示词候选生成"}
