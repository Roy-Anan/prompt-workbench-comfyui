"""Read-only rule query node: surfaces stored workbench rules onto the canvas.

This is the first external node in the workbench extension. It only reads the
existing backend rule store over loopback; rule editing stays in the backend /
sidebar panel.
"""

from __future__ import annotations

import json

import backend_client

LEVELS = ["checkpoint", "architecture", "global"]


class WorkbenchRuleQuery:
    """Fetch stored rules for one scope level and expose them as text."""

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
    RETURN_NAMES = ("rules_text", "raw_json")
    FUNCTION = "query_rules"
    CATEGORY = "Workbench/Rules"

    def query_rules(self, level, scope_key, backend_url, token=""):
        data = backend_client.list_rules(level, scope_key, backend_url, token)

        if "error" in data:
            return (f"[workbench] 无法读取规则：{data['error']}", "{}")

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


NODE_CLASS_MAPPINGS = {"WorkbenchRuleQuery": WorkbenchRuleQuery}
NODE_DISPLAY_NAME_MAPPINGS = {"WorkbenchRuleQuery": "工作台规则查询"}
