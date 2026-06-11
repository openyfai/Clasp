# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/agents/physics_validator.py
============================================
An agent that cross-references proposed causal edges against
basic physical and thermodynamic laws to prevent spurious correlations.
"""

from __future__ import annotations

import logging
from pydantic import BaseModel

from clasp.vendor.silex.llm.factory import build_provider

log = logging.getLogger("clasp.agents.physics_validator")


class PhysicsValidationResponse(BaseModel):
    is_possible: bool
    reason: str


class PhysicsValidatorAgent:
    """
    Validates statistical correlations using an LLM to ensure they are physically possible.
    """

    def __init__(self):
        try:
            self.llm = build_provider()
        except ValueError as e:
            log.warning(f"Failed to build LLM provider ({e}). Physics validation will default to True.")
            self.llm = None

    async def validate_edge(self, cause_node: str, effect_node: str) -> bool:
        """
        Cross-references basic laws to check if cause_node can physically affect effect_node.
        """
        if self.llm is None:
            # Fallback to statistical edge if LLM is unavailable
            return True

        prompt = f"""You are a strict industrial engineer and physicist verifying a causal graph.
A statistical correlation was found suggesting that changes in '{cause_node}' cause changes in '{effect_node}'.
According to the laws of chemistry, physics, and thermodynamics, is it physically possible for '{cause_node}' to directly cause a change in '{effect_node}'?

Consider common industrial processes. If the names refer to variables that could plausibly affect each other in a plant (e.g. flow causing pressure, temperature causing quality, valve causing flow), reply with is_possible = true.
If the correlation is clearly impossible or highly improbable (e.g. a downstream sensor causing an upstream valve to actuate without a controller, or totally unrelated systems), reply with is_possible = false.
Provide a brief 1-sentence reason.
"""

        log.debug(f"PhysicsValidator verifying edge: {cause_node} -> {effect_node}")
        try:
            response: PhysicsValidationResponse = await self.llm.complete_json(
                schema=PhysicsValidationResponse,
                system_prompt="You are a strict physics and industrial engineering validation agent. Prioritize rejecting physically impossible spurious correlations.",
                user_input=prompt
            )
            
            if not response.is_possible:
                log.info(f"PhysicsValidator REJECTED edge {cause_node} -> {effect_node}. Reason: {response.reason}")
            
            return response.is_possible

        except Exception as e:
            log.error(f"PhysicsValidator LLM failed: {e}. Defaulting to True to preserve statistical edge.")
            return True
