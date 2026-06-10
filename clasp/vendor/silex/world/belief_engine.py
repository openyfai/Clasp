"""
Belief/proposition lifecycle engine for Kronos epistemic integrity.

Maintains a truth-maintenance layer over the world model:
- Tracks propositions with log-odds confidence and stance
- Connects claims to evidence in the evidence_ledger table
- Schedules belief revision when contradictions or new evidence arrives
- Feeds updated beliefs into the context builder and critic

Research basis: Engineering Epistemic Integrity (2026)
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from typing import Any, Optional

log = logging.getLogger("silex.world.belief_engine")


class BeliefEngine:
    """
    Manages the proposition_beliefs and evidence_ledger tables.

    Provides truth-maintenance operations:
      - admit_evidence(): record a new piece of evidence for/against a claim
      - update_belief(): revise log-odds for a proposition given evidence
      - get_belief(): retrieve current stance and confidence for a claim
      - schedule_maintenance(): mark top unresolved contradictions for resolution
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    # ------------------------------------------------------------------ #
    #  Evidence Ledger                                                     #
    # ------------------------------------------------------------------ #

    async def admit_evidence(
        self,
        claim: str,
        source_type: str,
        *,
        source_id: str | None = None,
        supports: bool = True,
        confidence: float = 0.7,
        session_id: str | None = None,
        goal_id: str | None = None,
    ) -> str:
        """Record a new piece of evidence and trigger belief update."""
        ev_id = uuid.uuid4().hex
        try:
            await self._db.execute(
                """INSERT OR IGNORE INTO evidence_ledger
                   (evidence_id, source_type, source_id, claim, supports_positive,
                    confidence, session_id, goal_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ev_id, source_type, source_id, claim,
                 int(supports), confidence, session_id, goal_id, time.time()),
            )
        except Exception as exc:
            log.debug("admit_evidence failed: %s", exc)
            return ev_id

        await self._revise_belief(claim)
        return ev_id

    # ------------------------------------------------------------------ #
    #  Belief Revision (Bayesian log-odds update)                         #
    # ------------------------------------------------------------------ #

    async def _revise_belief(self, claim: str) -> None:
        """
        Aggregate all evidence for a claim and update its log-odds.
        Uses a simple Bayesian log-odds accumulation:
          log_odds += sum of log(P/(1-P)) for supporting evidence
          log_odds -= sum of log(P/(1-P)) for contradicting evidence
        Prior: log_odds = 0 (uniform)
        """
        try:
            rows = await self._db.fetch_all(
                "SELECT supports_positive, confidence FROM evidence_ledger WHERE claim = ?",
                (claim,),
            )
        except Exception as exc:
            log.debug("_revise_belief query failed: %s", exc)
            return

        log_odds = 0.0
        for r in rows:
            conf = min(max(float(r["confidence"]), 0.001), 0.999)
            update = math.log(conf / (1 - conf))
            if r["supports_positive"]:
                log_odds += update
            else:
                log_odds -= update

        confidence = 1.0 / (1.0 + math.exp(-log_odds))
        if log_odds > 1.5:
            stance = "true"
        elif log_odds < -1.5:
            stance = "false"
        else:
            stance = "uncertain"

        now = time.time()
        try:
            existing = await self._db.fetch_one(
                "SELECT proposition_id FROM proposition_beliefs WHERE claim = ?", (claim,)
            )
            if existing:
                await self._db.execute(
                    """UPDATE proposition_beliefs
                       SET log_odds=?, confidence=?, stance=?, updated_at=?
                       WHERE claim=?""",
                    (log_odds, confidence, stance, now, claim),
                )
            else:
                await self._db.execute(
                    """INSERT INTO proposition_beliefs
                       (proposition_id, claim, stance, log_odds, confidence, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (uuid.uuid4().hex, claim, stance, log_odds, confidence, now, now),
                )
        except Exception as exc:
            log.debug("_revise_belief write failed: %s", exc)

    async def get_belief(self, claim: str) -> dict[str, Any]:
        """Return the current belief state for a claim."""
        try:
            row = await self._db.fetch_one(
                "SELECT stance, log_odds, confidence, last_verified_at FROM proposition_beliefs WHERE claim = ?",
                (claim,),
            )
            if row:
                return dict(row)
        except Exception as exc:
            log.debug("get_belief failed: %s", exc)
        return {"stance": "unknown", "log_odds": 0.0, "confidence": 0.5, "last_verified_at": None}

    async def update_belief(self, claim: str, stance: str, confidence: float, source: str) -> None:
        """Directly set belief from an authoritative source (critic, debate, verification)."""
        now = time.time()
        log_odds = math.log(confidence / (1 - confidence)) if 0 < confidence < 1 else 0.0
        try:
            existing = await self._db.fetch_one(
                "SELECT proposition_id FROM proposition_beliefs WHERE claim = ?", (claim,)
            )
            if existing:
                await self._db.execute(
                    """UPDATE proposition_beliefs
                       SET stance=?, log_odds=?, confidence=?, last_verified_at=?, verification_source=?, updated_at=?
                       WHERE claim=?""",
                    (stance, log_odds, confidence, now, source, now, claim),
                )
            else:
                await self._db.execute(
                    """INSERT INTO proposition_beliefs
                       (proposition_id, claim, stance, log_odds, confidence, last_verified_at, verification_source, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (uuid.uuid4().hex, claim, stance, log_odds, confidence, now, source, now, now),
                )
        except Exception as exc:
            log.debug("update_belief write failed: %s", exc)

    # ------------------------------------------------------------------ #
    #  Scheduled Maintenance                                               #
    # ------------------------------------------------------------------ #

    async def get_unresolved_contradictions(self, limit: int = 5) -> list[dict[str, Any]]:
        """Return the top N contradictions that need belief maintenance."""
        try:
            rows = await self._db.fetch_all(
                """SELECT c.id, c.claim_a, c.claim_b, c.description, c.created_at
                   FROM contradictions c
                   LEFT JOIN proposition_beliefs pb ON (pb.claim = c.claim_a OR pb.claim = c.claim_b)
                   WHERE c.status = 'unresolved' OR pb.proposition_id IS NULL
                   ORDER BY c.created_at DESC
                   LIMIT ?""",
                (limit,),
            )
            return [dict(r) for r in rows]
        except Exception as exc:
            log.debug("get_unresolved_contradictions failed: %s", exc)
            return []

    async def get_stale_beliefs(self, stale_seconds: float = 86400.0, limit: int = 10) -> list[dict[str, Any]]:
        """Return beliefs that have not been verified recently."""
        cutoff = time.time() - stale_seconds
        try:
            rows = await self._db.fetch_all(
                """SELECT proposition_id, claim, stance, confidence, last_verified_at
                   FROM proposition_beliefs
                   WHERE (last_verified_at IS NULL OR last_verified_at < ?)
                     AND stance IN ('uncertain', 'unknown')
                   ORDER BY updated_at ASC
                   LIMIT ?""",
                (cutoff, limit),
            )
            return [dict(r) for r in rows]
        except Exception as exc:
            log.debug("get_stale_beliefs failed: %s", exc)
            return []

    async def record_verification(
        self,
        claim: str,
        verified_stance: str,
        confidence: float,
        tool_results: list[str],
    ) -> None:
        """Record that a claim has been verified by the agent using tool evidence."""
        for tr in tool_results:
            await self.admit_evidence(
                claim=claim,
                source_type="tool_result",
                supports=(verified_stance == "true"),
                confidence=confidence,
            )
        await self.update_belief(claim, verified_stance, confidence, source="tool_verification")
