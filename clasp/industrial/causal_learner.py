# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/causal_learner.py
====================================
Causal discovery loop for industrial time-series data.

On every significant sensor observation, scans the TimeBuffer for correlated
changes in a forward time window and builds causal edges in the KnowledgeGraph.

Algorithm (per observation):
  1. Z-score gate: is this value significant? If not, skip.
  2. Window scan: find all other nodes with significant changes in [t, t+time_window].
  3. Update evidence counters: (cause_node, effect_node) -> {occurrences, lag_sum}
  4. Threshold check: if occurrences >= min_occurrences AND confidence >= min_confidence:
       → write/reinforce a "causes_with_lag" edge in KnowledgeGraph

The KnowledgeGraph's add_edge() auto-reinforces existing typed edges (+0.1 strength),
so calling it repeatedly for confirmed causal pairs naturally strengthens well-evidenced edges.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from clasp.industrial.schemas import (
    CausalPattern,
    IndustrialEdgeType,
    SensorObservation,
)
from clasp.industrial.time_buffer import TimeBuffer
from clasp.vendor.silex.models.schemas import CausalEdge
from clasp.vendor.silex.world.graph import KnowledgeGraph

log = logging.getLogger("clasp.causal_learner")


# ---------------------------------------------------------------------------
# Evidence accumulator (in-memory, not persisted between restarts)
# ---------------------------------------------------------------------------

@dataclass
class EdgeEvidence:
    """
    Running evidence for a potential causal pair (cause → effect).
    Created on first co-occurrence, updated on each subsequent observation.
    """
    occurrences: int = 0            # how many times cause was followed by effect
    total_cause_events: int = 0     # total significant cause events (for confidence calc)
    lag_sum: float = 0.0            # cumulative lag (seconds) for averaging
    last_cause_time: float = 0.0    # timestamp of most recent cause event


