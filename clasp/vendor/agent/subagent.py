"""
Bounded cognitive sub-agent runner.

Spawns a private CognitiveLoop with scoped context, tools, turn budget,
and token budget. Returns only structured summaries, diffs, artifacts,
and verification logs to the parent — never the raw private history.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("agent.subagent")

DEFAULT_MAX_TURNS = 10
DEFAULT_BUDGET_TOKENS = 50_000
DEFAULT_MAX_DEPTH = 3


@dataclass
class ChildAgentResult:
    """Structured fan-in result from a bounded cognitive sub-agent."""

    job_id: str
    success: bool
    summary: str
    artifacts: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    diff_summary: str = ""
    turns_used: int = 0
    tokens_used: int = 0
    error: str = ""
    elapsed_seconds: float = 0.0


class BoundedCognitiveWorker:
    """
    Runs a private CognitiveLoop for a scoped task and returns a structured
    summary. Child context is NOT exposed to the parent — only the result.
    """

    def __init__(
        self,
        objective: str,
        *,
        job_id: str | None = None,
        scoped_tools: list[str] | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
        ancestry: list[str] | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        worktree_path: str | None = None,
        db_path: str | None = None,
    ) -> None:
        self.objective = objective
        self.job_id = job_id or f"cagent_{uuid.uuid4().hex[:10]}"
        self.scoped_tools = scoped_tools or []
        self.max_turns = max_turns
        self.budget_tokens = budget_tokens
        self.ancestry = ancestry or []
        self.max_depth = max_depth
        self.worktree_path = worktree_path
        self.db_path = db_path

    def _depth_exceeded(self) -> bool:
        return len(self.ancestry) >= self.max_depth

    async def run(self) -> ChildAgentResult:
        start = time.time()

        if self._depth_exceeded():
            return ChildAgentResult(
                job_id=self.job_id,
                success=False,
                summary="",
                error=f"Max recursion depth {self.max_depth} exceeded. Ancestry: {self.ancestry}",
            )

        try:
            from clasp.vendor.silex.core.cognitive_loop import CognitiveLoop
            from clasp.vendor.silex.utils.config import SILEX_DB
        except ImportError as exc:
            return ChildAgentResult(
                job_id=self.job_id,
                success=False,
                summary="",
                error=f"Import error: {exc}",
            )

        db_path = self.db_path or str(SILEX_DB)

        child_loop = CognitiveLoop(db_path=db_path)
        turns_used = 0
        tokens_used = 0

        try:
            await child_loop.startup(target_query=self.objective)

            # Restrict the child loop to its scoped tools
            if self.scoped_tools:
                available = set(child_loop.tool_registry.tools.keys())
                to_remove = [t for t in available if t not in set(self.scoped_tools)]
                for t in to_remove:
                    child_loop.tool_registry.tools.pop(t, None)
                log.debug(
                    "ChildAgent %s: tools scoped to %s", self.job_id, self.scoped_tools
                )

            full_prompt = (
                f"[CHILD AGENT TASK — job={self.job_id}]\n"
                f"Parent ancestry: {' -> '.join(self.ancestry) if self.ancestry else 'root'}\n"
                f"Turn budget: {self.max_turns} | Token budget: {self.budget_tokens}\n\n"
                f"Objective: {self.objective}\n\n"
                f"When done, output a structured JSON block with keys: "
                f"summary, artifacts (list of paths), evidence (list of strings), diff_summary."
            )

            response = await asyncio.wait_for(
                child_loop.process(full_prompt),
                timeout=float(self.max_turns * 60),
            )
            turns_used = getattr(child_loop, "_turn_count", 1)
            raw_response = getattr(response, "response", str(response))

            # Try to parse structured JSON from the response
            import json
            summary = raw_response
            artifacts: list[str] = []
            evidence: list[str] = []
            diff_summary = ""

            try:
                import re
                json_match = re.search(r"\{[^{}]*\"summary\"[^{}]*\}", raw_response, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group(0))
                    summary = parsed.get("summary", raw_response[:500])
                    artifacts = parsed.get("artifacts", [])
                    evidence = parsed.get("evidence", [])
                    diff_summary = parsed.get("diff_summary", "")
            except Exception:
                pass

            return ChildAgentResult(
                job_id=self.job_id,
                success=True,
                summary=summary[:2000],
                artifacts=artifacts,
                evidence=evidence,
                diff_summary=diff_summary,
                turns_used=turns_used,
                tokens_used=tokens_used,
                elapsed_seconds=time.time() - start,
            )

        except asyncio.TimeoutError:
            return ChildAgentResult(
                job_id=self.job_id,
                success=False,
                summary="",
                error=f"Child agent timed out after {self.max_turns * 60}s",
                turns_used=turns_used,
                elapsed_seconds=time.time() - start,
            )
        except Exception as exc:
            log.error("ChildAgent %s crashed: %s", self.job_id, exc)
            return ChildAgentResult(
                job_id=self.job_id,
                success=False,
                summary="",
                error=str(exc),
                elapsed_seconds=time.time() - start,
            )
        finally:
            try:
                await child_loop.shutdown()
            except Exception:
                pass


async def run_cognitive_subagent(
    objective: str,
    *,
    job_id: str | None = None,
    scoped_tools: list[str] | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    ancestry: list[str] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    worktree_path: str | None = None,
    db_path: str | None = None,
) -> ChildAgentResult:
    """Convenience wrapper to spin up and run a bounded cognitive sub-agent."""
    worker = BoundedCognitiveWorker(
        objective=objective,
        job_id=job_id,
        scoped_tools=scoped_tools,
        max_turns=max_turns,
        budget_tokens=budget_tokens,
        ancestry=ancestry,
        max_depth=max_depth,
        worktree_path=worktree_path,
        db_path=db_path,
    )
    return await worker.run()
