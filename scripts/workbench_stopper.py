"""Stop the workbench local Web runtime started by the launcher.

Safety model:
  * The recorded PID is never trusted alone. Before terminating, the stopper
    verifies process identity against stable attributes: interpreter path or
    command line, command-line entry, recorded instance id and creation time.
  * If identity cannot be confirmed (PID reused by another program, missing
    attributes, or a mismatch), the process is left alone and a warning is
    printed for manual review.
  * The whole workbench process tree (root plus recursive descendants) is
    terminated. Python processes are never killed by name, and Ollama,
    ComfyUI, or any other Python service is untouched.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PID_FILE = REPO_ROOT / ".workbench.pid"
RUNTIME_RECORD_FILE = REPO_ROOT / ".workbench-runtime.json"
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
APP_ID = "ComfyUI-Prompt-Workbench"
DEFAULT_HOST = os.environ.get("WORKBENCH_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("WORKBENCH_PORT", "17890"))

kernel32 = ctypes.windll.kernel32


def _powershell_exe() -> str:
    """Resolve a usable PowerShell launcher.

    A bare ``powershell`` may be absent from the child process PATH (the venv
    or a trimmed PATH can drop Windows PowerShell 5.1), which previously made
    every CIM identity lookup fail closed. Prefer pwsh 7, then powershell,
    then the legacy 5.1 full path under Windows.
    """
    found = shutil.which("pwsh") or shutil.which("powershell")
    if found:
        return found
    return os.path.join(
        os.environ.get("WINDIR", r"C:\Windows"),
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )


def pid_alive(pid: int) -> bool:
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    exit_code = ctypes.c_ulong()
    alive = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    kernel32.CloseHandle(handle)
    return bool(alive) and exit_code.value == 259


def _process_info(pid: int) -> dict[str, str] | None:
    """Read stable identity attributes for a PID via CIM.

    Returns a dict with ExecutablePath, CommandLine and CreationDate,
    or None when the process is gone.
    """
    ps = (
        "Get-CimInstance Win32_Process -Filter "
        f'"ProcessId={pid}" | '
        "Select-Object ProcessId,ExecutablePath,CommandLine,"
        "@{n='CreationDate';e={$_.CreationDate.ToString(\"yyyy-MM-ddTHH:mm:ss.ffffffZ\")}} | "
        "ConvertTo-Json -Compress"
    )
    try:
        output = subprocess.check_output(
            [_powershell_exe(), "-NoProfile", "-Command", ps],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    output = output.strip()
    if not output:
        return None
    try:
        data = json.loads(output)
    except ValueError:
        return None
    if isinstance(data, list):
        data = data[0] if data else {}
    return data if isinstance(data, dict) else None


def _normalized(path: str | None) -> str:
    if not path:
        return ""
    return str(Path(path)).lower().replace("/", "\\")


def _is_backend_command(cmd: str) -> bool:
    """True when the command line launches this workbench backend.

    Two entry forms are accepted: the launcher's
    ``python -m uvicorn main:app`` and the direct entry ``python main.py``
    (``main.py`` runs uvicorn internally). Anything else (another Python
    service, a static server, ...) fails closed.
    """
    if not cmd:
        return False
    if "main.py" in cmd:
        return True
    return "uvicorn" in cmd and "main:app" in cmd


def _venv_interpreter(exe: str, cmd: str) -> bool:
    """True when the process runs the project venv interpreter.

    On Windows the venv ``python.exe`` launcher may hand off to the base
    interpreter, so the actual listening child's ``ExecutablePath`` can point
    at the base interpreter while its command line still carries the venv
    path. Accept either signal: executable path, or the first command-line
    token (the interpreter itself).
    """
    venv = _normalized(str(VENV_PYTHON))
    if exe == venv:
        return True
    parts = cmd.strip().split()
    first = parts[0].strip('"').strip("'") if parts else ""
    return _normalized(first) == venv


def read_runtime_record() -> dict | None:
    """Read the structured launcher record; None when missing/corrupt."""
    if not RUNTIME_RECORD_FILE.exists():
        return None
    try:
        data = json.loads(RUNTIME_RECORD_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _creation_date_equal(a: str | None, b: str | None) -> bool:
    """Compare CreationDate instants; a missing side fails closed."""
    if not a or not b:
        return False
    return a.strip() == b.strip()


def _health_instance(host: str | None, port: int | None) -> str | None:
    """Read the running backend's instance id from its loopback health probe."""
    if not host or not port:
        return None
    try:
        import http.client

        conn = http.client.HTTPConnection(str(host), int(port), timeout=1.0)
        conn.request("GET", "/api/health")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        payload = json.loads(body.decode("utf-8"))
    except (OSError, http.client.HTTPException, ValueError, UnicodeDecodeError):
        return None
    value = payload.get("instance_id")
    return value if isinstance(value, str) and value else None


