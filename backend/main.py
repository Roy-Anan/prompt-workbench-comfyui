from __future__ import annotations

import asyncio
import hmac
import json
import os
import platform
import secrets
import shutil
import sqlite3
import subprocess
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request as UrlRequest, urlopen

import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

import asset_library
import backup_export
import backup_restore
import comfy_tasks
import datadir
import generation_history
import llm_history
import lora_metadata
import model_catalog
import model_identity
import profile_source
import prompt_profiles
import prompt_personas
import queue_dispatcher
import rule_engine
import rule_store
import runtime_log
import sidebar_summary as sidebar_summary_module
import task_queue
import vision_api
from asset_api import router as asset_router
from workflow_api import router as workflow_router
from workflow_api import system_router
from workflow_service import WorkflowStore, WorkflowError

APP_NAME = "ComfyUI-Prompt-Workbench"
APP_VERSION = "0.1.0-dev.5"

# Bootstrapping token: only used to request a per-session ticket. In the
# local Web runtime it is generated per launch by the start script and passed
# through an environment variable; it is never baked into the frontend build.
BOOTSTRAP_TOKEN = os.environ.get("WORKBENCH_SESSION_TOKEN", "")

# Per-launch instance identity, injected by the launcher and echoed by the
# health endpoint so the stopper can verify it matches the structured record.
INSTANCE_ID = os.environ.get("WORKBENCH_INSTANCE_ID", "")

# Unified data root: <repo>/userdata by default; overridable via env var.
DATA_ROOT = datadir.default_data_root()
SETTINGS_FILE = datadir.settings_path(DATA_ROOT)

# Frontend build directory served by FastAPI in the local Web runtime.
BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"

MANAGED_KEYS = set(datadir.MANAGED_KEYS)
OPTIONAL_EXTERNAL_KEYS = set(datadir.OPTIONAL_EXTERNAL_KEYS)
ALLOWED_STORAGE_KEYS = MANAGED_KEYS | OPTIONAL_EXTERNAL_KEYS | {"root"}
ALLOWED_CONNECTION_KEYS = {"ollama_url", "comfyui_url"}

# Migration report for the current process; populated on first settings load.
MIGRATION_REPORT: dict[str, Any] = {}

# The data-root preparation (managed dirs + one-time legacy migration) runs
# once per process. A lock serializes the first concurrent requests so the
# legacy settings file is rewritten by a single caller instead of several
# threads replacing settings.json at the same time on Windows.
_root_prepared = False
_root_lock = threading.Lock()

# Local-only origins allowed by CORS. The production Web runtime is same-origin
# (FastAPI serves both API and frontend), so no broad LAN/public origin is added.
# The ComfyUI loopback origins are included so the external extension sidebar
# panel can reach the health probe over loopback; read-only data endpoints still
# require a session or the node token.
ALLOWED_ORIGINS = {
    "http://127.0.0.1:17890",
    "http://localhost:17890",
    "http://127.0.0.1:1420",
    "http://localhost:1420",
    "http://127.0.0.1:8188",
    "http://localhost:8188",
}

# Per-session tickets handed to the browser via an HttpOnly cookie. They are
# generated at bootstrap and held only in process memory.
SESSION_TICKETS: set[str] = set()

# Read-only credential for local ComfyUI extension nodes. Nodes run in the
# integrated Python process and cannot see the launcher's in-memory bootstrap
# token, so a separate token is persisted under the data root and loaded once
# at startup. The backend rejects state-changing requests that present it,
# except for the narrow, idempotent node actions listed below.
NODE_TOKEN = ""
_node_token_loaded = False

# State-changing endpoints a node token is allowed to call. Each must be a
# best-effort, idempotent local action that does not mutate user drafts, rules,
# or settings. Anything else stays read-only for node callers.
NODE_WRITE_ALLOWED_PATHS = {"/api/system/release-vram"}

# Node-token POST endpoints matched by path suffix. These are read-only-derived
# calls: they ask the local LLM to produce a suggestion but never write it back
# to a draft, rule, or setting. They are listed by suffix because the workflow
# id segment is dynamic.
NODE_POST_ALLOWED_SUFFIXES = ("/prompt/candidates",)

CSRF_HEADER = "X-Workbench-CSRF"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

# Read-only loopback endpoints exposed without a session ticket. They carry
# only non-sensitive identity/count metadata (no Prompt texts, model names,
# notes or file paths), so the external ComfyUI sidebar can probe the local
# workbench over loopback.
PUBLIC_READONLY_PATHS = {"/api/health", "/api/overview"}


def default_settings() -> dict[str, Any]:
    return datadir.default_settings(DATA_ROOT)


def write_test(path: Path) -> None:
    probe = path / f".workbench-write-test-{secrets.token_hex(8)}"
    try:
        probe.write_text("ok", encoding="utf-8")
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_managed_directories(settings: dict[str, Any]) -> None:
    for key in MANAGED_KEYS:
        raw = settings["storage"].get(key)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"Managed storage path is empty: {key}")

        path = Path(raw).expanduser()
        path.mkdir(parents=True, exist_ok=True)

        if not path.is_dir():
            raise ValueError(f"Storage path is not a directory: {key}")

        write_test(path)