class CausalLearner:
    """
    Discovers causal relationships from time-series observations.

    Called by IndustrialSilexEngine on every observe() call.
    Maintains in-memory evidence counters and writes to the KnowledgeGraph
    when evidence crosses the configured thresholds.
    """

    def __init__(
        self,
        graph: KnowledgeGraph,
        time_buffer: TimeBuffer,
        time_window: int = 3600,
        min_occurrences: int = 5,
        min_confidence: float = 0.7,
        significance_z: float = 2.0,
    ):
        """
        Args:
            graph:            KnowledgeGraph instance — edges are written here.
            time_buffer:      TimeBuffer — used to scan for co-occurring changes.
            time_window:      Scan forward this many seconds for effects (default 1 hour).
            min_occurrences:  Minimum co-occurrence count before asserting causality.
            min_confidence:   Minimum confidence (occurrences / total_cause_events).
            significance_z:   Z-score threshold for "significant" observation.
        """
        self.graph = graph
        self.time_buffer = time_buffer
        self.time_window = time_window
        self.min_occurrences = min_occurrences
        self.min_confidence = min_confidence
        self.significance_z = significance_z

        # (cause_node_id, effect_node_id) -> EdgeEvidence
        self._evidence: dict[tuple[str, str], EdgeEvidence] = defaultdict(EdgeEvidence)

        # Track edges we've already written to avoid redundant DB writes
        # Set of (cause_node_id, effect_node_id) pairs
        self._confirmed_edges: set[tuple[str, str]] = set()

        self._observation_count = 0
        self._edge_write_count = 0

    # ------------------------------------------------------------------
    # Main entry point — called per observation
    # ------------------------------------------------------------------

    async def on_observation(self, obs: SensorObservation) -> list[CausalPattern]:
        """
        Process a new sensor observation and update causal evidence.

        Returns a list of CausalPattern objects that crossed the threshold
        in THIS call (newly confirmed or just reinforced). Usually empty.
        """
        self._observation_count += 1

        # Step 1: Significance gate
        if not self.time_buffer.is_significant(obs.node_id, obs.value, self.significance_z):
            return []

        cause_node_id = obs.node_id
        cause_time = obs.timestamp

        # Step 2: Scan forward window for effects
        effect_window_end = cause_time + self.time_window
        significant_effects = self.time_buffer.get_significant_changes_in_window(
            since=cause_time,
            until=effect_window_end,
            z_threshold=self.significance_z,
            exclude_node=cause_node_id,
        )

        # Step 3: Increment total cause events for this cause node
        # (used to compute confidence denominator)
        for eff_node_id, _, _ in significant_effects:
            evidence_key = (cause_node_id, eff_node_id)
            ev = self._evidence[evidence_key]
            ev.total_cause_events += 1

        # Also increment for pairs we track even if no effects seen this time
        # (needed for confidence calc — cause fired but effect didn't)
        # We track all pairs that have EVER had a cause event
        for (cn, _), ev in self._evidence.items():
            if cn == cause_node_id:
                ev.total_cause_events += 1

        # Step 4: Update evidence for each (cause, effect) pair
        newly_confirmed: list[CausalPattern] = []
        for eff_node_id, eff_time, _ in significant_effects:
            lag = eff_time - cause_time
            if lag < 0:
                continue  # skip — effect preceded cause in this scan window
            lag = max(0.0, lag)

            evidence_key = (cause_node_id, eff_node_id)
            ev = self._evidence[evidence_key]
            ev.occurrences += 1
            ev.lag_sum += lag
            ev.last_cause_time = cause_time
            # Reset denominator: use occurrences as proxy for now
            # (full denominator tracking done above)
            if ev.total_cause_events == 0:
                ev.total_cause_events = ev.occurrences

            # Compute confidence = occurrences / total_cause_events
            confidence = ev.occurrences / max(ev.total_cause_events, 1)
            avg_lag = ev.lag_sum / ev.occurrences

            # Step 5: Threshold check — write/reinforce edge
            if ev.occurrences >= self.min_occurrences and confidence >= self.min_confidence:
                pattern = CausalPattern(
                    precursor_node=cause_node_id,
                    outcome_node=eff_node_id,
                    lag_seconds=avg_lag,
                    confidence=confidence,
                    occurrences=ev.occurrences,
                    total_precursor_events=ev.total_cause_events,
                )
                await self._write_edge(pattern)
                newly_confirmed.append(pattern)

        return newly_confirmed

    # ------------------------------------------------------------------
    # Edge writing
    # ------------------------------------------------------------------

    async def _write_edge(self, pattern: CausalPattern) -> None:
        """
        Write or reinforce a causes_with_lag edge in the KnowledgeGraph.

        The KnowledgeGraph.add_edge() already handles reinforcement:
        if a typed edge (source, target, edge_type) already exists, it
        increments its strength by 0.1 (capped at 1.0) instead of inserting.
        """
        edge_key = (pattern.precursor_node, pattern.outcome_node)

        # Only check that BOTH nodes exist in the graph before writing
        if pattern.precursor_node not in self.graph.graph:
            log.debug(
                "Skipping edge — cause node not in graph: %s", pattern.precursor_node[:16]
            )
            return
        if pattern.outcome_node not in self.graph.graph:
            log.debug(
                "Skipping edge — effect node not in graph: %s", pattern.outcome_node[:16]
            )
            return

        evidence_text = (
            f"Observed {pattern.occurrences} times "
            f"with avg lag {pattern.lag_seconds:.0f}s "
            f"(confidence {pattern.confidence:.2f})"
        )

        edge = CausalEdge(
            source_node=pattern.precursor_node,
            target_node=pattern.outcome_node,
            # CausalEdge.edge_type must be one of the 8 valid EdgeType values.
            # 'causes' is the semantically correct mapping for temporal causal links.
            # The lag information is preserved in the evidence string.
            edge_type="causes",
            strength=min(1.0, pattern.confidence),
            evidence=evidence_text,
        )

        try:
            await self.graph.add_edge(edge)
            if edge_key not in self._confirmed_edges:
                self._confirmed_edges.add(edge_key)
                self._edge_write_count += 1
                log.info(
                    "New causal edge: %s -> %s (lag=%.0fs conf=%.2f obs=%d)",
                    pattern.precursor_node[:20],
                    pattern.outcome_node[:20],
                    pattern.lag_seconds,
                    pattern.confidence,
                    pattern.occurrences,
                )
            else:
                log.debug(
                    "Reinforced edge: %s -> %s",
                    pattern.precursor_node[:20],
                    pattern.outcome_node[:20],
                )
        except Exception as e:
            log.error("Failed to write edge %s -> %s: %s", pattern.precursor_node, pattern.outcome_node, e)

    # ------------------------------------------------------------------
    # Backward scan (for root cause analysis enrichment)
    # ------------------------------------------------------------------

    def get_likely_causes(self, node_id: str, event_time: float) -> list[tuple[str, float, float]]:
        """
        Given a node and event time, return likely causes from evidence.

        Returns list of (cause_node_id, estimated_lag, confidence) sorted by confidence desc.
        This is a fast in-memory lookup (no graph query) for the backward BFS.
        """
        results = []
        for (cause, effect), ev in self._evidence.items():
            if effect != node_id:
                continue
            if ev.occurrences < self.min_occurrences:
                continue
            confidence = ev.occurrences / max(ev.total_cause_events, 1)
            avg_lag = ev.lag_sum / ev.occurrences if ev.occurrences > 0 else 0.0
            results.append((cause, avg_lag, confidence))

        results.sort(key=lambda x: x[2], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Stats / introspection
    # ------------------------------------------------------------------

    def get_all_patterns(self) -> list[CausalPattern]:
        """Return all accumulated CausalPattern evidence, including sub-threshold."""
        patterns = []
        for (cause, effect), ev in self._evidence.items():
            if ev.occurrences == 0:
                continue
            confidence = ev.occurrences / max(ev.total_cause_events, 1)
            avg_lag = ev.lag_sum / ev.occurrences if ev.occurrences > 0 else 0.0
            patterns.append(CausalPattern(
                precursor_node=cause,
                outcome_node=effect,
                lag_seconds=avg_lag,
                confidence=confidence,
                occurrences=ev.occurrences,
                total_precursor_events=ev.total_cause_events,
            ))
        return sorted(patterns, key=lambda p: p.confidence, reverse=True)

    def stats(self) -> dict:
        """Return learner runtime statistics."""
        return {
            "observations_processed": self._observation_count,
            "evidence_pairs_tracked": len(self._evidence),
            "confirmed_edges_written": self._edge_write_count,
            "above_threshold_patterns": sum(
                1 for (_, _), ev in self._evidence.items()
                if ev.occurrences >= self.min_occurrences
                and (ev.occurrences / max(ev.total_cause_events, 1)) >= self.min_confidence
            ),
        }
