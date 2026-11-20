"""Release-VRAM node: unload the resident local LLM and free ComfyUI VRAM.

This node calls the backend's existing best-effort release chain before an
image prompt runs. It only triggers the narrow, idempotent release endpoint
that the node token is allowed to call; draft/rule/settings mutations stay
outside the node surface.
"""

from __future__ import annotations

import json

import backend_client


class WorkbenchReleaseVram:
    """Unload resident Ollama models and free ComfyUI model VRAM via the backend."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": True}),
                "backend_url": ("STRING", {"default": ""}),
            },
            "optional": {
                "token": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("status_text", "raw_json")
    FUNCTION = "release"
    CATEGORY = "Workbench/System"

    def release(self, enabled, backend_url, token=""):
        if not enabled:
            return ("[workbench] 已跳过卸载（节点未启用）", "{}")

        data = backend_client.release_vram(backend_url, token)

        if "error" in data:
            return (f"[workbench] 卸载失败：{data['error']}", "{}")

        lines: list[str] = []

        if not data.get("ollama_reachable", False):
            lines.append("Ollama 不可达，跳过 LLM 卸载")
        else:
            stopped = data.get("ollama_stopped", [])
            if stopped:
                lines.append(f"已卸载 LLM：{', '.join(stopped)}")
            still = data.get("ollama_still_resident", [])
            if still:
                lines.append(f"仍驻留 LLM：{', '.join(still)}")
            for err in data.get("ollama_errors", []):
                lines.append(f"LLM 卸载异常：{err}")

        if data.get("comfyui_freed", False):
            lines.append("ComfyUI 模型显存已释放")
        elif data.get("comfyui_error"):
            lines.append(f"ComfyUI 释放异常：{data['comfyui_error']}")
        else:
            lines.append("ComfyUI 释放状态未知")

        text = "\n".join(lines) if lines else "卸载链路已执行"
        return (text, json.dumps(data, ensure_ascii=False))


NODE_CLASS_MAPPINGS = {"WorkbenchReleaseVram": WorkbenchReleaseVram}
NODE_DISPLAY_NAME_MAPPINGS = {"WorkbenchReleaseVram": "工作台释放显存/卸载LLM"}
