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
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.agents.watcher_agent import WatcherAgent
from clasp.industrial.api.routes import router
from clasp.industrial.api.ws import manager

log = logging.getLogger("clasp.api.main")

# -----------------------------------------------------------------------------
# Background Demo Task
# -----------------------------------------------------------------------------

async def run_tep_demo(engine: IndustrialSilexEngine, watcher: WatcherAgent):
    """
    Background task to feed TEP data to the engine for demo purposes.
    We'll train on normal data quickly, then slow-feed the fault data.
    """
    from clasp.industrial.ingest.tep_simulator import TEPSimulator
    from clasp.industrial.schemas import SensorObservation
    
    log.info("Starting TEP background demo...")
    
    # 1. Fast forward training
    normal_path = Path("data/tep/d00.dat")
    if normal_path.exists():
        sim = TEPSimulator(engine, normal_path, speed=0.0)
        await sim.register_tep_nodes()
        await sim.run(max_rows=500)
        log.info("Finished fast-forwarding normal data.")
    
    # Reload Watcher maps with new learned edges
    await watcher.startup()
    
    # 2. Slow feed fault data
    fault_path = Path("data/tep/d05.dat")
    if not fault_path.exists():
        log.warning("Fault dataset not found, stopping demo.")
        return
        
    log.info("Starting slow feed of fault data (Fault 5)...")
    sim = TEPSimulator(engine, fault_path, speed=1.0) # 1 sec per row for demo
    
    # Hook engine.observe to feed Watcher & broadcast
    original_observe = engine.observe
    
    async def hooked_observe(node_key: str, value: float, timestamp: float | None = None):
        # 1. Observe
        res = await original_observe(node_key, value, timestamp)
        
        # 2. Let Watcher inspect
        graph_id = engine.get_node_id(node_key)
        if graph_id:
            obs = SensorObservation(node_id=graph_id, value=value, timestamp=timestamp or 0.0)
            alert = await watcher.on_observation(obs)
            
            if alert:
                log.warning(f"Watcher fired alert: {alert.message}")
                # Broadcast via WebSocket
                await manager.broadcast(alert.model_dump())
        return res
        
    engine.observe = hooked_observe
    
    # Run the rest of the simulation slowly
    await sim.run()

# -----------------------------------------------------------------------------
# Application Lifespan
# -----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting Clasp backend API...")
    
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
    
    import os
    if os.environ.get("CLASP_ENV") != "test":
        app.state.demo_task = asyncio.create_task(run_tep_demo(engine, watcher))
    else:
        log.info("Running in test mode: disabled background demo task.")
    
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
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For dev, allow all
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
