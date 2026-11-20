"""Read-only rule-context node: surface workbench rule information on canvas.

This node merges the two former read-only nodes:

* ``workflow`` source: pull the resolved rule context for one imported workflow
  (status / resolved fields / conflicts / unknowns / instruction).
* ``rules`` source: read the stored rule store for one scope level and return it
  as plain text.

Both modes only read the backend; rule editing, draft persistence and prompt
writing stay in the workbench web app.
"""

from __future__ import annotations

import json
from typing import Any

import backend_client

LEVELS = ["checkpoint", "architecture", "global"]
SOURCES = ["workflow", "rules"]

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


def _render_workflow_context(data: dict[str, Any]) -> tuple[str, str]:
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


def _render_rules(data: dict[str, Any]) -> tuple[str, str]:
    items = data.get("items", [])
    lines: list[str] = []
    for item in items:
        key = item.get("rule_key", "")
        value = item.get("value", "")
        scope = item.get("scope_key") or "-"
        note = item.get("note") or ""
        line = f"- [{item.get('level', '')}/{scope}] {key}: {value}"
        if note:
            line += f"（{note}）"
        lines.append(line)

    text = "\n".join(lines) if lines else "（当前层级无规则）"
    return (text, json.dumps(data, ensure_ascii=False))


class WorkbenchRuleContext:
    """Fetch rule context for one workflow, or the rule store for one level."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source": (SOURCES, {"default": "workflow"}),
                # Free-text workflow id. The real options live on the workbench
                # backend; a combo would be validated against a static placeholder
                # list, so this stays a plain string.
                "workflow_id": ("STRING", {"default": ""}),
                "level": (LEVELS, {"default": "global"}),
                "scope_key": ("STRING", {"default": "", "multiline": False}),
                "backend_url": ("STRING", {"default": ""}),
            },
            "optional": {
                "token": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("context_text", "raw_json")
    FUNCTION = "load"
    CATEGORY = "Workbench/Context"

    def load(
        self,
        source,
        workflow_id,
        level,
        scope_key,
        backend_url,
        token="",
    ):
        if source == "rules":
            data = backend_client.list_rules(level, scope_key, backend_url, token)
            if "error" in data:
                return (f"[workbench] 无法读取规则：{data['error']}", "{}")
            return _render_rules(data)

        workflow_id = (workflow_id or "").strip()
        if not workflow_id:
            return ("[workbench] 未填写 workflow_id；可在工作台工作流页复制。", "{}")

        data = backend_client.workflow_rule_prompt(workflow_id, backend_url, token)
        if "error" in data:
            return (f"[workbench] 无法读取规则上下文：{data['error']}", "{}")
        return _render_workflow_context(data)


NODE_CLASS_MAPPINGS = {"WorkbenchRuleContext": WorkbenchRuleContext}
NODE_DISPLAY_NAME_MAPPINGS = {"WorkbenchRuleContext": "工作台规则上下文"}
