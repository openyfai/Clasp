"""
clasp/scripts/run_tep_normal.py
================================
Runner script to feed TEP d00.dat into the Industrial Engine.
"""

import argparse
import asyncio
import logging
from pathlib import Path

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.ingest.tep_simulator import TEPSimulator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("clasp.scripts.run_tep_normal")

async def main():
    parser = argparse.ArgumentParser(description="Run TEP Simulator on normal data")
    parser.add_argument("--speed", type=float, default=500.0, help="Playback speed multiplier")
    parser.add_argument("--duration", type=int, default=600, help="Maximum number of rows to process (0 = all)")
    args = parser.parse_args()

    # Path to downloaded TEP normal data
    data_path = Path("data/tep/d00.dat")
    if not data_path.exists():
        log.error(f"Data file not found at {data_path}. Please run scripts/download_tep.py first.")
        return

    # Initialize Engine
    # We use lower threshold configurations here to ensure edges form quickly in testing/demonstration
    engine = IndustrialSilexEngine(
        min_occurrences=3,
        min_confidence=0.5,
        significance_z=1.0,
        time_window=3600
    )
    await engine.initialize()

    try:
        simulator = TEPSimulator(engine=engine, data_path=data_path, speed=args.speed)
        await simulator.register_tep_nodes()

        # Execute
        max_rows = args.duration if args.duration > 0 else None
        await simulator.run(max_rows=max_rows)

        # Print graph stats
        stats = await engine.get_graph_stats()
        log.info(f"Simulation complete. Graph stats: {stats.model_dump()}")
        
    finally:
        await engine.close()

if __name__ == "__main__":
    asyncio.run(main())