def initialize_database(settings: dict[str, Any]) -> str:
    database_dir = Path(settings["storage"]["database"]).expanduser()
    database_dir.mkdir(parents=True, exist_ok=True)

    if not database_dir.is_dir():
        raise ValueError("Database path is not a directory")

    database_path = database_dir / "workbench.sqlite3"

    with sqlite3.connect(database_path, timeout=10) as connection:
        connection.execute("PRAGMA foreign_keys = ON")

        model_identity.ensure_schema(connection)
        rule_store.ensure_schema(connection)
        asset_library.ensure_schema(connection)
        llm_history.ensure_schema(connection)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        row = connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()

        if row is None or row[0] is None:
            # INSERT OR IGNORE keeps concurrent first-load threads from racing
            # on the schema_version primary key (Windows file locking).
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_version(version, applied_at)
                VALUES (?, ?)
                """,
                (1, datetime.now(timezone.utc).isoformat()),
            )

        connection.execute(
            """
            INSERT INTO app_metadata(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (
                "application",
                APP_NAME,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    return str(database_path)


def save_settings_file(settings: dict[str, Any]) -> None:
    datadir.save_settings_file(DATA_ROOT, settings)


def backup_corrupt_settings() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    backup = SETTINGS_FILE.with_name(
        f"settings.corrupt-{timestamp}-{secrets.token_hex(3)}.json"
    )
    shutil.copy2(SETTINGS_FILE, backup)
    return backup


def _ensure_node_token() -> None:
    """Load (creating on first use) the persisted extension node token."""
    global NODE_TOKEN, _node_token_loaded
    if not _node_token_loaded:
        NODE_TOKEN = datadir.load_or_create_node_token(DATA_ROOT)
        _node_token_loaded = True


def load_settings() -> dict[str, Any]:
    global MIGRATION_REPORT, _root_prepared

    # Prepare managed directories and migrate any legacy data once per process.
    # The lock keeps the first concurrent callers from racing the legacy
    # settings rewrite; later calls skip preparation entirely.
    if not _root_prepared:
        with _root_lock:
            if not _root_prepared:
                MIGRATION_REPORT = datadir.prepare_root(DATA_ROOT)
                _root_prepared = True

    settings = default_settings()

    try:
        loaded = datadir.load_settings_file(DATA_ROOT)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        loaded = None
        try:
            backup_corrupt_settings()
        except OSError as exc:
            raise RuntimeError(
                f"Settings are corrupt and backup failed: {exc}"
            ) from exc

    if loaded is not None:
        storage = loaded.get("storage", {})
        connections = loaded.get("connections", {})

        if not isinstance(storage, dict):
            raise ValueError("Settings storage must be an object")
        if not isinstance(connections, dict):
            raise ValueError("Settings connections must be an object")
        if "_legacy_extra" in loaded and isinstance(loaded["_legacy_extra"], dict):
            settings["_legacy_extra"] = loaded["_legacy_extra"]

        # Preserve only external ComfyUI paths; managed paths are rebuilt
        # against the current data root so a legacy AppData path is not kept.
        for key in OPTIONAL_EXTERNAL_KEYS:
            if key in storage and isinstance(storage[key], str):
                settings["storage"][key] = storage[key]

        for key in ALLOWED_CONNECTION_KEYS:
            if key in connections and isinstance(connections[key], str):
                settings["connections"][key] = connections[key]

        loaded_prompt = loaded.get("prompt", {})
        if isinstance(loaded_prompt, dict):
            try:
                settings["prompt"]["protected_terms"] = _normalize_protected_terms(
                    loaded_prompt.get("protected_terms", [])
                )
            except ValueError:
                # A malformed persisted term list fails open to an empty list
                # rather than blocking settings startup.
                settings["prompt"]["protected_terms"] = []

    ensure_managed_directories(settings)
    initialize_database(settings)
    save_settings_file(settings)
    return settings


class SettingsUpdate(BaseModel):
    storage: dict[str, str] = Field(default_factory=dict)
    connections: dict[str, str] = Field(default_factory=dict)
    prompt: dict[str, Any] = Field(default_factory=dict)


ALLOWED_PROMPT_KEYS = {"protected_terms"}
MAX_PROTECTED_TERMS = 50
MAX_PROTECTED_TERM_CHARS = 60


def _normalize_protected_terms(raw: Any) -> list[str]:
    """Validate and de-duplicate the protected-term list; raise on bad shape."""
    if not isinstance(raw, list):
        raise ValueError("prompt.protected_terms must be a list")
    terms: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("protected_terms entries must be strings")
        term = item.strip()
        if not term or term in seen:
            continue
        if len(term) > MAX_PROTECTED_TERM_CHARS:
            raise ValueError(
                f"protected term too long (max {MAX_PROTECTED_TERM_CHARS} chars)"
            )
        seen.add(term)
        terms.append(term)
    if len(terms) > MAX_PROTECTED_TERMS:
        raise ValueError(f"too many protected terms (max {MAX_PROTECTED_TERMS})")
    return terms


class StorageValidation(BaseModel):
    path: str
    create: bool = False


class LoraManualUpdate(BaseModel):
    trigger_words: list[str] = Field(default_factory=list)
    description: str = ""
    base_model: str = ""
    combos: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    samples: list[dict[str, Any]] = Field(default_factory=list)
    hidden: bool = False


class IdentityHashRequest(BaseModel):
    category: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)


class RuleUpsert(BaseModel):
    level: str
    scope_key: str | None = None
    rule_key: str = Field(min_length=1, max_length=120)
    value: Any = None
    provenance: str = "unverified"
    confidence: str = "unverified"
    version: str = Field(default="1", min_length=1, max_length=40)
    note: str = Field(default="", max_length=1000)


class RuleDelete(BaseModel):
    level: str
    scope_key: str | None = None
    rule_key: str = Field(min_length=1, max_length=120)


class ProfileImport(BaseModel):
    file_name: str = Field(min_length=1, max_length=120)
    candidate_keys: list[str] | None = None


class QueueEnqueue(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=1000)


class QueueItemPause(BaseModel):
    paused: bool


class QueueItemMove(BaseModel):
    direction: str


class QueueGlobalPause(BaseModel):
    paused: bool


def _recover_orphan_queue_items() -> None:
    """Requeue items left in ``submitting`` by a previous process."""
    try:
        with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
            task_queue.ensure_schema(connection)
            count = task_queue.requeue_stale_submitting(connection)
    except Exception:
        runtime_log.logger().exception("启动恢复队列失败")
        return
    if count:
        runtime_log.logger().warning(
            "启动恢复：%s 条队列项停在上次提交中，已重新排队", count
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ensure_node_token()
    runtime_log.setup_logging(DATA_ROOT)
    runtime_log.logger().info("工作台后端启动（%s）", APP_VERSION)
    _recover_orphan_queue_items()
    queue_dispatcher.start()
    try:
        yield
    finally:
        runtime_log.logger().info("工作台后端停止")
        queue_dispatcher.stop()


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.include_router(workflow_router)
app.include_router(system_router)
app.include_router(asset_router)
app.include_router(vision_api.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", CSRF_HEADER],
)


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        # Same-origin browser navigations and same-origin fetch often omit
        # Origin; loopback non-browser clients are covered by the token check.
        return True
    return origin in ALLOWED_ORIGINS


def _authorized(request: Request) -> tuple[bool, str]:
    """Return (authorized, session_kind).

    session_kind is "cookie" for the browser ticket, "bearer" for the
    bootstrap token (start script / tests / local CLI), or "" when rejected.
    """
    # 1) Per-session ticket carried in an HttpOnly cookie (browser runtime).
    cookie_ticket = request.cookies.get("workbench_session")
    if cookie_ticket and cookie_ticket in SESSION_TICKETS:
        return True, "cookie"

    # 2) Bootstrap token via Bearer header (start script / tests / local CLI).
    supplied = request.headers.get("Authorization", "")
    expected = f"Bearer {BOOTSTRAP_TOKEN}"
    if BOOTSTRAP_TOKEN and hmac.compare_digest(supplied, expected):
        return True, "bearer"

    # 3) Read-only node token (ComfyUI extension nodes on the same machine).
    if NODE_TOKEN:
        expected_node = f"Bearer {NODE_TOKEN}"
        if hmac.compare_digest(supplied, expected_node):
            return True, "node"

    return False, ""


def _node_post_allowed(path: str) -> bool:
    """Whether a node token may call this non-safe method.

    Exact release actions are whitelisted outright; derived read-only POST
    endpoints (e.g. local LLM candidate generation) match by suffix.
    """
    if path in NODE_WRITE_ALLOWED_PATHS:
        return True
    return any(path.endswith(suffix) for suffix in NODE_POST_ALLOWED_SUFFIXES)


@app.middleware("http")
async def require_session(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    # Static frontend assets and the SPA shell are served without API auth.
    if not request.url.path.startswith("/api"):
        return await call_next(request)

    # The health endpoint is an unauthenticated identity probe: the launcher
    # uses it to confirm the listener is actually this workbench before
    # issuing a session. It exposes only app id/version, no user data.
    if request.url.path in PUBLIC_READONLY_PATHS:
        return await call_next(request)

    # Reject cross-origin state changes from untrusted origins.
    if request.method not in SAFE_METHODS and not _origin_allowed(request):
        return JSONResponse(
            status_code=403,
            content={"detail": "Origin not allowed"},
        )

    # /api/bootstrap performs its own origin/token check and issues the ticket.
    if request.url.path != "/api/bootstrap":
        authorized, session_kind = _authorized(request)
        if not authorized:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing local session"},
            )
    else:
        authorized, session_kind = _authorized(request)

    # The node token is read-only except for a narrow set of idempotent local
    # actions. Reject any other state-changing request even when the token
    # itself is valid.
    if session_kind == "node" and request.method not in SAFE_METHODS:
        if not _node_post_allowed(request.url.path):
            return JSONResponse(
                status_code=403,
                content={"detail": "Node token is read-only"},
            )

    # CSRF protection for browser cookie sessions on state-changing requests.
    # The Bearer bootstrap token is a non-browser caller and does not carry
    # ambient cookies, so it is exempt from the CSRF header check.
    if request.method not in SAFE_METHODS and session_kind == "cookie":
        csrf_cookie = request.cookies.get("workbench_csrf")
        csrf_header = request.headers.get(CSRF_HEADER, "")
        if not csrf_cookie or not hmac.compare_digest(csrf_cookie, csrf_header):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token mismatch"},
            )

    # Unhandled exceptions never reach the check below: Starlette's outermost
    # ServerErrorMiddleware turns them into the 500 response, so the only place
    # that can see them is here, around the call itself.
    try:
        response = await call_next(request)
    except Exception:
        runtime_log.logger().exception(
            "请求异常 %s %s", request.method, request.url.path
        )
        raise
    if response.status_code >= 500:
        runtime_log.logger().error(
            "请求失败 %s %s -> HTTP %s",
            request.method,
            request.url.path,
            response.status_code,
        )
    return response


@app.get("/api/bootstrap")
def bootstrap(request: Request):
    """Issue a per-session ticket to the local browser.

    Same-origin loopback navigations/fetch omit Origin or send a loopback
    Origin and are issued a ticket directly. Any other origin must present the
    Bearer bootstrap token (script / CLI / tests). The ticket is stored in an
    HttpOnly cookie; a separate readable CSRF cookie protects state changes.
    """
    origin = request.headers.get("Origin")
    same_origin = not origin or origin in ALLOWED_ORIGINS

    if not same_origin:
        authorized, _ = _authorized(request)
        if not authorized:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing local session"},
            )

    ticket = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    SESSION_TICKETS.add(ticket)

    response = JSONResponse(
        {
            "ok": True,
            "app": APP_NAME,
            "version": APP_VERSION,
            "csrf": csrf,
        }
    )
    response.set_cookie(
        "workbench_session",
        ticket,
        httponly=True,
        samesite="Strict",
        secure=False,
        path="/",
        max_age=60 * 60 * 8,
    )
    response.set_cookie(
        "workbench_csrf",
        csrf,
        httponly=False,
        samesite="Strict",
        secure=False,
        path="/",
        max_age=60 * 60 * 8,
    )
    return response


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "app": APP_NAME,
        "app_id": APP_NAME,
        "instance_id": INSTANCE_ID,
        "version": APP_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


