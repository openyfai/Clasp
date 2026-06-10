"""
clasp/scripts/run_tep_fault5.py
================================
Runner script to feed TEP d05.dat (Fault 5) into the Industrial Engine.
Demonstrates WatcherAgent alerting and RootCauseAgent tracing.
"""

import argparse
import asyncio
import logging
from pathlib import Path

from clasp.industrial.agents import RootCauseAgent, WatcherAgent
from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.ingest.tep_simulator import TEPSimulator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("clasp.scripts.run_tep_fault5")

async def main():
    parser = argparse.ArgumentParser(description="Run TEP Simulator on Fault 5 data")
    parser.add_argument("--speed", type=float, default=500.0, help="Playback speed multiplier")
    parser.add_argument("--duration", type=int, default=600, help="Maximum number of rows to process")
    args = parser.parse_args()

    # Path to downloaded TEP normal data (for initial training) and fault data
    normal_path = Path("data/tep/d00.dat")
    fault_path = Path("data/tep/d05.dat")
    if not fault_path.exists() or not normal_path.exists():
        log.error("Data files not found. Please run scripts/download_tep.py first.")
        return

    # Initialize Engine
    engine = IndustrialSilexEngine(
        min_occurrences=3,
        min_confidence=0.5,
        significance_z=2.0,  # Stricter significance for fault detection
        time_window=3600
    )
    await engine.initialize()

    watcher = WatcherAgent(engine)
    root_cause = RootCauseAgent(engine)

    try:
        # Phase A: Train the graph rapidly on normal data (first 600 rows)
        log.info("--- PHASE A: Learning normal causality ---")
        train_sim = TEPSimulator(engine=engine, data_path=normal_path, speed=0)
        await train_sim.register_tep_nodes()
        await train_sim.run(max_rows=600)
        
        # Start watcher indexing
        await watcher.startup()

        # Phase B: Run Fault 5
        log.info("--- PHASE B: Injecting Fault 5 ---")
        fault_sim = TEPSimulator(engine=engine, data_path=fault_path, speed=args.speed)
        
        # Intercept observe to pass to watcher
        original_observe = fault_sim.engine.observe
        
        alert_fired = False
        
        async def hooked_observe(node_key: str, value: float, timestamp: float):
            await original_observe(node_key, value, timestamp)
            from clasp.industrial.schemas import SensorObservation
            graph_id = engine.get_node_id(node_key)
            if graph_id:
                obs = SensorObservation(node_id=graph_id, value=value, timestamp=timestamp)
                alert = await watcher.on_observation(obs)
                if alert:
                    nonlocal alert_fired
                    alert_fired = True
                
        fault_sim.engine.observe = hooked_observe
        
        await fault_sim.run(max_rows=args.duration)
        
        log.info("--- PHASE C: Root Cause Analysis ---")
        # Trace back from Quality metric (XMEAS 35) or similar
        explanation = await root_cause.investigate("xmeas_35", event_time=args.duration * 0.05 * 3600)
        log.info(f"LLM Explanation:\n{explanation['explanation']}")
        log.info(f"Raw Chain Steps: {len(explanation['chain'])}")
        
    finally:
        await engine.close()

if __name__ == "__main__":
    asyncio.run(main())
