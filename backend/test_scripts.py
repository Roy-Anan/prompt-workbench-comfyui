"""Unit tests for launcher / stopper safety logic (no real process spawned)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load(module_name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / file_name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_loopback_accepts_local():
    launcher = _load("launcher", "workbench_launcher.py")
    for host in ("127.0.0.1", "localhost", "::1"):
        assert launcher.is_loopback_host(host) is True


def test_launcher_loopback_rejects_public_lan():
    launcher = _load("launcher", "workbench_launcher.py")
    for host in ("0.0.0.0", "192.168.1.20", "172.16.0.3", "203.0.113.7"):
        assert launcher.is_loopback_host(host) is False


def test_launcher_health_requires_identity(monkeypatch):
    launcher = _load("launcher", "workbench_launcher.py")

    class _Conn:
        def __init__(self, status, body):
            self._status = status
            self._body = body

        def request(self, method, path):
            pass

        def getresponse(self):
            return self

        def read(self):
            return self._body

        def close(self):
            pass

    # A server that returns an empty 200 (no app identity) must NOT count.
    monkeypatch.setattr(launcher.http.client, "HTTPConnection",
                        lambda host, port, timeout: _Conn(200, b"{}"))
    assert launcher.health_identifies_workbench("127.0.0.1", 8000) is False

    # The real workbench health body carries the app identifier.
    body = b'{"ok":true,"app":"ComfyUI-Prompt-Workbench","version":"0.1.0-dev.4"}'
    monkeypatch.setattr(launcher.http.client, "HTTPConnection",
                        lambda host, port, timeout: _Conn(200, body))
    assert launcher.health_identifies_workbench("127.0.0.1", 8000) is True


def test_stopper_rejects_identity_mismatch(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")

    # PID reused by a process whose executable is not the project venv.
    monkeypatch.setattr(
        stopper, "_process_info",
        lambda pid: {
            "ExecutablePath": "C:/OtherApp/python.exe",
            "CommandLine": "python -m http.server",
            "CreationDate": "20260101000000.000000+000",
        },
    )
    matches, reason = stopper.identify_workbench(999999)
    assert matches is False
    assert "venv" in reason

    # A process without readable identity fails closed.
    monkeypatch.setattr(stopper, "_process_info", lambda pid: None)
    matches, reason = stopper.identify_workbench(999999)
    assert matches is False


def _good_record(stopper, **overrides):
    venv = str(stopper.VENV_PYTHON)
    record = {
        "app_id": stopper.APP_ID,
        "instance_id": "test-instance-123",
        "pid": 12345,
        "creation_date": "2026-01-01T00:00:00.000000Z",
        "executable_path": venv,
        "command_line": [
            venv, "-m", "uvicorn", "main:app",
            "--host", "127.0.0.1", "--port", "8000",
        ],
        "host": "127.0.0.1",
        "port": 8000,
    }
    record.update(overrides)
    return record


def _good_info(stopper, **overrides):
    venv = str(stopper.VENV_PYTHON).lower().replace("/", "\\")
    info = {
        "ExecutablePath": venv,
        "CommandLine": f'"{venv}" -m uvicorn main:app --host 127.0.0.1 --port 8000',
        "CreationDate": "2026-01-01T00:00:00.000000Z",
    }
    info.update(overrides)
    return info


def _patch_health(monkeypatch, stopper, instance="test-instance-123"):
    monkeypatch.setattr(stopper, "_health_instance", lambda host, port: instance)


def test_stopper_matches_workbench_process(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    _patch_health(monkeypatch, stopper)
    monkeypatch.setattr(
        stopper, "_process_info",
        lambda pid: _good_info(stopper),
    )
    matches, _ = stopper.identify_workbench(12345, _good_record(stopper))
    assert matches is True


def test_stopper_rejects_missing_record(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    monkeypatch.setattr(stopper, "_process_info", lambda pid: _good_info(stopper))
    matches, reason = stopper.identify_workbench(12345, None)
    assert matches is False
    assert "运行记录" in reason


def test_stopper_rejects_creation_date_mismatch(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    _patch_health(monkeypatch, stopper)
    monkeypatch.setattr(
        stopper, "_process_info",
        lambda pid: _good_info(
            stopper, CreationDate="2026-02-02T22:22:22.000000Z"
        ),
    )
    record = _good_record(stopper)
    matches, reason = stopper.identify_workbench(12345, record)
    assert matches is False
    assert "启动时间" in reason


def test_stopper_rejects_executable_mismatch(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    _patch_health(monkeypatch, stopper)
    monkeypatch.setattr(
        stopper, "_process_info",
        lambda pid: _good_info(stopper, ExecutablePath="C:/OtherApp/python.exe"),
    )
    matches, reason = stopper.identify_workbench(12345, _good_record(stopper))
    assert matches is False
    assert "可执行路径" in reason


def test_stopper_rejects_command_line_mismatch(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    _patch_health(monkeypatch, stopper)
    venv = str(stopper.VENV_PYTHON).lower().replace("/", "\\")
    monkeypatch.setattr(
        stopper, "_process_info",
        lambda pid: _good_info(
            stopper,
            CommandLine=f'"{venv}" -m http.server 9999',
        ),
    )
    matches, reason = stopper.identify_workbench(12345, _good_record(stopper))
    assert matches is False
    assert "命令行" in reason


def test_stopper_rejects_instance_mismatch(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    _patch_health(monkeypatch, stopper, instance="other-instance")
    monkeypatch.setattr(stopper, "_process_info", lambda pid: _good_info(stopper))
    record = _good_record(stopper)
    matches, reason = stopper.identify_workbench(12345, record)
    assert matches is False
    assert "实例标识" in reason


def test_stopper_rejects_unreachable_health(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    monkeypatch.setattr(stopper, "_health_instance", lambda host, port: None)
    monkeypatch.setattr(stopper, "_process_info", lambda pid: _good_info(stopper))
    matches, reason = stopper.identify_workbench(12345, _good_record(stopper))
    assert matches is False
    assert "实例标识" in reason


def test_launcher_runtime_record_roundtrip(monkeypatch, tmp_path):
    launcher = _load("launcher", "workbench_launcher.py")
    monkeypatch.setattr(launcher, "PID_FILE", tmp_path / ".workbench.pid")
    monkeypatch.setattr(
        launcher, "RUNTIME_RECORD_FILE", tmp_path / ".workbench-runtime.json"
    )
    monkeypatch.setattr(launcher, "_process_creation_date", lambda pid: "2026-01-01T00:00:00.000000Z")

    class _Proc:
        pid = 4321

    launcher.write_runtime_record(_Proc(), "instance-abc")
    record = launcher.read_runtime_record()
    assert record is not None
    assert record["pid"] == 4321
    assert record["instance_id"] == "instance-abc"
    assert record["app_id"] == launcher.APP_ID
    assert launcher.PID_FILE.is_file()

    launcher.clear_runtime_record()
    assert launcher.read_runtime_record() is None
    assert not launcher.PID_FILE.exists()


def test_launcher_corrupt_record_reads_none(monkeypatch, tmp_path):
    launcher = _load("launcher", "workbench_launcher.py")
    monkeypatch.setattr(
        launcher, "RUNTIME_RECORD_FILE", tmp_path / ".workbench-runtime.json"
    )
    launcher.RUNTIME_RECORD_FILE.write_text("{broken", encoding="utf-8")
    assert launcher.read_runtime_record() is None


def test_stopper_rejects_missing_instance_env(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    monkeypatch.setattr(
        stopper, "_process_info",
        lambda pid: _good_info(stopper, EnvironmentVariables={}),
    )
    matches, reason = stopper.identify_workbench(12345, _good_record(stopper))
    assert matches is False
    assert "实例标识" in reason


def test_stopper_accepts_backend_command_forms():
    stopper = _load("stopper", "workbench_stopper.py")
    venv = str(stopper.VENV_PYTHON).lower().replace("/", "\\")
    assert stopper._is_backend_command(f'"{venv}" -m uvicorn main:app --port 17890') is True
    assert stopper._is_backend_command(f'"{venv}" main.py') is True
    assert stopper._is_backend_command(f'"{venv}" -m http.server 9999') is False
    assert stopper._is_backend_command("") is False


def test_stopper_identify_loose_accepts_direct_entry(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    venv = str(stopper.VENV_PYTHON).lower().replace("/", "\\")
    monkeypatch.setattr(
        stopper, "_process_info",
        lambda pid: {
            "ExecutablePath": venv,
            "CommandLine": f'"{venv}" main.py',
            "CreationDate": "2026-01-01T00:00:00.000000Z",
        },
    )
    matches, reason = stopper.identify_loose(4321)
    assert matches is True
    assert "兜底" in reason


def test_stopper_identify_loose_rejects_other_python(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    monkeypatch.setattr(
        stopper, "_process_info",
        lambda pid: {
            "ExecutablePath": "C:/OtherApp/python.exe",
            "CommandLine": "python -m http.server 9999",
            "CreationDate": "2026-01-01T00:00:00.000000Z",
        },
    )
    matches, _ = stopper.identify_loose(4321)
    assert matches is False


def test_stopper_fallback_stops_matched_process(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    monkeypatch.setattr(stopper, "_health_app_matches", lambda h, p: True)
    monkeypatch.setattr(stopper, "_listening_pids", lambda port: [4321])
    monkeypatch.setattr(stopper, "pid_alive", lambda pid: True)
    monkeypatch.setattr(stopper, "identify_loose", lambda pid: (True, "兜底"))
    monkeypatch.setattr(stopper, "descendant_pids", lambda pid: [])
    terminated: list[list[int]] = []
    monkeypatch.setattr(stopper, "_terminate_tree",
                        lambda tree: terminated.append(tree) or True)
    monkeypatch.setattr(stopper, "clear_runtime_record", lambda: None)

    assert stopper._stop_without_record() == 0
    assert terminated == [[4321]]


def test_stopper_fallback_leaves_unmatched_process(monkeypatch):
    stopper = _load("stopper", "workbench_stopper.py")
    monkeypatch.setattr(stopper, "_health_app_matches", lambda h, p: True)
    monkeypatch.setattr(stopper, "_listening_pids", lambda port: [4321])
    monkeypatch.setattr(stopper, "pid_alive", lambda pid: True)
    monkeypatch.setattr(stopper, "identify_loose",
                        lambda pid: (False, "不属于项目 .venv"))
    terminated = []
    monkeypatch.setattr(stopper, "_terminate_tree",
                        lambda tree: terminated.append(tree) or True)

    assert stopper._stop_without_record() == 1
    assert terminated == []


def test_comfyui_launcher_auto_launches_by_default(monkeypatch):
    monkeypatch.delenv("COMFYUI_AUTO_LAUNCH", raising=False)
    launcher = _load("comfy_launcher", "comfyui_launcher.py")
    captured: dict = {}

    def fake_call(cmd):
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(launcher.subprocess, "call", fake_call)
    assert launcher.main() == 0
    assert "--auto-launch" in captured["cmd"]


def test_comfyui_launcher_respects_disable_env(monkeypatch):
    monkeypatch.setenv("COMFYUI_AUTO_LAUNCH", "0")
    launcher = _load("comfy_launcher", "comfyui_launcher.py")
    captured: dict = {}

    def fake_call(cmd):
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(launcher.subprocess, "call", fake_call)
    assert launcher.main() == 0
    assert "--auto-launch" not in captured["cmd"]


def test_stopper_fallback_no_health_match_does_not_touch(monkeypatch, tmp_path):
    stopper = _load("stopper", "workbench_stopper.py")
    # Isolate from the repo root: a real launcher record left there by a running
    # instance would take the "corrupt record" branch and change the outcome.
    monkeypatch.setattr(
        stopper, "RUNTIME_RECORD_FILE", tmp_path / ".workbench-runtime.json"
    )
    monkeypatch.setattr(stopper, "_health_app_matches", lambda h, p: False)
    listed: list[int] = []
    monkeypatch.setattr(stopper, "_listening_pids",
                        lambda port: listed.append(port) or [])
    terminated = []
    monkeypatch.setattr(stopper, "_terminate_tree",
                        lambda tree: terminated.append(tree) or True)

    assert stopper._stop_without_record() == 0
    assert listed == []
    assert terminated == []
