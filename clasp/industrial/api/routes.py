# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/api/routes.py
==============================
REST and WebSocket routes for the IndustrialSilexEngine API.
"""

from typing import Any
import json
import time
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
from clasp.config import CLASP_SETTINGS, CLASP_API_KEY

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.agents.watcher_agent import WatcherAgent
from clasp.industrial.agents.root_cause_agent import RootCauseAgent
from clasp.industrial.agents.optimizer_agent import OptimizerAgent
from clasp.industrial.api.ws import manager

router = APIRouter()

# -----------------------------------------------------------------------------
# Dependency injection helpers
# -----------------------------------------------------------------------------

def get_engine(request: Request) -> IndustrialSilexEngine:
    engine = getattr(request.app.state, "engine", None)
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return engine

def get_watcher(request: Request) -> WatcherAgent:
    watcher = getattr(request.app.state, "watcher", None)
    if not watcher:
        raise HTTPException(status_code=500, detail="WatcherAgent not initialized")
    return watcher

# -----------------------------------------------------------------------------
# DTOs
# -----------------------------------------------------------------------------

class InvestigateRequest(BaseModel):
    node_id: str
    timestamp: float

# -----------------------------------------------------------------------------
# Settings helpers
# -----------------------------------------------------------------------------

def _read_settings():
    if not CLASP_SETTINGS.exists():
        return {
            "safe_mode": True,
            "system_prompt": "You are an autonomous industrial safety AI. Prioritize safety above all else.",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.0-flash"
        }
    with open(CLASP_SETTINGS, "r") as f:
        return json.load(f)

def _write_settings(settings: dict):
    CLASP_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    with open(CLASP_SETTINGS, "w") as f:
        json.dump(settings, f, indent=4)

class SettingsUpdate(BaseModel):
    safe_mode: bool
    system_prompt: str
    llm_provider: str
    llm_model: str

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@router.get("/api/health")
async def health_check(request: Request):
    """
    Health check endpoint — unauthenticated, for load balancers and monitoring.
    Returns engine readiness status.
    """
    engine = getattr(request.app.state, "engine", None)
    engine_ready = engine is not None and engine._initialized
    auth_enabled = CLASP_API_KEY is not None

    return {
        "status": "ok",
        "engine_ready": engine_ready,
        "auth_enabled": auth_enabled,
        "timestamp": time.time(),
    }

@router.get("/api/settings")
async def get_settings():
    """Get the current AI agent settings."""
    return _read_settings()

@router.post("/api/settings")
async def update_settings(update: SettingsUpdate):
    """Update AI agent settings."""
    settings = _read_settings()
    settings["safe_mode"] = update.safe_mode
    settings["system_prompt"] = update.system_prompt
    settings["llm_provider"] = update.llm_provider
    settings["llm_model"] = update.llm_model
    _write_settings(settings)
    return settings

@router.get("/api/plant/status")
async def get_plant_status(engine: IndustrialSilexEngine = Depends(get_engine)):
    """Return the most recent values for all registered nodes."""
    status = {}
    for node_id in engine._node_registry.values():
        val = engine._time_buffer.get_latest(node_id)
        if val is not None:
            label = engine._node_id_to_label.get(node_id, node_id)
            status[label] = val
            status[node_id] = val
    return status

@router.get("/api/graph")
async def get_graph(engine: IndustrialSilexEngine = Depends(get_engine)):
    """Return the causal graph in a D3.js compatible format {nodes, links}."""
    graph = engine.graph.graph

    nodes = []
    links = []

    for n, data in graph.nodes(data=True):
        label = engine._node_id_to_label.get(n, n)
        ind_type = data.get("industrial_type", "unknown")
        nodes.append({
            "id": n,
            "label": label,
            "type": ind_type
        })

    for u, v, data in graph.edges(data=True):
        links.append({
            "source": u,
            "target": v,
            "type": data.get("edge_type"),
            "confidence": data.get("confidence", 0.0),
            "lag_seconds": data.get("lag_seconds", 0.0)
        })

    return {"nodes": nodes, "links": links}

@router.get("/api/alerts")
async def get_alerts(engine: IndustrialSilexEngine = Depends(get_engine)):
    """
    Return the last 100 alerts. Alerts are kept in the engine's in-memory
    circular buffer (capped at 100 entries), so they survive a browser refresh
    but not a server restart. For full persistence, a future version will
    store alerts in SQLite.
    """
    alerts = engine.get_active_alerts()
    return [a.model_dump() for a in reversed(alerts)]

@router.post("/api/investigate")
async def post_investigate(
    req: InvestigateRequest,
    engine: IndustrialSilexEngine = Depends(get_engine)
):
    """Trigger a Root Cause Analysis investigation."""
    agent = RootCauseAgent(engine)
    res = await agent.investigate(req.node_id, req.timestamp)
    return res

@router.get("/api/recommendations/{node_id}")
async def get_recommendations(
    node_id: str,
    engine: IndustrialSilexEngine = Depends(get_engine)
):
    """Get optimization recommendations for a specific node."""
    agent = OptimizerAgent(engine)
    recs = await agent.get_recommendations(node_id)
    return recs

@router.get("/api/auth/me")
async def get_current_user(request: Request):
    """
    Returns a session stub for the current operator.
    In a full deployment, this would decode the Bearer token to return the
    authenticated user's profile from an identity provider.
    Currently returns a placeholder — replace with real auth when integrating
    with your plant's identity system (LDAP, OAuth2, etc.).
    """
    # Check if auth is active — if so, the middleware already validated the token,
    # so we know the caller is authenticated. Return an operator profile stub.
    if CLASP_API_KEY:
        return {
            "name": "Authenticated Operator",
            "role": "Process Engineer",
            "auth_method": "bearer_token",
            "note": "Replace with real identity provider integration (LDAP/OAuth2)."
        }
    return {
        "name": "Development User",
        "role": "Administrator",
        "auth_method": "none",
        "warning": "CLASP_API_KEY not set. Authentication is disabled."
    }

@router.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket):
    """WebSocket endpoint for streaming real-time alerts."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