@app.get("/api/overview")
def overview():
    """Read-only aggregate counts for the external ComfyUI sidebar panel.

    Public over loopback like the health probe. Returns only non-sensitive
    counts (rules / workflows / drafts / assets / history); no Prompt texts,
    model snapshots, notes or file paths are exposed.
    """
    counts: dict[str, int] = {
        "rules": 0,
        "workflows": 0,
        "drafts": 0,
        "assets": 0,
        "history": 0,
    }
    try:
        with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
            generation_history.ensure_schema(connection)
            counts["history"] = len(generation_history.list_history(connection))
            rule_store.ensure_schema(connection)
            counts["rules"] = len(rule_store.list_rules(connection))

        store = WorkflowStore(DATA_ROOT / "workflows")
        workflows = store.list_workflows()
        counts["workflows"] = len(workflows)
        for workflow in workflows:
            counts["drafts"] += len(store.list_drafts(workflow["id"]))

        with asset_library.connect(datadir.database_path(DATA_ROOT)) as connection:
            asset_library.ensure_schema(connection)
            counts["assets"] = len(
                asset_library.list_items(connection, DATA_ROOT / "assets")
            )
    except (OSError, ValueError, sqlite3.Error, WorkflowError) as exc:
        return {"ok": False, "version": APP_VERSION, "error": str(exc), "counts": counts}

    return {"ok": True, "version": APP_VERSION, "counts": counts}


@app.get("/api/sidebar/summary")
def sidebar_summary():
    """Desensitized read-only aggregate for the external ComfyUI sidebar.

    Like ``/api/overview`` it is consumed over loopback, but it requires a
    session ticket, bearer token or the read-only node token. The projection
    omits Prompt texts, draft notes, snapshots, absolute paths and image lists;
    see ``sidebar_summary.build_summary``.
    """
    settings = load_settings()
    return sidebar_summary_module.build_summary(DATA_ROOT, settings)


@app.get("/api/settings")
def get_settings():
    try:
        return load_settings()
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Settings initialization failed: {exc}",
        ) from exc


@app.post("/api/settings")
def update_settings(payload: SettingsUpdate):
    current = load_settings()

    unknown_storage = set(payload.storage) - ALLOWED_STORAGE_KEYS
    if unknown_storage:
        raise HTTPException(
            400, f"Unknown storage keys: {sorted(unknown_storage)}"
        )

    if "root" in payload.storage:
        raise HTTPException(
            400,
            "storage.root is fixed by the local host and cannot be changed",
        )

    unknown_connections = set(payload.connections) - ALLOWED_CONNECTION_KEYS
    if unknown_connections:
        raise HTTPException(
            400,
            f"Unknown connection keys: {sorted(unknown_connections)}",
        )

    candidate = json.loads(json.dumps(current))

    for key, value in payload.storage.items():
        value = value.strip()

        if key in OPTIONAL_EXTERNAL_KEYS:
            if value:
                external_path = Path(value).expanduser()
                if not external_path.exists() or not external_path.is_dir():
                    raise HTTPException(
                        400,
                        f"External model directory does not exist: {key}",
                    )
            candidate["storage"][key] = value
            continue

        if not value:
            raise HTTPException(400, f"Storage path cannot be empty: {key}")

        candidate["storage"][key] = str(Path(value).expanduser())

    for key, value in payload.connections.items():
        value = value.strip()

        if not value.startswith(("http://", "https://")):
            raise HTTPException(400, f"Invalid URL: {key}")

        candidate["connections"][key] = value.rstrip("/")

    unknown_prompt = set(payload.prompt) - ALLOWED_PROMPT_KEYS
    if unknown_prompt:
        raise HTTPException(400, f"Unknown prompt keys: {sorted(unknown_prompt)}")

    terms_raw = payload.prompt.get(
        "protected_terms", candidate.get("prompt", {}).get("protected_terms", [])
    )
    try:
        terms = _normalize_protected_terms(terms_raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    candidate.setdefault("prompt", {})["protected_terms"] = terms

    try:
        ensure_managed_directories(candidate)
        initialize_database(candidate)
        save_settings_file(candidate)
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(
            400,
            f"Cannot validate or save settings: {exc}",
        ) from exc

    return candidate


@app.post("/api/storage/validate")
def validate_storage(payload: StorageValidation):
    raw = payload.path.strip()

    if not raw:
        raise HTTPException(400, "Path is empty")

    path = Path(raw).expanduser()

    try:
        if payload.create:
            path.mkdir(parents=True, exist_ok=True)

        exists = path.exists()
        is_dir = path.is_dir()
        writable = False

        if exists and is_dir:
            write_test(path)
            writable = True

        return {
            "path": str(path),
            "exists": exists,
            "is_directory": is_dir,
            "writable": writable,
        }

    except OSError as exc:
        raise HTTPException(
            400,
            f"Path validation failed: {exc}",
        ) from exc


def probe_url(url: str, path: str) -> dict[str, Any]:
    target = url.rstrip("/") + path
    request = UrlRequest(target)
    request.add_header("User-Agent", APP_NAME)

    try:
        with urlopen(request, timeout=2.5) as response:
            return {
                "online": 200 <= response.status < 300,
                "status_code": response.status,
                "url": url,
                "error": None,
            }

    except (URLError, TimeoutError, OSError) as exc:
        return {
            "online": False,
            "status_code": None,
            "url": url,
            "error": str(exc),
        }


def fetch_ollama_models(url: str) -> dict[str, Any]:
    """Read-only list local Ollama model names from ``/api/tags``.

    Never falls back to a cloud model; an unreachable or malformed response is
    reported conservatively as offline with an empty list and no fabricated
    entries.
    """
    target = url.rstrip("/") + "/api/tags"
    request = UrlRequest(target)
    request.add_header("User-Agent", APP_NAME)

    try:
        with urlopen(request, timeout=2.5) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        entries = data.get("models") if isinstance(data, dict) else None
        names: list[str] = []
        details: dict[str, str] = {}
        families: dict[str, list[str]] = {}
        raw_details: dict[str, dict[str, Any]] = {}
        if isinstance(entries, list):
            for item in entries:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    name = item["name"]
                    names.append(name)
                    item_details = item.get("details")
                    if isinstance(item_details, dict):
                        raw_details[name] = item_details
                        param_size = item_details.get("parameter_size")
                        if isinstance(param_size, str) and param_size.strip():
                            details[name] = param_size.strip()
                        fam = item_details.get("family")
                        fam_list = item_details.get("families")
                        caps = item_details.get("capabilities")
                        tags: list[str] = []
                        if isinstance(fam, str) and fam.strip():
                            tags.append(fam.strip())
                        if isinstance(fam_list, list):
                            tags.extend(str(f).strip() for f in fam_list if str(f).strip())
                        if isinstance(caps, list):
                            tags.extend(str(c).strip() for c in caps if str(c).strip())
                        families[name] = tags
        model_vision = vision_api.probe_vision_map(url, raw_details)
        return {
            "online": True,
            "models": names,
            "model_details": details,
            "model_families": families,
            "model_vision": model_vision,
            "error": None,
        }
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "online": False,
            "models": [],
            "model_details": {},
            "model_families": {},
            "model_vision": {},
            "error": str(exc),
        }


def _as_nonneg_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0:
        return int(value)
    return None


