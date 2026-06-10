# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/ingest/csv_adapter.py
=======================================
Generic historical CSV adapter for IndustrialSilexEngine.
Reads any time-series CSV and maps columns to engine.observe() calls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from typing import Any

import pandas as pd

from clasp.industrial.engine import IndustrialSilexEngine

log = logging.getLogger("clasp.ingest.csv")


class CSVAdapter:
    """
    Reads a time-series CSV and feeds it into the IndustrialSilexEngine.
    """

    def __init__(
        self,
        engine: IndustrialSilexEngine,
        csv_path: str | Path,
        column_map: dict[str, str],
        time_col: str,
        speed: float = 1.0,
        time_format: str | None = None,
    ):
        """
        Args:
            engine:      Initialized IndustrialSilexEngine.
            csv_path:    Path to the CSV file.
            column_map:  Dict mapping {csv_column_name: clasp_node_id}.
            time_col:    Name of the time column.
            speed:       Playback speed multiplier (e.g., 10.0 = 10x real-time).
                         If <= 0, feeds data instantly without sleeping.
            time_format: Format string if time is datetime, or None if it's already unix seconds.
        """
        self.engine = engine
        self.csv_path = Path(csv_path)
        self.column_map = column_map
        self.time_col = time_col
        self.speed = speed
        self.time_format = time_format

    async def run(self, max_rows: int | None = None) -> None:
        """
        Stream the CSV data into the engine.

        Args:
            max_rows: Optional limit on number of rows to process.
        """
        if not self.csv_path.exists():
            raise FileNotFoundError(f"CSV not found: {self.csv_path}")

        log.info(f"Loading CSV: {self.csv_path}")
        df = pd.read_csv(self.csv_path)

        if max_rows is not None:
            df = df.head(max_rows)

        # Convert time column to Unix seconds if needed
        if self.time_format is not None:
            df[self.time_col] = pd.to_datetime(df[self.time_col], format=self.time_format).astype(int) / 10**9
        else:
            # Assume it's already numeric (seconds or hours or ms).
            # If the user has custom time scaling (e.g. hours), they should transform it beforehand,
            # or we assume it's seconds. The TEP simulator handles its own hour->second conversion.
            df[self.time_col] = pd.to_numeric(df[self.time_col])

        # Filter to only the columns we need to process to save memory
        cols_to_keep = [self.time_col] + list(self.column_map.keys())
        # ensure all mapped columns actually exist in df
        existing_cols = [c for c in cols_to_keep if c in df.columns]
        df = df[existing_cols]

        log.info(f"Starting playback of {len(df)} rows at {self.speed}x speed.")

        previous_sim_time = None
        start_real_time = time.time()
        rows_processed = 0

        for _, row in df.iterrows():
            sim_time = float(row[self.time_col])

            # Sleep to match simulation speed
            if self.speed > 0 and previous_sim_time is not None:
                sim_delta = sim_time - previous_sim_time
                if sim_delta > 0:
                    real_delta = sim_delta / self.speed
                    await asyncio.sleep(real_delta)

            previous_sim_time = sim_time

            # Feed observations
            for csv_col, node_id in self.column_map.items():
                if csv_col in row and pd.notna(row[csv_col]):
                    val = float(row[csv_col])
                    await self.engine.observe(node_key=node_id, value=val, timestamp=sim_time)

            rows_processed += 1
            if rows_processed % 1000 == 0:
                log.info(f"Processed {rows_processed}/{len(df)} rows...")

        elapsed = time.time() - start_real_time
        log.info(f"CSV playback complete. {rows_processed} rows in {elapsed:.2f}s real time.")
