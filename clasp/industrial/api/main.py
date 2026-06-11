# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/api/main.py
============================
FastAPI application entry point.
"""

import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from clasp.config import CLASP_API_KEY, CLASP_CORS_ORIGINS, CLASP_DEMO_MODE
from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.agents.watcher_agent import WatcherAgent
from clasp.industrial.api.routes import router
from clasp.industrial.api.ws import manager

log = logging.getLogger("clasp.api.main")

# -----------------------------------------------------------------------------
# API Key Authentication Middleware
# -----------------------------------------------------------------------------

class ApiKeyMiddleware:
    """
    Enforces Bearer token authentication on all non-health routes.
    If CLASP_API_KEY is not configured, logs a loud warning and skips auth
    (development convenience only — never do this in production).
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Unauthenticated routes: health check and WebSocket
        if path in ("/api/health", "/docs", "/openapi.json") or path.startswith("/ws/"):
            await self.app(scope, receive, send)
            return

        # If no API key is configured, skip auth with a warning
        if not CLASP_API_KEY:
            await self.app(scope, receive, send)
            return

        # Extract and validate Bearer token
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):].strip()
            if token == CLASP_API_KEY:
                await self.app(scope, receive, send)
                return

        # Unauthorized
        response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid or missing API key. Provide 'Authorization: Bearer <CLASP_API_KEY>'."},
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


# -----------------------------------------------------------------------------
# Rate Limiter for LLM Endpoints
# -----------------------------------------------------------------------------

_investigate_calls: dict[str, list[float]] = defaultdict(list)
INVESTIGATE_WINDOW_SECONDS = 60
MAX_INVESTIGATE_PER_WINDOW = 10  # Also controlled by CLASP_INVESTIGATE_RATE_LIMIT env

async def check_investigate_rate_limit(request: Request):
    """FastAPI dependency that enforces rate limiting on /api/investigate."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window_start = now - INVESTIGATE_WINDOW_SECONDS

    # Prune old entries
    _investigate_calls[client_ip] = [
        t for t in _investigate_calls[client_ip] if t > window_start
    ]

    if len(_investigate_calls[client_ip]) >= MAX_INVESTIGATE_PER_WINDOW:
        raise JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": f"Rate limit exceeded: max {MAX_INVESTIGATE_PER_WINDOW} investigations per minute."},
        )

    _investigate_calls[client_ip].append(now)


# -----------------------------------------------------------------------------
# Background Demo Task (only runs if CLASP_DEMO_MODE=true)
# -----------------------------------------------------------------------------

async def run_tep_demo(engine: IndustrialSilexEngine, watcher: WatcherAgent):
    """
    Background task to feed TEP data to the engine for demo purposes.
    Only starts if CLASP_DEMO_MODE=true in environment.
    """
    from clasp.industrial.ingest.tep_simulator import TEPSimulator
    from clasp.industrial.schemas import SensorObservation

    log.info("DEMO MODE: Starting TEP background demo...")

    # 1. Fast forward training on normal data
    normal_path = Path("data/tep/d00.dat")
    if normal_path.exists():
        sim = TEPSimulator(engine, normal_path, speed=0.0)
        await sim.register_tep_nodes()
        await sim.run(max_rows=500)
        log.info("DEMO MODE: Finished fast-forwarding normal data.")

    # Reload Watcher maps with newly learned edges
    await watcher.startup()

    # 2. Slow feed fault data
    fault_path = Path("data/tep/d05.dat")
    if not fault_path.exists():
        log.warning("DEMO MODE: Fault dataset not found at data/tep/d05.dat. Stopping demo.")
        return

    log.info("DEMO MODE: Starting slow feed of fault data (Fault 5)...")
    sim = TEPSimulator(engine, fault_path, speed=1.0)

    original_observe = engine.observe

    async def hooked_observe(node_key: str, value: float, timestamp: float | None = None):
        res = await original_observe(node_key, value, timestamp)
        graph_id = engine.get_node_id(node_key)
        if graph_id:
            obs = SensorObservation(node_id=graph_id, value=value, timestamp=timestamp or 0.0)
            alert = await watcher.on_observation(obs)
            if alert:
                log.warning(f"DEMO MODE Watcher alert: {alert.message}")
                await manager.broadcast(alert.model_dump())
        return res

    engine.observe = hooked_observe
    await sim.run()


# -----------------------------------------------------------------------------
# Application Lifespan
# -----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting Clasp backend API...")

    if not CLASP_API_KEY:
        log.warning(
            "⚠️  SECURITY WARNING: CLASP_API_KEY is not set. "
            "All API endpoints are publicly accessible. "
            "Set CLASP_API_KEY in your .env file before deploying to production."
        )
    else:
        log.info("✅ API key authentication is active.")

    # Initialize Engine
    engine = IndustrialSilexEngine()
    await engine.initialize()

    # Initialize Watcher
    watcher = WatcherAgent(engine)
    await watcher.startup()

    # Start WebSocket manager background task
    manager.start_background_task()

    # Attach to app state
    app.state.engine = engine
    app.state.watcher = watcher

    # Start demo only if explicitly enabled
    if CLASP_DEMO_MODE:
        log.info("CLASP_DEMO_MODE=true: Starting TEP demo background task.")
        app.state.demo_task = asyncio.create_task(run_tep_demo(engine, watcher))
    elif "CLASP_ENV" in __import__("os").environ and __import__("os").environ["CLASP_ENV"] == "test":
        log.info("Running in test mode: TEP demo disabled.")
    else:
        log.info("CLASP_DEMO_MODE is not set. No demo data will be loaded. Connect a real OPC-UA source.")

    yield

    log.info("Shutting down Clasp backend API...")
    if hasattr(app.state, "demo_task"):
        app.state.demo_task.cancel()
    manager.stop_background_task()
    await engine.close()


# -----------------------------------------------------------------------------
# FastAPI App
# -----------------------------------------------------------------------------

app = FastAPI(
    title="Clasp Industrial API",
    description="Real-time causal graph backend for industrial process control.",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS: locked to configured origins (no wildcard in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CLASP_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Key authentication on all routes
app.add_middleware(ApiKeyMiddleware)

app.include_router(router)
