# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/agents/watcher_agent.py
=========================================
Continuous background monitor that flags precursors and fires early warnings.
"""

from __future__ import annotations

import logging
import time

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.schemas import Alert, IndustrialNodeType, SensorObservation

log = logging.getLogger("clasp.agents.watcher")


class WatcherAgent:
    """
    Watches incoming observations, matching them against known causal precursor patterns
    to fire early-warning Alerts before QualityMetrics or AlarmEvents drop/trigger.
    """

    def __init__(self, engine: IndustrialSilexEngine):
        self.engine = engine
        # Map: precursor_node_id -> list of outcome_node_ids
        self.precursor_map: dict[str, list[str]] = {}
        
    async def startup(self) -> None:
        """
        Pre-load causal paths leading to critical nodes.
        """
        log.info("WatcherAgent starting up, indexing critical pathways...")
        self.precursor_map.clear()
        
        if not self.engine.graph or not self.engine.graph.graph:
            return

        nx_graph = self.engine.graph.graph
        
        # Find all critical outcome nodes
        critical_nodes = []
        for nid, data in nx_graph.nodes(data=True):
            ntype = data.get("node_type")
            # In our schema mapping, quality metrics might be stored in metadata
            meta = data.get("metadata", {})
            industrial_type = meta.get("industrial_type", ntype)
            
            if industrial_type in (IndustrialNodeType.QUALITY_METRIC.value, IndustrialNodeType.ALARM_EVENT.value):
                critical_nodes.append(nid)

        # For each critical node, find its precursors (1-hop for now)
        for c_node in critical_nodes:
            edges = self.engine._find_incoming_causal_edges(c_node)
            # engine._find_incoming_causal_edges returns list[tuple[str, float, float]]
            # (source_id, lag_seconds, strength)
            for src, lag, strength in edges:
                if src not in self.precursor_map:
                    self.precursor_map[src] = []
                if c_node not in self.precursor_map[src]:
                    self.precursor_map[src].append(c_node)
                    
        log.info(f"WatcherAgent indexing complete. Found {len(self.precursor_map)} active precursors.")

    async def on_observation(self, obs: SensorObservation) -> Alert | None:
        """
        Check if the incoming observation triggers any known precursor patterns.
        """
        if not self.precursor_map:
            # Re-index occasionally if empty, or rely on manual re-indexing
            await self.startup()

        # Is this node a known precursor to something bad?
        if obs.node_id not in self.precursor_map:
            return None

        # Check if the value is statistically significant
        buffer = self.engine._time_buffer
        if not buffer.is_significant(obs.node_id, obs.value, z_threshold=self.engine._significance_z):
            return None

        # It is a precursor AND it's anomalous! Fire an alert.
        outcomes = self.precursor_map[obs.node_id]
        
        # Build the alert for the first matched outcome for simplicity
        outcome_id = outcomes[0]
        outcome_label = self.engine._node_id_to_label.get(outcome_id, outcome_id)
        precursor_label = self.engine._node_id_to_label.get(obs.node_id, obs.node_id)
        
        # Estimate lag by checking the causal edge again
        # We could cache this in startup, but doing a quick lookup here is fine
        edges = self.engine._find_incoming_causal_edges(outcome_id)
        lag = 0.0
        confidence = 0.5
        for src, e_lag, strngth in edges:
            if src == obs.node_id:
                lag = e_lag
                confidence = strngth
                break
                
        alert = Alert(
            type="early_warning",
            outcome_risk=outcome_label,
            pattern=f"Anomalous behavior detected in {precursor_label}",
            estimated_time_to_impact=lag,
            confidence=confidence,
            created_at=time.time()
        )
        
        log.warning(f"🚨 ALERT FIRED: {alert.pattern} -> Risk of {alert.outcome_risk} in {lag}s!")
        return alert
