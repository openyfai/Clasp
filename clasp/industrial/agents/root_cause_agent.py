# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/agents/root_cause_agent.py
============================================
Root cause analysis agent that triggers a backward causal search and
uses the Silex LLM to explain the chain in plain English.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.vendor.silex.llm.factory import build_provider

log = logging.getLogger("clasp.agents.root_cause")

class RootCauseExplanation(BaseModel):
    explanation: str

class RootCauseAgent:
    """
    Triggers RCA on the engine and narrates the causal chain using an LLM.
    """

    def __init__(self, engine: IndustrialSilexEngine):
        self.engine = engine
        try:
            self.llm = build_provider()
        except ValueError as e:
            log.warning(f"Failed to build LLM provider ({e}). Using mock fallback.")
            self.llm = None

    async def investigate(self, affected_node: str, event_time: float, max_depth: int = 5) -> dict[str, Any]:
        """
        Runs the RCA trace and generates an LLM narration.

        Returns:
            {
                "chain": list of dicts (the raw trace),
                "explanation": str (plain English explanation)
            }
        """
        log.info(f"RootCauseAgent investigating node {affected_node} at {event_time}...")
        
        # 1. Get raw trace from the deterministic engine
        rca_result = await self.engine.root_cause_analysis(affected_node, event_time, max_depth)
        chain = [step.model_dump() for step in rca_result.chain]

        if not chain or len(chain) == 1:
            return {
                "chain": chain,
                "explanation": f"No precursors found for {affected_node}. It may be the source of the issue."
            }

        # 2. Build structured prompt
        chain_lines = []
        for step in chain:
            time_offset = event_time - step["timestamp"]
            mins_ago = int(time_offset / 60)
            val = step["value"]
            nid = step["node_id"]
            node_label = self.engine._node_id_to_label.get(nid, nid)
            val_str = f"{val:.2f}" if val is not None else "Unknown"
            chain_lines.append(f"  [{mins_ago} mins ago] {node_label} changed to {val_str}")

        chain_str = "\n".join(chain_lines)

        prompt = f"""You are a plant engineer assistant. Here is a causal chain of events traced by our deterministic engine, ending in an alert on {affected_node}:

{chain_str}

In 3-5 sentences, explain this sequence of events to a plant operator. 
Use plain language. Be direct. Do NOT hallucinate any events not listed in the chain above.
"""

        # 3. Call LLM
        log.info("Requesting LLM explanation of the causal chain...")
        if self.llm is None:
            explanation = f"Mock LLM Explanation: The root cause trace indicates {affected_node} was affected by a chain of events starting {len(chain)} steps prior."
        else:
            try:
                # complete_json returns the Pydantic model directly
                response = await self.llm.complete_json(
                    schema=RootCauseExplanation,
                    system_prompt="You are a clear, concise industrial engineer.",
                    user_input=prompt
                )
                explanation = response.explanation
            except Exception as e:
                log.error(f"LLM generation failed: {e}")
                explanation = "Error generating explanation. Please review the raw chain."

        return {
            "chain": chain,
            "explanation": explanation
        }
