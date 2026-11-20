"""Workbench external ComfyUI extension.

The package lives outside the ComfyUI install and is registered through
``../extra_paths.yaml``, so integrated-install upgrades never overwrite the
workbench nodes or panels.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_DIR = Path(__file__).parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

NODE_CLASS_MAPPINGS: dict[str, type] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
WEB_DIRECTORY = "./web"


def _load(module_name: str, file_name: str) -> None:
    spec = importlib.util.spec_from_file_location(module_name, _DIR / file_name)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if hasattr(module, "NODE_CLASS_MAPPINGS"):
        NODE_CLASS_MAPPINGS.update(module.NODE_CLASS_MAPPINGS)
    if hasattr(module, "NODE_DISPLAY_NAME_MAPPINGS"):
        NODE_DISPLAY_NAME_MAPPINGS.update(module.NODE_DISPLAY_NAME_MAPPINGS)


_load("workbench_rules_node", "rules_node.py")
_load("workbench_release_node", "release_node.py")
_load("workbench_context_node", "context_node.py")
_load("workbench_candidate_node", "candidate_node.py")

# Register the local read-only proxy routes (no-op when the ComfyUI server
# module is unavailable, e.g. under plain pytest collection).
try:
    import proxy

    proxy.register_routes()
except Exception:
    pass

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
