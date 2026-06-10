# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/agents/optimizer_agent.py
===========================================
Optimizer Agent that queries the graph for controllable variables (XMV)
that strongly influence output metrics.
"""

from __future__ import annotations

import logging
from typing import Any

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.schemas import IndustrialNodeType

log = logging.getLogger("clasp.agents.optimizer")


class OptimizerAgent:
    """
    Finds parameter recommendations based on historical causal patterns.
    """

    def __init__(self, engine: IndustrialSilexEngine):
        self.engine = engine

    async def get_recommendations(self, target_metric_node: str) -> list[dict[str, Any]]:
        """
        Query the graph for actionable variables that cause changes in target_metric_node.
        """
        log.info(f"OptimizerAgent analyzing optimization paths for {target_metric_node}...")
        
        if not self.engine.graph or not self.engine.graph.graph:
            return []

        # Find 1-hop and 2-hop precursors
        # Real version would do a broader search specifically for controllable inputs
        # For our simplified simulation, we just find all known causes.
        
        recommendations = []
        visited = set()
        
        def traverse(node_id, depth, accumulated_confidence):
            if depth > 3 or node_id in visited:
                return
            visited.add(node_id)
            
            edges = self.engine._find_incoming_causal_edges(node_id)
            for src, lag_sec, strength in edges:
                conf = strength * accumulated_confidence
                
                # Check if src is an OperatorAction or controllable ProcessVariable (e.g., XMV)
                data = self.engine.graph.graph.nodes[src]
                ntype = data.get("node_type")
                meta = data.get("metadata", {})
                ind_type = meta.get("industrial_type", ntype)
                
                # In TEP, XMV (manipulated variables) are our controllable inputs.
                # The label or ID usually indicates this.
                label = self.engine._node_id_to_label.get(src, src)
                is_controllable = "XMV" in label or ind_type == IndustrialNodeType.OPERATOR_ACTION.value
                
                if is_controllable and conf > 0.1:
                    # In a real app we would get evidence counts, but for now we just 
                    # provide the node metrics.
                    recommendations.append({
                        "actionable_node": src,
                        "actionable_label": label,
                        "target_node": target_metric_node,
                        "predicted_improvement_confidence": conf,
                        "evidence_count": 0, # Placeholder
                        "lag_seconds": lag_sec
                    })
                
                traverse(src, depth + 1, conf)

        traverse(target_metric_node, 1, 1.0)
        
        # Sort by highest confidence
        recommendations.sort(key=lambda x: x["predicted_improvement_confidence"], reverse=True)
        
        # Deduplicate actionable nodes, keeping highest path confidence
        seen_nodes = set()
        deduped = []
        for r in recommendations:
            if r["actionable_node"] not in seen_nodes:
                seen_nodes.add(r["actionable_node"])
                deduped.append(r)
                
        return deduped
