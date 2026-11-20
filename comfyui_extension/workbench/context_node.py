"""Read-only workflow rule-context node: surfaces the resolved rule context
for one imported workbench workflow onto the canvas.

The node only reads the backend's LLM-ready rule projection (`/rules/prompt`).
Rule editing, draft persistence and prompt writing stay in the workbench web
app. The ``workflow_id`` identifies which imported workflow's context to pull;
the backend resolves checkpoint/architecture/global inheritance server-side.
"""

from __future__ import annotations

import json
from typing import Any

import backend_client

STATUS_LABELS = {
    "registered": "已登记",
    "unregistered": "未登记",
    "conflict": "存在冲突",
    "unknown": "未覆盖",
}


def _field_line(entry: dict[str, Any]) -> str:
    key = entry.get("key", "")
    status = entry.get("status", "")
    value = entry.get("value")
    source = entry.get("source") or entry.get("source_level") or ""
    status_label = STATUS_LABELS.get(status, status)
    if status == "resolved":
        return f"- [{status_label}] {key}: {value}（来源：{source}）"
    if status == "conflict":
        message = entry.get("message") or "同级别规则冲突，需人工裁决"
        return f"- [{status_label}] {key}: {message}"
    return f"- [{status_label}] {key}"


class WorkbenchWorkflowContext:
    """Fetch the resolved rule context for one workflow and expose it as text."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # Combo seeded with an empty placeholder; the extension JS
                # fills the real workflow options from the backend list once the
                # canvas loads. Free-text entry stays possible as a fallback.
                "workflow_id": ([""], {"default": ""}),
                "backend_url": ("STRING", {"default": ""}),
            },
            "optional": {
                "token": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("context_text", "raw_json")
    FUNCTION = "load_context"
    CATEGORY = "Workbench/Context"

    def load_context(self, workflow_id, backend_url, token=""):
        workflow_id = (workflow_id or "").strip()
        if not workflow_id:
            return ("[workbench] 未填写 workflow_id；可在工作台工作流页复制。", "{}")

        data = backend_client.workflow_rule_prompt(workflow_id, backend_url, token)
        if "error" in data:
            return (f"[workbench] 无法读取规则上下文：{data['error']}", "{}")

        status = data.get("status", "")
        status_label = STATUS_LABELS.get(status, status)
        checkpoint = data.get("checkpoint") or "-"
        architecture = data.get("architecture_label") or data.get("architecture") or "-"

        lines = [f"状态：{status_label}｜底模：{checkpoint}｜架构：{architecture}"]

        fields = data.get("fields") or []
        resolved = [f for f in fields if f.get("status") == "resolved"]
        conflicts = [f for f in fields if f.get("status") == "conflict"]
        unknowns = [f for f in fields if f.get("status") == "unknown"]

        if resolved:
            lines.append("已解析规则：")
            lines.extend(_field_line(f) for f in resolved)
        if conflicts:
            lines.append("待裁决冲突：")
            lines.extend(_field_line(f) for f in conflicts)
        if unknowns:
            lines.append("未覆盖字段：" + "、".join(f.get("key", "") for f in unknowns))

        instruction = data.get("instruction")
        if instruction:
            lines.append(f"口径：{instruction}")

        if not fields and status != "registered":
            lines.append("（当前工作流未在规则库登记；不得依据模型名臆造参数或风格。）")

        return ("\n".join(lines), json.dumps(data, ensure_ascii=False))


NODE_CLASS_MAPPINGS = {"WorkbenchWorkflowContext": WorkbenchWorkflowContext}
NODE_DISPLAY_NAME_MAPPINGS = {"WorkbenchWorkflowContext": "工作台工作流规则上下文"}
