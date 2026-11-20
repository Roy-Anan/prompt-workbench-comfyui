"""Shared pytest fixtures for the backend test suite."""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Fresh in-process FastAPI app bound to a temporary data root.

    Yields ``(TestClient, main_module)`` so API tests can seed the SQLite store
    under the temporary data root before making requests.
    """
    monkeypatch.setenv("WORKBENCH_SESSION_TOKEN", "test-session-token")
    monkeypatch.setenv(
        "WORKBENCH_DATA_ROOT",
        str(tmp_path / "workbench-data"),
    )

    sys.modules.pop("main", None)
    sys.modules.pop("datadir", None)
    import datadir
    import main

    main.BOOTSTRAP_TOKEN = "test-session-token"
    main.DATA_ROOT = datadir.default_data_root()
    main.SETTINGS_FILE = datadir.settings_path(main.DATA_ROOT)
    main.MIGRATION_REPORT = {}
    main.SESSION_TICKETS.clear()
    main.NODE_TOKEN = ""
    main._node_token_loaded = False

    with TestClient(main.app) as test_client:
        yield test_client, main

    sys.modules.pop("main", None)
    sys.modules.pop("datadir", None)
