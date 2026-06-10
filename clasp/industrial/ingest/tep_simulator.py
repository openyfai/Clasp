# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/ingest/tep_simulator.py
=========================================
Tennessee Eastman Process (TEP) simulator ingestion adapter.
Reads the space-separated .dat files and feeds them into the engine.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import pandas as pd

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.schemas import IndustrialNodeType

log = logging.getLogger("clasp.ingest.tep")


class TEPSimulator:
    """
    Replays Tennessee Eastman Process data from standard .dat files.
    """

    def __init__(self, engine: IndustrialSilexEngine, data_path: str | Path, speed: float = 1.0):
        """
        Args:
            engine:    The initialized engine.
            data_path: Path to the .dat file (e.g. d00.dat).
            speed:     Playback speed multiplier (e.g. 500.0). If <= 0, feeds instantly.
        """
        self.engine = engine
        self.data_path = Path(data_path)
        self.speed = speed

    async def register_tep_nodes(self) -> None:
        """
        Register all 53 TEP variables (XMEAS 1-41, XMV 1-12) as nodes in the engine.
        """
        log.info("Registering 53 TEP nodes in the engine...")
        
        # XMEAS 1 to 41 (Process Variables, except 35 which is Quality)
        for i in range(1, 42):
            node_id = f"xmeas_{i}"
            label = f"XMEAS({i}) Variable"
            node_type = IndustrialNodeType.QUALITY_METRIC if i == 35 else IndustrialNodeType.PROCESS_VARIABLE
            await self.engine.register_node(node_id, label, node_type)

        # XMV 1 to 12 (Manipulated Variables -> Equipment Units / Process Variables)
        for i in range(1, 13):
            node_id = f"xmv_{i}"
            label = f"XMV({i}) Manipulated Variable"
            await self.engine.register_node(node_id, label, IndustrialNodeType.PROCESS_VARIABLE)

    async def run(self, max_rows: int | None = None) -> None:
        """
        Read the .dat file and stream it into the engine.
        """
        if not self.data_path.exists():
            raise FileNotFoundError(f"TEP data file not found: {self.data_path}")

        log.info(f"Loading TEP data: {self.data_path}")
        
        # .dat files are space-separated, no headers. 
        # Usually 53 or 54 columns depending on trailing spaces.
        df = pd.read_csv(self.data_path, sep=r'\s+', header=None)
        
        if max_rows is not None:
            df = df.head(max_rows)

        # Build column mapping: 
        # Col 0-40: XMEAS_1 to XMEAS_41
        # Col 41-52: XMV_1 to XMV_12
        # Col 53: Time (hours) (in some variants it might be col 0. 
        # Wait, the prompt plan says col 53 is time in hours. Let's trust the plan.)
        
        # We will dynamically map indices
        col_map = {}
        for i in range(41):
            col_map[i] = f"xmeas_{i+1}"
        for i in range(12):
            col_map[41+i] = f"xmv_{i+1}"
            
        time_col_idx = 53

        log.info(f"Starting playback of {len(df)} TEP rows at {self.speed}x speed.")
        
        previous_sim_time = None
        start_real_time = time.time()
        rows_processed = 0

        for _, row in df.iterrows():
            if time_col_idx >= len(row):
                # Fallback if time col isn't found at 53 (some dataset variations have time as index or omitted)
                # We'll just assume regular sampling intervals (e.g. 3 mins = 0.05 hours) if missing.
                # But standard Rieth et al. / Braatz has time.
                log.warning(f"Time column {time_col_idx} missing, assuming uniform 3 min intervals.")
                sim_time_hours = rows_processed * 0.05 
            else:
                sim_time_hours = float(row[time_col_idx])
                
            # Convert hours to Unix seconds (arbitrary epoch start is fine for relative causality)
            sim_time_seconds = sim_time_hours * 3600.0

            # Sleep to match simulation speed
            if self.speed > 0 and previous_sim_time is not None:
                sim_delta = sim_time_seconds - previous_sim_time
                if sim_delta > 0:
                    real_delta = sim_delta / self.speed
                    await asyncio.sleep(real_delta)

            previous_sim_time = sim_time_seconds

            # Feed observations
            for col_idx, node_id in col_map.items():
                if col_idx < len(row) and pd.notna(row[col_idx]):
                    val = float(row[col_idx])
                    await self.engine.observe(node_key=node_id, value=val, timestamp=sim_time_seconds)

            rows_processed += 1
            if rows_processed % 1000 == 0:
                log.info(f"Processed {rows_processed}/{len(df)} rows...")

        elapsed = time.time() - start_real_time
        log.info(f"TEP playback complete. {rows_processed} rows in {elapsed:.2f}s real time.")