def _health_app_matches(host: str, port: int) -> bool:
    """True only if the loopback health probe identifies this app.

    Used by the no-record fallback: without a launcher record there is no
    per-launch instance id to compare, so the app identifier alone (plus the
    venv-executable check on the owning PID) is the identity signal.
    """
    try:
        import http.client

        conn = http.client.HTTPConnection(str(host), int(port), timeout=1.0)
        conn.request("GET", "/api/health")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        payload = json.loads(body.decode("utf-8"))
    except (OSError, http.client.HTTPException, ValueError, UnicodeDecodeError):
        return False
    return payload.get("app") == APP_ID or payload.get("app_id") == APP_ID


def _listening_pids(port: int) -> list[int]:
    """PIDs with a listening socket on the loopback backend port."""
    ps = (
        "Get-NetTCPConnection -LocalPort {0} -State Listen "
        "-ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess"
    ).format(int(port))
    try:
        output = subprocess.check_output(
            [_powershell_exe(), "-NoProfile", "-Command", ps],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [int(line.strip()) for line in output.splitlines() if line.strip().isdigit()]


def identify_loose(pid: int) -> tuple[bool, str]:
    """Conservative identity check when no launcher record exists.

    No per-launch instance id or creation time is available; the process is
    accepted only when its executable is the project venv interpreter and its
    command line launches this backend. Other Python processes are left alone.
    """
    info = _process_info(pid)
    if info is None:
        return False, "无法读取进程身份信息（进程可能已退出）"

    exe = _normalized(info.get("ExecutablePath"))
    cmd = (info.get("CommandLine") or "").lower()
    if not exe:
        return False, "缺少可执行路径，身份无法确认"
    if not _venv_interpreter(exe, cmd):
        return False, f"解释器不属于项目 .venv：{exe}"
    if not _is_backend_command(cmd):
        return False, "命令行不是本工作台后端入口"
    return True, "身份匹配（无记录兜底）"


def identify_workbench(pid: int, record: dict | None = None) -> tuple[bool, str]:
    """Confirm the PID belongs to this workbench.

    The PID alone is never trusted. We compare the live process against the
    structured launcher record on executable path, command line, recorded
    instance id and process creation time. Any missing critical attribute or
    mismatch fails closed: the process is left alone.
    """
    info = _process_info(pid)
    if info is None:
        return False, "无法读取进程身份信息（进程可能已退出）"

    exe = _normalized(info.get("ExecutablePath"))
    cmd = (info.get("CommandLine") or "").lower()
    creation = info.get("CreationDate")

    if not exe:
        return False, "缺少可执行路径，身份无法确认"
    if not _venv_interpreter(exe, cmd):
        return False, f"解释器不属于项目 .venv：{exe}"
    if not _is_backend_command(cmd):
        return False, "命令行不是本工作台后端入口"
    # Project ownership is already pinned by the executable being the project
    # .venv interpreter above; the command line does not carry the working
    # directory, so it is not checked for a repo path here.

    # Structured record is required for the stricter identity comparison.
    if not record:
        return False, "缺少结构化运行记录，身份无法确认"

    if record.get("app_id") != APP_ID:
        return False, "记录中的应用标识与本工作台不匹配"

    record_instance = record.get("instance_id")
    if not record_instance:
        return False, "记录缺少本次启动实例标识，身份无法确认"

    live_instance = _health_instance(record.get("host"), record.get("port"))
    if not live_instance:
        return False, "无法读取活动进程实例标识，身份无法确认"
    if live_instance != record_instance:
        return False, "实例标识与本次启动记录不一致"

    record_pid = record.get("pid")
    if record_pid is not None and int(record_pid) != pid:
        return False, f"记录 PID（{record_pid}）与当前 PID（{pid}）不一致"

    record_exe = _normalized(record.get("executable_path"))
    if record_exe and record_exe != exe:
        return False, f"可执行路径与记录不符：{exe} != {record_exe}"

    record_cmd = record.get("command_line")
    if isinstance(record_cmd, list):
        record_parts = [_normalized(part) for part in record_cmd if part]
    else:
        record_parts = [_normalized(record_cmd)] if record_cmd else []
    live_parts = [part for part in cmd.split() if part]
    if record_parts and not all(any(part in live for live in live_parts) for part in record_parts):
        return False, "命令行参数与记录不符"

    # Creation time is an auxiliary PID-reuse signal. The stronger reuse
    # guard is the per-launch random instance id compared against the live
    # health probe above. When the recorded creation time is missing (older
    # records, or it was not yet readable right after launch), do not fail on
    # it; both sides are compared only when present on both.
    record_creation = record.get("creation_date")
    if record_creation and creation and not _creation_date_equal(record_creation, creation):
        return False, "进程启动时间与记录不一致（PID 可能已被复用）"

    return True, "身份匹配"


def child_pids(parent_pid: int) -> list[int]:
    """Direct children of the recorded workbench root via CIM."""
    try:
        output = subprocess.check_output(
            [
                _powershell_exe(),
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter "
                f'"ParentProcessId={parent_pid}" | '
                "Select-Object -ExpandProperty ProcessId",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []

    children: list[int] = []
    for line in output.splitlines():
        line = line.strip()
        if line.isdigit():
            children.append(int(line))
    return children


def descendant_pids(root_pid: int) -> list[int]:
    """Recursively collect the full descendant PID set."""
    seen: set[int] = set()
    queue = [root_pid]
    while queue:
        parent = queue.pop()
        for child in child_pids(parent):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return sorted(seen)


def terminate(pid: int) -> bool:
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return False
    ok = kernel32.TerminateProcess(handle, 0)
    kernel32.CloseHandle(handle)
    return bool(ok)


def _terminate_tree(targets: list[int]) -> bool:
    """Terminate the root plus descendants, waiting briefly for exit."""
    for pid in targets:
        if pid_alive(pid):
            terminate(pid)
    for _ in range(10):
        if not any(pid_alive(pid) for pid in targets):
            return True
        time.sleep(0.3)
    return False


def clear_runtime_record() -> None:
    PID_FILE.unlink(missing_ok=True)
    RUNTIME_RECORD_FILE.unlink(missing_ok=True)


def _stop_without_record() -> int:
    """Fallback when no structured launcher record exists.

    The instance may have been started directly (``python main.py``) rather
    than through the launcher. Identity is still pinned conservatively: the
    loopback health probe must identify this app, and the owning PID must run
    the project venv interpreter with a backend command line. Anything else is
    left alone.
    """
    corrupt = RUNTIME_RECORD_FILE.exists()
    if not _health_app_matches(DEFAULT_HOST, DEFAULT_PORT):
        if corrupt:
            print("[工作台] 运行记录损坏，且端口上未识别到本工作台，未结束进程，请人工检查。")
            return 1
        print("[工作台] 未找到运行记录，端口上也未识别到本工作台，工作台可能已经停止。")
        return 0

    candidates = [pid for pid in _listening_pids(DEFAULT_PORT) if pid_alive(pid)]
    targets: list[int] = []
    for pid in candidates:
        ok, reason = identify_loose(pid)
        if ok:
            targets.append(pid)
        else:
            print(f"[工作台] 端口 {DEFAULT_PORT} 上的进程 {pid} 身份不符（{reason}），已保留现场。")

    if not targets:
        print("[工作台] 健康检查识别到本工作台，但未找到可确认身份的后端进程，请人工检查。")
        return 1

    tree = sorted({pid for root in targets for pid in [root, *descendant_pids(root)]})
    if not _terminate_tree(tree):
        print(f"[工作台] 进程 {targets[0]} 仍在运行，请手动确认。")
        return 1

    clear_runtime_record()
    print(f"[工作台] 已停止工作台进程 {targets[0]}（含其子进程），未影响其他服务。")
    return 0


def main() -> int:
    record = read_runtime_record()

    if record is None:
        # No structured record (never launched via the launcher, or corrupt).
        # Fall back to health-probe + venv-executable identity instead of
        # giving up outright; see _stop_without_record for the safety model.
        return _stop_without_record()

    try:
        root_pid = int(record.get("pid"))
    except (TypeError, ValueError):
        print("[工作台] 运行记录中的 PID 无效，未执行结束操作。")
        return 1

    if not pid_alive(root_pid):
        print(f"[工作台] 记录中的进程 {root_pid} 已退出，清理运行记录。")
        clear_runtime_record()
        return 0

    matches, reason = identify_workbench(root_pid, record)
    if not matches:
        print(
            f"[工作台] 进程 {root_pid} 身份与本工作台不匹配（{reason}）。"
            "为避免误杀其他程序，已保留现场，未结束任何进程，请人工检查。"
        )
        return 1

    targets = [root_pid, *descendant_pids(root_pid)]
    if not _terminate_tree(targets):
        print(f"[工作台] 进程 {root_pid} 仍在运行，请手动确认。")
        return 1

    clear_runtime_record()
    print(f"[工作台] 已停止工作台进程 {root_pid}（含其子进程），未影响其他服务。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
