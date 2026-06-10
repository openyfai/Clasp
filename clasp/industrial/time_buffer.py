# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/time_buffer.py
================================
Rolling in-memory time-series buffer for plant sensor readings.

Stores the last `window_seconds` of observations per node.
Used by CausalLearner to:
  - detect significant changes (Z-score gating)
  - scan for co-occurring changes (forward/backward windows)

Pure pandas + numpy — no graph dependency. Safe to test independently.
"""

from __future__ import annotations

import time
from collections import defaultdict

import numpy as np
import pandas as pd


class TimeBuffer:
    """
    Rolling in-memory time-series buffer.

    Each node gets its own list of (timestamp, value) observations.
    When the buffer exceeds `window_seconds`, old entries are pruned.

    Thread safety: single-threaded asyncio use only. The buffer is
    mutated only by engine.observe() which runs in the event loop.
    """

    def __init__(self, window_seconds: int = 1209600):
        """
        Args:
            window_seconds: How many seconds of history to keep per node.
                            Default 1209600 = 14 days (handles temporary telemetry buffer garbage collection).
        """
        self.window_seconds = window_seconds
        # node_id -> list of [timestamp (float), value (float)]
        self._data: dict[str, list[list[float]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(self, node_id: str, value: float, timestamp: float | None = None) -> None:
        """Record a new sensor reading for a node."""
        ts = timestamp if timestamp is not None else time.time()
        self._data[node_id].append([ts, value])
        self._prune(node_id, ts)

    def _prune(self, node_id: str, now: float) -> None:
        """Remove observations older than window_seconds."""
        cutoff = now - self.window_seconds
        buf = self._data[node_id]
        # Find the first index that is within the window (binary-ish skip from front)
        if buf and buf[0][0] < cutoff:
            # Keep only entries >= cutoff
            self._data[node_id] = [row for row in buf if row[0] >= cutoff]

    # ------------------------------------------------------------------
    # Read — single node
    # ------------------------------------------------------------------

    def get_latest(self, node_id: str) -> float | None:
        """Return the most recent value for a node, or None if unseen."""
        buf = self._data.get(node_id)
        if not buf:
            return None
        return buf[-1][1]

    def get_latest_timestamp(self, node_id: str) -> float | None:
        """Return the most recent timestamp for a node, or None if unseen."""
        buf = self._data.get(node_id)
        if not buf:
            return None
        return buf[-1][0]

    def get_history(self, node_id: str, since: float | None = None) -> pd.DataFrame:
        """
        Return the history of a node as a DataFrame with columns [timestamp, value].

        Args:
            node_id: The node to fetch history for.
            since:   Only return readings after this unix timestamp.
                     If None, returns all buffered history.
        """
        buf = self._data.get(node_id, [])
        if not buf:
            return pd.DataFrame(columns=["timestamp", "value"])
        df = pd.DataFrame(buf, columns=["timestamp", "value"])
        if since is not None:
            df = df[df["timestamp"] >= since]
        return df.reset_index(drop=True)

    def get_value_near(self, node_id: str, target_time: float, tolerance_seconds: float = 300.0) -> float | None:
        """
        Return the value of a node at approximately target_time, within tolerance.
        Used by root cause analysis to fetch historical values.

        Returns the observation closest to target_time within ±tolerance_seconds,
        or None if no observation exists in that window.
        """
        buf = self._data.get(node_id)
        if not buf:
            return None
        lo = target_time - tolerance_seconds
        hi = target_time + tolerance_seconds
        candidates = [(abs(row[0] - target_time), row[1]) for row in buf if lo <= row[0] <= hi]
        if not candidates:
            return None
        return min(candidates, key=lambda x: x[0])[1]

    # ------------------------------------------------------------------
    # Significance detection (Z-score)
    # ------------------------------------------------------------------

    def rolling_mean_std(self, node_id: str, min_samples: int = 5) -> tuple[float, float] | None:
        """
        Compute the rolling mean and std of recent observations for a node.

        Args:
            node_id:     The node to compute stats for.
            min_samples: Minimum number of observations required.
                         Returns None if fewer observations exist.

        Returns:
            (mean, std) tuple, or None if insufficient data.
        """
        buf = self._data.get(node_id)
        if not buf or len(buf) < min_samples:
            return None
        values = np.array([row[1] for row in buf])
        std = float(np.std(values))
        return float(np.mean(values)), std

    def is_significant(self, node_id: str, value: float, z_threshold: float = 2.0) -> bool:
        """
        Test whether `value` is a statistically significant deviation from recent history.

        Uses Z-score: |value - mean| / std > z_threshold.

        Args:
            node_id:     The node being tested.
            value:       The new observed value.
            z_threshold: Z-score threshold. Default 2.0 (≈95th percentile of normal dist).

        Returns:
            True if the value is significant (worth processing for causal learning).
            Also returns True if fewer than 5 historical observations (can't compute baseline).
        """
        stats = self.rolling_mean_std(node_id)
        if stats is None:
            # Not enough history — treat first few readings as always significant
            return True
        mean, std = stats
        if std < 1e-9:
            # Constant signal — any non-zero deviation is significant
            return abs(value - mean) > 1e-9
        z_score = abs(value - mean) / std
        return z_score >= z_threshold

    def z_score(self, node_id: str, value: float) -> float | None:
        """
        Return the Z-score of `value` relative to the node's rolling history.
        Returns None if insufficient history.
        """
        stats = self.rolling_mean_std(node_id)
        if stats is None:
            return None
        mean, std = stats
        if std < 1e-9:
            return 0.0
        return abs(value - mean) / std

    # ------------------------------------------------------------------
    # Multi-node scans (used by CausalLearner)
    # ------------------------------------------------------------------

    def get_significant_changes_in_window(
        self,
        since: float,
        until: float,
        z_threshold: float = 2.0,
        exclude_node: str | None = None,
    ) -> list[tuple[str, float, float]]:
        """
        Scan ALL buffered nodes for significant changes between [since, until].

        Used by CausalLearner to find potential effects after a cause event.

        Args:
            since:        Start of window (unix seconds).
            until:        End of window (unix seconds).
            z_threshold:  Z-score threshold for "significant" change.
            exclude_node: Node to skip (typically the node that triggered the scan).

        Returns:
            List of (node_id, timestamp, value) tuples for all significant readings
            that occurred within [since, until], sorted by timestamp.
        """
        results: list[tuple[str, float, float]] = []
        for node_id, buf in self._data.items():
            if node_id == exclude_node:
                continue
            for ts, val in buf:
                if since <= ts <= until:
                    if self.is_significant(node_id, val, z_threshold):
                        results.append((node_id, ts, val))

        results.sort(key=lambda x: x[1])
        return results

    # ------------------------------------------------------------------
    # Stats / introspection
    # ------------------------------------------------------------------

    def known_nodes(self) -> list[str]:
        """Return all node IDs that have at least one buffered observation."""
        return [nid for nid, buf in self._data.items() if buf]

    def current_state(self) -> dict[str, float]:
        """Return the latest value for every known node. Useful for /api/plant/status."""
        return {
            node_id: buf[-1][1]
            for node_id, buf in self._data.items()
            if buf
        }

    def observation_count(self, node_id: str) -> int:
        """Return the number of buffered observations for a node."""
        return len(self._data.get(node_id, []))

    def total_observations(self) -> int:
        """Return total buffered observations across all nodes."""
        return sum(len(buf) for buf in self._data.values())