def fetch_comfyui_status(url: str) -> dict[str, Any]:
    """Read-only ComfyUI connection and device VRAM snapshot from ``/system_stats``.

    Unreachable, malformed, or structurally unexpected responses are reported
    conservatively as offline with unknown fields; no device or VRAM figure is
    fabricated. This does not submit a task or otherwise control ComfyUI.
    """
    offline: dict[str, Any] = {
        "online": False,
        "error": None,
        "version": None,
        "pytorch_version": None,
        "ram_total": None,
        "ram_free": None,
        "devices": [],
    }
    target = url.rstrip("/") + "/system_stats"
    request = UrlRequest(target)
    request.add_header("User-Agent", APP_NAME)

    try:
        with urlopen(request, timeout=2.5) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        offline["error"] = str(exc)
        return offline

    if not isinstance(data, dict):
        offline["error"] = "system_stats 响应结构不符"
        return offline

    system = data.get("system") if isinstance(data.get("system"), dict) else {}
    devices_raw = data.get("devices")
    devices: list[dict[str, Any]] = []
    if isinstance(devices_raw, list):
        for item in devices_raw:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            dev_type = item.get("type")
            name = item.get("name")
            devices.append(
                {
                    "index": index if isinstance(index, int) else None,
                    "name": name if isinstance(name, str) else None,
                    "type": dev_type if isinstance(dev_type, str) else None,
                    "vram_total": _as_nonneg_int(item.get("vram_total")),
                    "vram_free": _as_nonneg_int(item.get("vram_free")),
                }
            )

    def _sys_str(key: str) -> str | None:
        value = system.get(key)
        return value if isinstance(value, str) else None

    return {
        "online": True,
        "error": None,
        "version": _sys_str("comfyui_version"),
        "pytorch_version": _sys_str("pytorch_version"),
        "ram_total": _as_nonneg_int(system.get("ram_total")),
        "ram_free": _as_nonneg_int(system.get("ram_free")),
        "devices": devices,
    }


def _get_json(url: str, path: str) -> tuple[dict[str, Any] | None, str | None]:
    """GET a JSON body; ``(data, error)`` with data ``None`` on failure."""
    target = url.rstrip("/") + path
    request = UrlRequest(target)
    request.add_header("User-Agent", APP_NAME)
    try:
        with urlopen(request, timeout=2.5) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw), None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def _queue_ids(items: Any) -> list[str]:
    ids: list[str] = []
    if not isinstance(items, list):
        return ids
    for item in items:
        if not isinstance(item, list) or not item:
            continue
        # ComfyUI queue entry: [index, prompt_id, prompt, ...]; id in slot 1.
        prompt_id: Any = None
        if len(item) > 1 and isinstance(item[1], str):
            prompt_id = item[1]
        elif isinstance(item[0], str):
            prompt_id = item[0]
        if isinstance(prompt_id, str) and prompt_id:
            ids.append(prompt_id)
    return ids


def fetch_comfyui_queue(url: str) -> dict[str, Any]:
    """Read-only ComfyUI queue and history prompt identifiers.

    Only prompt ids are projected; no task is submitted, cancelled, or
    reordered. Unreachable or malformed responses report empty lists with an
    error and no fabricated ids.
    """
    queue_data, queue_error = _get_json(url, "/queue")
    history_data, history_error = _get_json(url, "/history")

    if queue_data is None:
        return {
            "online": False,
            "error": queue_error,
            "running": [],
            "pending": [],
            "history": [],
        }

    running = _queue_ids(queue_data.get("queue_running"))
    pending = _queue_ids(queue_data.get("queue_pending"))
    history_ids: list[str] = []
    if isinstance(history_data, dict):
        history_ids = [key for key in history_data.keys() if isinstance(key, str)]

    return {
        "online": True,
        "error": history_error if history_data is None else None,
        "running": running,
        "pending": pending,
        "history": history_ids,
    }


@app.get("/api/services/status")
def services_status():
    settings = load_settings()
    connections = settings["connections"]

    return {
        "ollama": probe_url(
            connections["ollama_url"],
            "/api/tags",
        ),
        "comfyui": probe_url(
            connections["comfyui_url"],
            "/system_stats",
        ),
    }


def _dir_info(raw: Any) -> dict[str, Any]:
    path = Path(str(raw)).expanduser() if isinstance(raw, str) and raw.strip() else None
    return {"path": str(path) if path else None, "exists": bool(path and path.is_dir())}


@app.get("/api/diagnostics")
def diagnostics():
    """Read-only local diagnostic report. Never uploaded externally.

    Summarizes app/version, Python/platform, per-domain record counts,
    configured directories and local service reachability. No model weights,
    original images or full prompt payloads are attached; a database failure is
    recorded as an error field rather than aborting the report.
    """
    settings = load_settings()
    storage = settings.get("storage", {})
    connections = settings["connections"]

    counts: dict[str, Any] = {
        "rules": None,
        "history": None,
        "tasks": None,
        "model_identities": None,
        "workflows": None,
        "profiles": None,
        "drafts": None,
    }
    db_error: str | None = None
    db_integrity: str | None = None
    storage_health: dict[str, Any] | None = None

    database_dir = Path(storage.get("database", "")).expanduser()
    database_path = database_dir / "workbench.sqlite3"
    try:
        with model_identity.connect(database_path) as connection:
            rule_store.ensure_schema(connection)
            generation_history.ensure_schema(connection)
            comfy_tasks.ensure_schema(connection)

            def _count(table: str) -> int:
                row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                return int(row[0])

            counts["rules"] = _count("workbench_rule")
            counts["history"] = _count("generation_history")
            counts["tasks"] = _count("comfy_task")
            counts["model_identities"] = _count("model_identity")

            # Read-only integrity probe; "ok" means the schema-level check
            # passed. A non-ok result is surfaced verbatim, never guessed away.
            integrity_row = connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()
            db_integrity = str(integrity_row[0]) if integrity_row else "unknown"
    except Exception as exc:  # conservative: report the failure, do not 500
        db_error = f"{type(exc).__name__}: {exc}"

    try:
        workflows_root = Path(storage.get("workflows", "")).expanduser()
        workflow_store = WorkflowStore(workflows_root)
        workflows = workflow_store.list_workflows()
        counts["workflows"] = len(workflows)
        # Drafts are file-stored per workflow; count the full draft box,
        # including soft-deleted recycle-bin entries, conservatively.
        counts["drafts"] = sum(
            len(workflow_store.list_drafts(item["id"], include_trashed=True))
            for item in workflows
        )
        # Read-only conservative scan: corrupt drafts/versions and orphan
        # files are reported, never auto-repaired or deleted here.
        storage_health = workflow_store.inspect_storage()
    except (OSError, WorkflowError, FileNotFoundError) as exc:
        counts["workflows"] = None
        counts["drafts"] = None
        db_error = f"{db_error} | workflows: {exc}" if db_error else f"workflows: {exc}"

    try:
        counts["profiles"] = len(profile_source.list_profiles())
    except OSError:
        counts["profiles"] = None

    managed_dirs = {key: _dir_info(storage.get(key, "")) for key in datadir.MANAGED_KEYS}
    external_dirs = {
        key: _dir_info(storage.get(key, "")) for key in datadir.OPTIONAL_EXTERNAL_KEYS
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app": APP_NAME,
        "version": APP_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "data_root": str(DATA_ROOT),
        "database_path": str(database_path),
        "database": {"integrity": db_integrity},
        "storage_health": storage_health,
        "counts": counts,
        "directories": managed_dirs,
        "external_directories": external_dirs,
        "services": {
            "ollama": probe_url(connections["ollama_url"], "/api/tags"),
            "comfyui": probe_url(connections["comfyui_url"], "/system_stats"),
        },
        "database_error": db_error,
        "uploaded": False,
    }


@app.get("/api/logs")
def runtime_logs(lines: int = 200):
    """Read-only tail of the on-disk runtime log.

    Returns the last bounded number of plain text lines and the local file
    path. The log stays local and is excluded from backup archives; it carries
    no model weights, images or full prompt payloads.
    """
    load_settings()
    count = max(1, min(int(lines), 1000))
    entries = runtime_log.read_tail(DATA_ROOT, count)
    return {
        "path": str(runtime_log.log_file(DATA_ROOT)),
        "lines": entries,
        "count": len(entries),
    }


