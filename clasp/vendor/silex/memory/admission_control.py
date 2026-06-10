"""
Adaptive Memory Admission Control (A-MAC) Framework.

Implements the S(m) composite scoring function to filter out low-quality,
redundant, or useless memories before they reach the persistent store.

Scores candidates on 5 dimensions:
  1. Utility    (heuristic keyword scoring)
  2. Confidence (ROUGE-L approximation vs context)
  3. Novelty    (semantic distance vs existing memories)
  4. Recency    (exponential decay based on time)
  5. Type Prior (static weighting by information type)
"""

import asyncio
import difflib
from typing import Callable, Coroutine, Dict

from clasp.vendor.silex.utils.config import AMAC_THRESHOLD, AMAC_WEIGHTS
from clasp.vendor.silex.utils.logger import setup_logger

log = setup_logger("silex.admission_control")

# Cap the length of strings for LCS to prevent O(n*m) blocking the event loop
MAX_LCS_LENGTH = 2000


class AdmissionController:
    """Evaluates whether a memory candidate is worth storing."""

    def __init__(self, threshold: float = AMAC_THRESHOLD, weights: list[float] = None):
        self.threshold = threshold
        self.weights = weights or AMAC_WEIGHTS
        # weights = [Utility(0.1), Confidence(0.1), Novelty(0.1), Recency(0.1), TypePrior(0.6)]

    async def evaluate_admission(
        self,
        candidate_content: str,
        content_type: str,
        source_context: str,
        novelty_checker: Callable[[str], Coroutine[None, None, float]],
    ) -> Dict[str, float]:
        """
        Evaluate a candidate memory and return its scores.

        Args:
            candidate_content: The memory text.
            content_type: e.g., 'preference', 'fact', 'plan', 'transient', 'semantic'
            source_context: The text context from which the memory was extracted.
            novelty_checker: Async func that takes candidate string and returns
                             a novelty score [0, 1] (1 = completely novel).

        Returns:
            Dict containing individual scores, composite_score, and 'admitted' bool.
        """
        utility = self.evaluate_future_utility(candidate_content)
        confidence = await self.compute_factual_confidence(candidate_content, source_context)
        novelty = await novelty_checker(candidate_content)
        recency = self.compute_temporal_recency()
        type_prior = self.get_content_type_prior(content_type)

        w_u, w_c, w_n, w_r, w_t = self.weights
        composite_score = (
            (w_u * utility)
            + (w_c * confidence)
            + (w_n * novelty)
            + (w_r * recency)
            + (w_t * type_prior)
        )

        return {
            "utility": utility,
            "confidence": confidence,
            "novelty": novelty,
            "recency": recency,
            "type_prior": type_prior,
            "composite_score": composite_score,
            "admitted": composite_score >= self.threshold,
        }

    def evaluate_future_utility(self, candidate: str) -> float:
        """Rule-based heuristic for future utility."""
        candidate_lower = candidate.lower()
        utility_keywords = [
            "always", "never", "must", "prefer", "error", "failed",
            "password", "key", "token", "remember", "important",
            "api", "endpoint", "path", "directory", "config"
        ]
        matches = sum(1 for kw in utility_keywords if kw in candidate_lower)
        # Cap at 1.0 (5 matches = max utility)
        return min(1.0, matches * 0.20)

    async def compute_factual_confidence(self, candidate: str, context: str) -> float:
        """
        Compute ROUGE-L like confidence via Longest Common Subsequence.
        Runs in a background thread to prevent blocking the event loop.
        """
        if not context:
            return 0.5  # Neutral prior if no context provided

        # Cap length to prevent O(n*m) blowup
        cand_trunc = candidate[:MAX_LCS_LENGTH]
        ctx_trunc = context[:MAX_LCS_LENGTH]

        def _lcs_ratio() -> float:
            matcher = difflib.SequenceMatcher(None, cand_trunc, ctx_trunc)
            # quick_ratio is O(n+m) which is much safer than real ratio O(n*m)
            # but real ratio is accurate. With 2000 chars, real ratio is fine.
            return matcher.ratio()

        return await asyncio.to_thread(_lcs_ratio)

    def compute_temporal_recency(self, decay_rate: float = 0.01) -> float:
        """
        Exponential decay based on time.
        For admission (where the memory is brand new), this is always 1.0.
        (Included to match the research PDF's formulation).
        """
        age = 0.0  # It's brand new
        import math
        return math.exp(-decay_rate * age)

    def get_content_type_prior(self, content_type: str) -> float:
        """Static priority weights based on information type."""
        priors = {
            "preference": 0.9,     # User preferences are highly prized
            "system": 0.9,         # System constraints are critical
            "fact": 0.7,           # Objective truths
            "semantic": 0.7,       # General knowledge
            "plan": 0.5,           # Ephemeral action plans
            "transient": 0.1,      # Scratchpad / short-lived
            "reflection": 0.6,     # Self-reflections
            "inference": 0.5,      # Deduced facts
        }
        return priors.get(content_type, 0.5)
