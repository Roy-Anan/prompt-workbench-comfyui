"""Launch the bundled ComfyUI with the workbench external extension mounted.

This is an optional convenience entry next to the Qiuye launcher GUI. It does
**not** copy anything into the integrated install and does not modify ComfyUI
source: the extension is exposed only by passing ``extra_paths.yaml`` through
the official ``--extra-model-paths-config`` CLI argument.

ComfyUI and Ollama are still started/stopped by the user; this script just
runs ComfyUI in the foreground so the console output stays visible.

Override the integrated-install location with the ``COMFYUI_ROOT`` environment
variable if it differs from the default path below.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRA_YAML = REPO_ROOT / "comfyui_extension" / "extra_paths.yaml"

COMFY_ROOT = Path(
    os.environ.get("COMFYUI_ROOT", r"D:\AI\Qiuye\ComfyUI-aki-v3.2")
)
PYTHON = COMFY_ROOT / "python" / "python.exe"
MAIN = COMFY_ROOT / "ComfyUI" / "main.py"

LISTEN = os.environ.get("COMFYUI_LISTEN", "127.0.0.1")
PORT = os.environ.get("COMFYUI_PORT", "8188")


def main() -> int:
    missing = [str(path) for path in (PYTHON, MAIN, EXTRA_YAML) if not path.is_file()]
    if missing:
        print("[ComfyUI] 找不到以下路径：")
        for item in missing:
            print("  -", item)
        print("[ComfyUI] 可设置环境变量 COMFYUI_ROOT 指向整合包根目录后重试。")
        return 1

    cmd = [
        str(PYTHON),
        str(MAIN),
        "--listen",
        LISTEN,
        "--port",
        PORT,
        "--extra-model-paths-config",
        str(EXTRA_YAML),
    ]
    # Any extra arguments passed to this script are forwarded to ComfyUI.
    cmd += sys.argv[1:]

    print("[ComfyUI] 启动命令：", " ".join(cmd))
    print("[ComfyUI] 工作台扩展已通过 extra_paths.yaml 挂载；按 Ctrl+C 退出。")
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