@app.get("/api/backup/export")
def backup_export_archive():
    """Read-only local backup ZIP of the managed persistent data domains.

    Archives settings, the SQLite database (online backup), workflow copies
    and snapshots, knowledge material, prompt templates, drafts and history
    index records. Cache, logs, prior backups and prior exports are excluded;
    model weights and original images are never copied (external paths are
    recorded in the manifest only). Restore / verified recovery is a later
    slice. The report manifest is stored inside the archive as ``manifest.json``.
    """
    load_settings()  # ensure the managed root and database are ready
    data, manifest = backup_export.build_backup_archive(DATA_ROOT, APP_VERSION)
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{backup_export.backup_filename()}"'
        ),
        "X-Workbench-Backup-Integrity": str(manifest["database"]["integrity"]),
    }
    return Response(content=data, media_type="application/zip", headers=headers)


@app.post("/api/backup/restore")
async def backup_restore_archive(request: Request, overwrite: bool = False):
    """Restore a verified backup ZIP uploaded as the raw request body.

    Fail-closed: the archive manifest and archived database integrity are
    checked before any existing data is touched; a pre-restore backup of the
    current root is written first. By default existing files are kept
    (skipped and reported); ``overwrite=true`` replaces same-path files. The
    database is restored as a whole file. ComfyUI weights and original images
    are never part of the archive and are not touched here.
    """
    load_settings()
    body = await request.body()
    try:
        report = backup_restore.restore_backup(
            DATA_ROOT, body, overwrite=overwrite, version=APP_VERSION
        )
    except backup_restore.RestoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"恢复写入失败：{exc}")
    return report


@app.get("/api/ollama/models")
def ollama_models():
    settings = load_settings()
    return fetch_ollama_models(settings["connections"]["ollama_url"])


@app.get("/api/comfyui/status")
def comfyui_status():
    settings = load_settings()
    return fetch_comfyui_status(settings["connections"]["comfyui_url"])


@app.get("/api/comfyui/queue")
def comfyui_queue():
    settings = load_settings()
    return fetch_comfyui_queue(settings["connections"]["comfyui_url"])


def _comfy_ws_target(http_url: str) -> str:
    base = http_url.strip()
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base.rstrip("/") + "/ws?clientId=workbench"


@app.websocket("/api/comfyui/ws")
async def comfyui_ws_proxy(websocket: WebSocket):
    """Proxy the ComfyUI progress WebSocket through the workbench origin.

    The browser connects same-origin to the workbench; the backend opens the
    upstream ComfyUI socket server-side, so no cross-port Origin header reaches
    ComfyUI's origin middleware (the loopback 403s disappear). Frames are
    forwarded both ways; HTTP polling remains the fallback if the socket drops.
    """
    try:
        settings = load_settings()
        target = _comfy_ws_target(settings["connections"]["comfyui_url"])
    except Exception:
        await websocket.close(code=1011)
        return

    await websocket.accept()
    try:
        async with websockets.connect(target, max_size=None, open_timeout=5) as upstream:
            async def forward_client_to_upstream() -> None:
                try:
                    while True:
                        message = await websocket.receive_text()
                        await upstream.send(message)
                except (WebSocketDisconnect, RuntimeError, OSError, ConnectionError):
                    return

            async def forward_upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        await websocket.send_text(message)
                except (RuntimeError, OSError, ConnectionError):
                    return

            tasks = [
                asyncio.create_task(forward_client_to_upstream()),
                asyncio.create_task(forward_upstream_to_client()),
            ]
            _done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


class VisionHistoryEntry(BaseModel):
    asset_id: int
    name: str | None = None


class VisionHistorySave(BaseModel):
    model: str | None = None
    prompt: str | None = None
    description: str
    assets: list[VisionHistoryEntry] = []


class WritingHistorySave(BaseModel):
    model: str | None = None
    persona_id: str | None = None
    persona_name: str | None = None
    genre: str | None = None
    style: str | None = None
    mode: str | None = None
    task: str | None = None
    output_text: str


HISTORY_KINDS = ("prompt", "vision", "writing")


@app.get("/api/history/all")
def history_aggregated(kind: str = "all", keyword: str | None = None):
    """Unified archive across the three generators.

    ``kind`` filters one of ``prompt`` (out-prompt tasks only), ``vision``
    (image-to-text) or ``writing`` (manually saved drafts); ``all`` merges
    them newest-first. Counts are returned unfiltered for the section chips.
    """
    selected = kind or "all"
    if selected not in {"all", *HISTORY_KINDS}:
        raise HTTPException(400, f"Unknown history kind: {kind}")

    output_dir = load_settings().get("storage", {}).get("output", "")
    store = WorkflowStore(DATA_ROOT / "workflows")
    assets_dir = DATA_ROOT / "assets"

    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        # Asset records are read by column name; keep the Row factory enabled.
        connection.row_factory = sqlite3.Row
        generation_history.ensure_schema(connection)
        llm_history.ensure_schema(connection)
        asset_library.ensure_schema(connection)

        prompt_all = generation_history.list_history(connection)
        vision_all = llm_history.list_vision(connection)
        writing_all = llm_history.list_writing(connection)

        counts = {
            "prompt": len(prompt_all),
            "vision": len(vision_all),
            "writing": len(writing_all),
        }

        items: list[dict[str, Any]] = []

        if selected in {"all", "prompt"}:
            prompt_items = generation_history.list_history(
                connection, keyword=keyword
            )
            for item in prompt_items:
                item["kind"] = "prompt"
                item["id"] = item.get("prompt_id")
                item["images"] = generation_history.annotate_exists(
                    item.get("images") or [], output_dir
                )
                item["linked_draft"] = store.find_linked_draft(
                    item.get("workflow_id") or "", item.get("prompt_id") or ""
                )
            items.extend(prompt_items)

        if selected in {"all", "vision"}:
            vision_items = llm_history.list_vision(connection, keyword=keyword)
            for item in vision_items:
                resolved: list[dict[str, Any]] = []
                for asset in item["assets"]:
                    record = asset_library.get_record(
                        connection, asset["asset_id"], assets_dir
                    )
                    resolved.append(
                        {
                            "asset_id": asset["asset_id"],
                            "name": record["name"] if record else asset["name"],
                            "exists": bool(record and record["exists"]),
                        }
                    )
                item["assets"] = resolved
            items.extend(vision_items)

        if selected in {"all", "writing"}:
            items.extend(llm_history.list_writing(connection, keyword=keyword))

    items.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return {"items": items, "counts": counts}


@app.post("/api/history/vision")
def history_save_vision(payload: VisionHistorySave):
    """Persist one image-to-text result, referencing asset-library pictures.

    Only images picked from the asset library may be archived; temporary local
    uploads stay out of history and no image bytes are copied here.
    """
    description = (payload.description or "").strip()
    if not description:
        raise HTTPException(400, "识别结果为空，不能存入历史")
    if not payload.assets:
        raise HTTPException(400, "请从素材库选择图片后再存入历史")

    asset_ids: list[int] = []
    image_names: list[str] = []
    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        connection.row_factory = sqlite3.Row
        llm_history.ensure_schema(connection)
        asset_library.ensure_schema(connection)
        for entry in payload.assets:
            record = asset_library.get_record(
                connection, entry.asset_id, DATA_ROOT / "assets"
            )
            if record is None:
                raise HTTPException(404, f"素材 {entry.asset_id} 不存在或已删除")
            asset_ids.append(entry.asset_id)
            image_names.append(entry.name or record["name"])
        record = llm_history.record_vision(
            connection,
            model=payload.model,
            prompt=payload.prompt,
            description=description,
            asset_ids=asset_ids,
            image_names=image_names,
        )
    return record


@app.post("/api/history/writing")
def history_save_writing(payload: WritingHistorySave):
    """Persist one writing-assistant candidate; only on explicit user save."""
    if not (payload.output_text or "").strip():
        raise HTTPException(400, "候选内容为空，不能存入历史")
    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        llm_history.ensure_schema(connection)
        return llm_history.record_writing(
            connection,
            model=payload.model,
            persona_id=payload.persona_id,
            persona_name=payload.persona_name,
            genre=payload.genre,
            style=payload.style,
            mode=payload.mode,
            task=payload.task,
            output_text=payload.output_text,
        )


@app.delete("/api/history/{kind}/{record_id}")
def history_delete(kind: str, record_id: str):
    """Permanently delete one history record from any section.

    The row is removed only; drafts, asset originals and ComfyUI output images
    on disk are never touched. There is no recycle bin.
    """
    if kind not in HISTORY_KINDS:
        raise HTTPException(400, f"Unknown history kind: {kind}")

    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        generation_history.ensure_schema(connection)
        llm_history.ensure_schema(connection)
        if kind == "prompt":
            removed = generation_history.delete_by_prompt_id(
                connection, record_id
            )
        elif kind == "vision":
            try:
                removed = llm_history.delete_vision(connection, int(record_id))
            except ValueError:
                raise HTTPException(400, "无效的记录编号") from None
        else:
            try:
                removed = llm_history.delete_writing(connection, int(record_id))
            except ValueError:
                raise HTTPException(400, "无效的记录编号") from None

    if not removed:
        raise HTTPException(404, "历史记录不存在或已删除")
    return {"removed": True}


