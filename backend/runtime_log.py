"""Lightweight on-disk runtime logging for the local workbench.

Rotates a single small log file under the managed ``logs/`` directory. The
file stays local: it is never uploaded by the backup/export endpoints, and the
read API only returns a bounded tail of plain text lines.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
LOG_FILE_NAME = "workbench.log"

FORMATTER = logging.Formatter(
    "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)

_configured_for: Path | None = None
_handler: RotatingFileHandler | None = None


def log_file(root: Path) -> Path:
    """Absolute path of the runtime log file for the given data root."""
    return Path(root) / "logs" / LOG_FILE_NAME


def logger() -> logging.Logger:
    """Named app logger; records propagate to the root file handler."""
    return logging.getLogger("workbench")


def setup_logging(root: Path) -> Path:
    """Attach the rotating file handler to the root logger (idempotent).

    Re-running against the same path does not stack duplicate handlers. If
    the data root changed (e.g. tests rebinding the root), the previous
    handler is detached and a fresh one is attached to the new file.
    """
    global _configured_for, _handler

    path = log_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if _configured_for == resolved:
        return path

    if _handler is not None:
        logging.getLogger("").removeHandler(_handler)
        _handler.close()

    handler = RotatingFileHandler(
        path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(FORMATTER)
    handler.setLevel(logging.INFO)

    root_logger = logging.getLogger("")
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    # uvicorn propagates its records to the root logger by default; keep the
    # level explicit so access/error noise is not promoted above INFO.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.INFO)

    _handler = handler
    _configured_for = resolved
    logger().info("运行日志已落盘：%s", path)
    return path


def read_tail(root: Path, lines: int = 200) -> list[str]:
    """Return the last non-empty lines of the log file, newest last."""
    path = log_file(root)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries = [line for line in text.splitlines() if line.strip()]
    return entries[-lines:]
