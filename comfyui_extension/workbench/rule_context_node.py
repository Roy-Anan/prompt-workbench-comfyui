"""Read-only rule-list node: browse the workbench rule store on canvas.

This node is workflow-unbound: it no longer takes a ``workflow_id`` and no
longer pulls the resolved context of one imported workflow. It reads the stored
rule store for one scope level (checkpoint / architecture / global) and returns
the matching rules as plain text.

It only reads the backend; rule editing, draft persistence and prompt writing
stay in the workbench web app.
"""

from __future__ import annotations

import json
from typing import Any

import backend_client

LEVELS = ["checkpoint", "architecture", "global"]


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
    """Read the stored rule store for one scope level."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
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

    def load(self, level, scope_key, backend_url, token=""):
        data = backend_client.list_rules(level, scope_key, backend_url, token)
        if "error" in data:
            return (f"[workbench] 无法读取规则：{data['error']}", "{}")
        return _render_rules(data)


NODE_CLASS_MAPPINGS = {"WorkbenchRuleContext": WorkbenchRuleContext}
NODE_DISPLAY_NAME_MAPPINGS = {"WorkbenchRuleContext": "工作台规则清单"}