@app.get("/api/history")
def generation_history_list(
    workflow_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
):
    """Read-only generation history, newest first, optionally filtered.

    Filters: ``workflow_id`` exact match, ``status`` exact task status,
    ``keyword`` case-insensitive match against prompt text and model snapshot.
    """
    output_dir = load_settings().get("storage", {}).get("output", "")
    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        generation_history.ensure_schema(connection)
        items = generation_history.list_history(
            connection, workflow_id=workflow_id, status=status, keyword=keyword
        )
    store = WorkflowStore(DATA_ROOT / "workflows")
    for item in items:
        item["images"] = generation_history.annotate_exists(
            item.get("images") or [], output_dir
        )
        item["linked_draft"] = store.find_linked_draft(
            item.get("workflow_id") or "", item.get("prompt_id") or ""
        )
    return {"items": items}


@app.get("/api/history/image")
def history_image(filename: str, subfolder: str = ""):
    """Serve a recorded output image read-only from the configured output dir.

    Path traversal and non-image extensions are rejected at the resolver; the
    original file is never copied or modified. This static route must be
    registered before the ``/api/history/{prompt_id}`` dynamic route, otherwise
    ``image`` is captured as a prompt_id and the image endpoint returns 404.
    """
    output_dir = load_settings().get("storage", {}).get("output", "")
    path = generation_history.resolve_image_path(output_dir, filename, subfolder)
    if path is None:
        raise HTTPException(status_code=404, detail="图片不存在或不可访问")
    return FileResponse(path)


@app.get("/api/history/{prompt_id}")
def generation_history_detail(prompt_id: str):
    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        generation_history.ensure_schema(connection)
        record = generation_history.get_by_prompt_id(connection, prompt_id)
    if record is None:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    output_dir = load_settings().get("storage", {}).get("output", "")
    record["images"] = generation_history.annotate_exists(
        record.get("images") or [], output_dir
    )
    store = WorkflowStore(DATA_ROOT / "workflows")
    record["linked_draft"] = store.find_linked_draft(
        record.get("workflow_id") or "", record.get("prompt_id") or ""
    )
    return record


@app.get("/api/models/catalog")
def models_catalog():
    """Read-only checkpoint / LoRA names exposed by ComfyUI.

    Names come from ComfyUI ``/object_info``; an optional configured local
    directory marks whether each file is present on disk and records cached
    metadata in the identity store. No weights are copied or modified; a
    SHA256 digest is never computed eagerly here.
    """
    settings = load_settings()
    connections = settings["connections"]
    storage = settings["storage"]

    catalog = model_catalog.fetch_catalog(
        comfyui_url=connections["comfyui_url"],
        checkpoint_dir=storage.get("checkpoint", ""),
        lora_dir=storage.get("lora", ""),
        clip_dir=storage.get("clip", ""),
        vae_dir=storage.get("vae", ""),
    )

    database_path = Path(storage["database"]).expanduser() / "workbench.sqlite3"
    # catalog group -> identity category -> storage directory key.
    identity_groups = (
        ("checkpoints", "checkpoint", "checkpoint"),
        ("loras", "lora", "lora"),
        ("clips", "clip", "clip"),
        ("vaes", "vae", "vae"),
    )
    with model_identity.connect(database_path) as connection:
        model_identity.ensure_schema(connection)
        for _group, category, dir_key in identity_groups:
            directory = storage.get(dir_key, "")
            if directory:
                model_identity.index_directory(connection, category, directory)

        summaries: dict[str, dict[str, Any]] = {}
        for group, category, _dir_key in identity_groups:
            summaries[group] = {
                entry["name"]: model_identity.identity_summary(
                    connection, category, entry["name"]
                )
                for entry in catalog.get(group, [])
            }

    for group, _category, _dir_key in identity_groups:
        for entry in catalog.get(group, []):
            entry["identity"] = summaries[group].get(
                entry["name"], {"known": False}
            )

    # R98: read-only LoRA sidecar metadata summary, attached to LoRA entries.
    # R114: managed manual notes under knowledge/lora win over the sidecar;
    # the ComfyUI LoRA directory is never written to.
    manual_lora_dir = Path(storage.get("knowledge", "")).expanduser() / "lora"
    lora_names = [entry["name"] for entry in catalog.get("loras", [])]
    lora_meta = lora_metadata.scan_lora_metadata(
        storage.get("lora", ""), lora_names, manual_dir=str(manual_lora_dir)
    )
    for entry in catalog.get("loras", []):
        entry["meta"] = lora_meta.get(entry["name"])

    # R127: read-only identity grouping. Two LoRAs are flagged related when
    # they share a file stem (case-insensitive) or at least one trigger word.
    # This is a hint for manual review, not an automatic merge. Hidden manual
    # notes are kept out of grouping so bundled/non-role weights do not pull
    # role LoRAs into related/compatible hints.
    visible_loras = [
        entry
        for entry in catalog.get("loras", [])
        if not (lora_meta.get(entry["name"]) or {}).get("hidden")
    ]
    related, related_triggers = _group_related_loras(visible_loras, lora_meta)
    compatible = _group_compatible_loras(visible_loras, lora_meta)
    for entry in catalog.get("loras", []):
        entry["related"] = related.get(entry["name"], [])
        entry["related_triggers"] = related_triggers.get(entry["name"], [])
        entry["compatible"] = compatible.get(entry["name"], [])

    return catalog


def _group_related_loras(
    loras: list[dict[str, Any]], meta: dict[str, dict[str, Any]]
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    by_stem: dict[str, list[str]] = {}
    by_trigger: dict[str, list[str]] = {}
    names = [entry["name"] for entry in loras if isinstance(entry.get("name"), str)]
    for entry in loras:
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        stem = Path(name).stem.lower()
        by_stem.setdefault(stem, []).append(name)
        record = meta.get(name) or {}
        for word in record.get("trigger_words", []):
            if isinstance(word, str) and word.strip():
                by_trigger.setdefault(word.strip().lower(), []).append(name)

    related: dict[str, set[str]] = {name: set() for name in names}
    for group in list(by_stem.values()) + list(by_trigger.values()):
        if len(group) > 1:
            for name in group:
                related[name].update(other for other in group if other != name)

    trigger_map: dict[str, set[str]] = {}
    for name in names:
        record = meta.get(name) or {}
        trigger_map[name] = {
            word.strip().lower()
            for word in record.get("trigger_words", [])
            if isinstance(word, str) and word.strip()
        }

    related_triggers: dict[str, set[str]] = {name: set() for name in names}
    for name, others in related.items():
        for other in others:
            related_triggers[name].update(trigger_map[name] & trigger_map[other])

    return (
        {name: sorted(values) for name, values in related.items()},
        {name: sorted(values) for name, values in related_triggers.items()},
    )


def _group_compatible_loras(
    loras: list[dict[str, Any]], meta: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    """Read-only hint: LoRAs sharing the same non-empty base model.

    A shared base model only means the pair may be reviewed for stacking; it is
    not an automatic compatibility verdict, and identities are never merged.
    """
    by_base: dict[str, list[str]] = {}
    names: list[str] = []
    for entry in loras:
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        names.append(name)
        record = meta.get(name) or {}
        base = str(record.get("base_model") or "").strip().lower()
        if base:
            by_base.setdefault(base, []).append(name)

    compatible: dict[str, set[str]] = {name: set() for name in names}
    for group in by_base.values():
        if len(group) > 1:
            for name in group:
                compatible[name].update(other for other in group if other != name)
    return {name: sorted(values) for name, values in compatible.items()}


@app.post("/api/models/loras/{name}/manual")
def save_lora_manual(name: str, payload: LoraManualUpdate):
    """Persist the manual LoRA note in the managed ``knowledge/lora`` dir.

    Whitelisted fields only; the ComfyUI LoRA directory is never written to.
    A missing/unconfigured knowledge root rejects the write conservatively.
    """
    settings = load_settings()
    manual_dir = Path(settings["storage"].get("knowledge", "")).expanduser() / "lora"
    try:
        record = lora_metadata.save_manual(
            str(manual_dir), name, payload.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": name, "manual": record}


# 小体积类别允许主动计算并缓存哈希；checkpoint 多 GB，不提供主动触发入口。
HASHABLE_IDENTITY_CATEGORIES = ("lora", "clip", "vae")


@app.post("/api/models/identity/hash")
def hash_model_identity(payload: IdentityHashRequest):
    """Compute and cache the on-demand SHA256 for one recorded small model.

    The digest path comes only from the identity store (never a client-supplied
    path); weights are not copied or modified.
    """
    category = payload.category.strip().lower()
    name = payload.name.strip()
    if category not in HASHABLE_IDENTITY_CATEGORIES:
        raise HTTPException(status_code=400, detail="该类别不支持主动计算哈希")
    settings = load_settings()
    database_path = Path(settings["storage"]["database"]).expanduser() / "workbench.sqlite3"
    with model_identity.connect(database_path) as connection:
        model_identity.ensure_schema(connection)
        record = model_identity.ensure_hash(connection, category, name)
        if record is None:
            raise HTTPException(status_code=404, detail="身份库未记录该模型")
        summary = model_identity.identity_summary(connection, category, name)
    return {"category": category, "name": name, "identity": summary}


@app.get("/api/rules")
def list_rules(level: str | None = None, scope_key: str | None = None):
    """List stored rules, optionally filtered by level and scope."""
    settings = load_settings()
    database_path = Path(settings["storage"]["database"]).expanduser() / "workbench.sqlite3"
    with model_identity.connect(database_path) as connection:
        rule_store.ensure_schema(connection)
        return {"items": rule_store.list_rules(connection, level=level, scope_key=scope_key)}


@app.get("/api/rules/resolve")
def resolve_rules(level: str, scope_key: str | None = None, keys: str = ""):
    """Resolve requested rule keys for one context via programmatic inheritance.

    Only the requested ``keys`` (comma separated) are evaluated; unrelated
    stored rules are never returned. Conflicts and unknowns are reported rather
    than guessed.
    """
    requested = [key.strip() for key in keys.split(",") if key.strip()]
    if not requested:
        raise HTTPException(400, "At least one rule key is required")
    if level not in rule_engine.LEVEL_ORDER:
        raise HTTPException(400, f"Unknown rule level: {level}")

    settings = load_settings()
    database_path = Path(settings["storage"]["database"]).expanduser() / "workbench.sqlite3"
    with model_identity.connect(database_path) as connection:
        rule_store.ensure_schema(connection)
        return rule_store.resolve_stored(connection, level, scope_key, requested)


@app.post("/api/rules")
def upsert_rule(payload: RuleUpsert):
    """Insert or replace one structured rule.

    Validation is enforced twice: by the pydantic model and again by the
    engine record validator in ``rule_store``. Invalid records are rejected
    with 400 and are never written to the store.
    """
    settings = load_settings()
    database_path = Path(settings["storage"]["database"]).expanduser() / "workbench.sqlite3"
    try:
        with model_identity.connect(database_path) as connection:
            rule_store.ensure_schema(connection)
            record = rule_store.upsert_rule(
                connection,
                level=payload.level,
                scope_key=payload.scope_key,
                rule_key=payload.rule_key,
                value=payload.value,
                provenance=payload.provenance,
                confidence=payload.confidence,
                version=payload.version,
                note=payload.note,
            )
            return record
    except rule_engine.RuleError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/rules")
def delete_rule(payload: RuleDelete):
    """Delete one rule identified by level/scope_key/key."""
    if payload.level not in rule_engine.LEVEL_ORDER:
        raise HTTPException(400, f"Unknown rule level: {payload.level}")

    settings = load_settings()
    database_path = Path(settings["storage"]["database"]).expanduser() / "workbench.sqlite3"
    with model_identity.connect(database_path) as connection:
        rule_store.ensure_schema(connection)
        removed = rule_store.delete_rule(
            connection, payload.level, payload.scope_key, payload.rule_key
        )
    return {"removed": removed}


@app.get("/api/rules/history")
def rule_history(
    level: str | None = None,
    scope_key: str | None = None,
    rule_key: str | None = None,
):
    """List rule history snapshots, newest first (read-only)."""
    settings = load_settings()
    database_path = Path(settings["storage"]["database"]).expanduser() / "workbench.sqlite3"
    with model_identity.connect(database_path) as connection:
        rule_store.ensure_schema(connection)
        return {
            "items": rule_store.list_history(
                connection, level=level, scope_key=scope_key, rule_key=rule_key
            )
        }


@app.post("/api/rules/history/{history_id}/restore")
def restore_rule_history(history_id: int):
    """Roll one rule back to a history snapshot through the shared validator."""
    settings = load_settings()
    database_path = Path(settings["storage"]["database"]).expanduser() / "workbench.sqlite3"
    try:
        with model_identity.connect(database_path) as connection:
            rule_store.ensure_schema(connection)
            record = rule_store.restore_history(connection, history_id)
    except rule_engine.RuleError as exc:
        raise HTTPException(400, str(exc)) from exc
    if record is None:
        raise HTTPException(404, "历史版本不存在或已失效")
    return record


@app.get("/api/profiles")
def list_profiles():
    """List legacy profile notes with parsed identity and candidate counts.

    Read-only: no candidate is persisted to the rule store by this endpoint.
    """
    return {"items": profile_source.list_profiles()}


@app.get("/api/profiles/dir")
def profiles_dir_info():
    """Return the resolved legacy profile directory path and existence flag."""
    return profile_source.profiles_dir_info()


@app.post("/api/profiles/open-dir")
def open_profiles_dir():
    """Open the fixed legacy profile directory in the host file manager.

    Only the configured profile directory is opened; no caller-supplied path
    is accepted.
    """
    try:
        return profile_source.open_profiles_dir()
    except FileNotFoundError as exc:
        raise HTTPException(404, f"Profile directory not found: {exc}") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, f"Cannot open profile directory: {exc}") from exc


@app.get("/api/profiles/{file_name}")
def preview_profile(file_name: str):
    """Return parsed candidates for one legacy profile note (read-only).

    Each candidate carries a read-only diff flag (new/override/same) against
    rules already persisted; the preview never writes to the store.
    """
    try:
        preview = profile_source.preview_profile(file_name)
    except FileNotFoundError:
        raise HTTPException(404, f"Profile not found: {file_name}") from None

    settings = load_settings()
    database_path = Path(settings["storage"]["database"]).expanduser() / "workbench.sqlite3"
    with model_identity.connect(database_path) as connection:
        rule_store.ensure_schema(connection)
        profile_source.annotate_diff(connection, preview)
    return preview


@app.post("/api/profiles/import")
def import_profile(payload: ProfileImport):
    """Import one reviewed profile's candidates as checkpoint-level rules.

    Candidates are validated by the engine before upsert; invalid ones are
    reported as rejected without aborting the rest. This is a local write behind
    the existing session/CSRF middleware; no external service is called.
    """
    settings = load_settings()
    database_path = Path(settings["storage"]["database"]).expanduser() / "workbench.sqlite3"
    try:
        with model_identity.connect(database_path) as connection:
            rule_store.ensure_schema(connection)
            report = profile_source.import_profile(
            connection,
            payload.file_name,
            selected_keys=payload.candidate_keys,
        )
        return report
    except FileNotFoundError:
        raise HTTPException(404, f"Profile not found: {payload.file_name}") from None
    except rule_engine.RuleError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---- Local Prompt Profile registry (Core + Model-specific rules) ---------

class ProfileConfirm(BaseModel):
    confirm: bool = False


@app.get("/api/prompt-profiles")
def list_prompt_profiles():
    """List registered local Prompt Profiles with validation state."""
    return {"items": prompt_profiles.list_profiles()}


@app.get("/api/personas")
def list_personas():
    """List selectable identity / system-prompt personas."""
    return {"items": prompt_personas.list_personas()}


@app.get("/api/genres")
def list_genres():
    """List writing genre setting templates."""
    return {"items": prompt_personas.list_genres()}


@app.get("/api/styles")
def list_styles():
    """List writing style templates."""
    return {"items": prompt_personas.list_styles()}


@app.get("/api/modes")
def list_modes():
    """List candidate generation modes registered under the modes folder."""
    return {"items": prompt_personas.list_modes()}


@app.get("/api/prompt-profiles/staging")
def list_profile_staging():
    """List staged Profile directories awaiting review/publish."""
    return {"items": prompt_profiles.list_staging()}


@app.get("/api/prompt-profiles/{profile_id}")
def get_prompt_profile(profile_id: str):
    entry = prompt_profiles.get_profile(profile_id)
    if entry is None:
        raise HTTPException(404, f"Profile not found: {profile_id}")
    return entry


@app.post("/api/prompt-profiles/staging/{profile_id}/publish")
def publish_prompt_profile(profile_id: str):
    try:
        return prompt_profiles.publish_from_staging(profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/prompt-profiles/{profile_id}/disable")
def disable_prompt_profile(profile_id: str):
    try:
        return prompt_profiles.set_enabled(profile_id, False)
    except FileNotFoundError:
        raise HTTPException(404, f"Profile not found: {profile_id}") from None


@app.post("/api/prompt-profiles/{profile_id}/enable")
def enable_prompt_profile(profile_id: str):
    try:
        return prompt_profiles.set_enabled(profile_id, True)
    except FileNotFoundError:
        raise HTTPException(404, f"Profile not found: {profile_id}") from None


@app.get("/api/prompt-profiles/{profile_id}/versions")
def list_profile_versions(profile_id: str):
    if prompt_profiles.get_profile(profile_id) is None:
        raise HTTPException(404, f"Profile not found: {profile_id}")
    return {"items": prompt_profiles.list_versions(profile_id)}


@app.post("/api/prompt-profiles/{profile_id}/rollback/{stamp}")
def rollback_prompt_profile(profile_id: str, stamp: str):
    try:
        return prompt_profiles.rollback(profile_id, stamp)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/prompt-profiles/{profile_id}")
def delete_prompt_profile(profile_id: str, payload: ProfileConfirm | None = None):
    confirm = bool(payload and payload.confirm)
    try:
        return prompt_profiles.delete_profile(profile_id, confirm)
    except FileNotFoundError:
        raise HTTPException(404, f"Profile not found: {profile_id}") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---- Local pending submission queue --------------------------------------

def _queue_store() -> WorkflowStore:
    return WorkflowStore(DATA_ROOT / "workflows")


def _queue_public(item: dict[str, Any]) -> dict[str, Any]:
    """Project a queue row for the API: expose only a snapshot flag, not the
    frozen snapshot body (which can be large)."""
    public = {key: value for key, value in item.items() if key != "prompt_snapshot"}
    public["has_snapshot"] = item.get("prompt_snapshot") is not None
    return public


@app.get("/api/queue")
def list_queue(workflow_id: str | None = None):
    """List local pending/settled queue items, newest order by queue position."""
    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        task_queue.ensure_schema(connection)
        items = [_queue_public(item) for item in task_queue.list_items(connection, workflow_id)]
    return {"items": items, "global_paused": queue_dispatcher.is_global_paused()}


@app.post("/api/queue")
def enqueue_queue(payload: QueueEnqueue):
    """Add a confirmed API-format workflow to the local pending queue.

    Enqueueing only records an intent locally; it does not contact ComfyUI or
    the LLM. The same format/whitelist preconditions as submission are checked
    here so an unconfirmable workflow cannot enter the pending list.
    """
    try:
        workflow = _queue_store().get_workflow(payload.workflow_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    manifest = workflow.get("manifest", {})
    if manifest.get("format") != "api":
        raise HTTPException(
            status_code=400,
            detail="UI 格式工作流不能加入队列，请先在 ComfyUI 中导出 API 格式。",
        )
    if not manifest.get("confirmed"):
        raise HTTPException(
            status_code=400,
            detail="请先确认 Prompt 节点白名单，再加入队列。",
        )

    name = str(manifest.get("filename") or payload.workflow_id)
    # Freeze the confirmed working copy and Prompt list at enqueue time so a
    # later edit to the working copy does not change what a queued item submits.
    try:
        working = _queue_store().read_working(payload.workflow_id)
    except (WorkflowError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    snapshot = {
        "working": working,
        "prompts": workflow.get("working_prompts", []),
    }
    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        task_queue.ensure_schema(connection)
        created = task_queue.enqueue(
            connection, payload.workflow_id, name, payload.note, snapshot
        )
    return _queue_public(created)


@app.delete("/api/queue/{queue_id}")
def cancel_queue_item(queue_id: str):
    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        task_queue.ensure_schema(connection)
        item = task_queue.get_item(connection, queue_id)
        if item is None or item["status"] == "cancelled":
            raise HTTPException(status_code=404, detail="排队项不存在")
        try:
            task_queue.cancel_item(connection, queue_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/queue/{queue_id}/pause")
def pause_queue_item(queue_id: str, payload: QueueItemPause):
    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        task_queue.ensure_schema(connection)
        item = task_queue.get_item(connection, queue_id)
        if item is None or item["status"] == "cancelled":
            raise HTTPException(status_code=404, detail="排队项不存在")
        try:
            updated = task_queue.set_paused(connection, queue_id, payload.paused)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return updated


@app.post("/api/queue/{queue_id}/move")
def move_queue_item(queue_id: str, payload: QueueItemMove):
    with model_identity.connect(datadir.database_path(DATA_ROOT)) as connection:
        task_queue.ensure_schema(connection)
        item = task_queue.get_item(connection, queue_id)
        if item is None:
            raise HTTPException(status_code=404, detail="排队项不存在")
        if item["status"] in ("submitting", "submitted"):
            raise HTTPException(status_code=409, detail="已提交的任务不能调整顺序。")
        try:
            items = task_queue.move_item(connection, queue_id, payload.direction)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items}


@app.post("/api/queue/pause-all")
def pause_all_queue(payload: QueueGlobalPause):
    """Hold/resume the whole local dispatcher without cancelling pending items."""
    queue_dispatcher.set_global_paused(payload.paused)
    return {"global_paused": payload.paused}


# ---- Static frontend hosting (local Web runtime) ---------------------------

def _dist_ready() -> bool:
    return FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file()


def _resolve_within_dist(full_path: str) -> Path | None:
    """Resolve ``full_path`` against dist and confirm it stays inside dist.

    Uses real-path containment (not a string prefix) so ``../`` traversal and
    adjacent-directory prefix collisions (e.g. ``dist-other``) are rejected.
    URL-encoded traversal (``%2e%2e%2f``) is decoded before resolving.
    Returns None when the resolved path escapes dist.
    """
    from urllib.parse import unquote

    dist_root = FRONTEND_DIST.resolve()
    decoded = unquote(full_path)
    # Normalize away any traversal segments before joining.
    normalized = Path("/", decoded).as_posix().lstrip("/")
    candidate = (dist_root / normalized).resolve()

    try:
        candidate.relative_to(dist_root)
    except ValueError:
        return None
    return candidate


@app.get("/{full_path:path}")
def serve_frontend(request: Request, full_path: str):
    # Unknown /api routes must return JSON, never the SPA HTML.
    if full_path.startswith("api/") or full_path == "api":
        raise HTTPException(status_code=404, detail="API endpoint not found")

    if not _dist_ready():
        raise HTTPException(
            status_code=503,
            detail="Frontend build missing: run the frontend build before starting the Web runtime.",
        )

    requested = _resolve_within_dist(full_path)
    if requested is None:
        raise HTTPException(status_code=403, detail="Forbidden path")

    if requested.is_file():
        return FileResponse(requested)

    # SPA fallback: any non-API, non-asset route returns the app shell.
    return FileResponse(FRONTEND_DIST / "index.html")


def is_loopback_host(host: str) -> bool:
    """Return True only for explicit loopback addresses/names."""
    import ipaddress
    import socket

    host = host.strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Resolve names defensively; only accept if every resolved address
        # is loopback.
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        for family, _, _, _, sockaddr in infos:
            try:
                addr = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                return False
            if not addr.is_loopback:
                return False
        return bool(infos)


def main() -> None:
    import uvicorn

    host = os.environ.get("WORKBENCH_HOST", "127.0.0.1")
    port = int(os.environ.get("WORKBENCH_PORT", "17890"))

    # Default must stay loopback; refuse LAN/public binds.
    if not is_loopback_host(host):
        raise SystemExit(
            f"Refusing to bind to non-loopback host '{host}'. "
            "The local Web runtime defaults to 127.0.0.1."
        )

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
