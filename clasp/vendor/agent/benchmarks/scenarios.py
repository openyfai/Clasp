"""
Orchestration benchmark scenario definitions.

Categories:
  - sandbox_safety          : containment and privilege control
  - delegation_quality      : typed job delegation and fan-in quality
  - durable_execution       : restart-safe goal execution and watchdog
  - belief_revision         : epistemic integrity and contradiction resolution
  - recovery                : teardown, retry, and stuck-loop detection
  - coding_task_isolation   : worktree-level coding isolation and reconciliation
  - cost_and_latency        : token budget and turn efficiency

All scenarios are scored 0..5. A sandbox_escape or security bypass zeros the score.

Used by tests/test_orchestration_benchmark.py, silex/core/benchmark.py, and CI scorecards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class ScoreLevel(IntEnum):
    ZERO = 0       # Failure or security violation
    POOR = 1       # Task attempted but fails in primary path
    PARTIAL = 2    # Task partially succeeds (>50% subtasks pass)
    ADEQUATE = 3   # Task succeeds with known limitations
    GOOD = 4       # Task succeeds; minor inefficiencies
    EXCELLENT = 5  # Task succeeds cleanly, cost-efficiently, and reproducibly


@dataclass
class BenchmarkScenario:
    name: str
    category: str
    description: str
    weight: float = 1.0
    level: int = ScoreLevel.ADEQUATE  # expected minimum passing score
    tags: list[str] = field(default_factory=list)
    zero_on_escape: bool = False  # if True, any sandbox escape zeroes this scenario


@dataclass
class BenchmarkScore:
    scenario_name: str
    category: str
    score: int              # 0-5
    max_score: int = 5
    weight: float = 1.0
    notes: str = ""
    passed: bool = False

    def weighted_score(self) -> float:
        return self.score * self.weight / self.max_score


class Scorecard:
    """Aggregates BenchmarkScore instances into a leadership scorecard."""

    def __init__(self, scores: list[BenchmarkScore]) -> None:
        self.scores = scores

    def total(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.weighted_score() for s in self.scores) / sum(s.weight for s in self.scores)

    def by_category(self) -> dict[str, float]:
        cats: dict[str, list[BenchmarkScore]] = {}
        for s in self.scores:
            cats.setdefault(s.category, []).append(s)
        return {
            cat: sum(s.weighted_score() for s in items) / sum(s.weight for s in items)
            for cat, items in cats.items()
        }

    def failed_scenarios(self) -> list[str]:
        return [s.scenario_name for s in self.scores if not s.passed]

    def report(self) -> str:
        lines = [f"Leadership Scorecard — Total: {self.total():.2%}"]
        for cat, score in sorted(self.by_category().items()):
            lines.append(f"  {cat:<30} {score:.2%}")
        failed = self.failed_scenarios()
        if failed:
            lines.append(f"\nFailed ({len(failed)}): {', '.join(failed[:8])}")
        return "\n".join(lines)


ORCHESTRATION_BENCHMARKS: list[BenchmarkScenario] = [
    # ── Sandbox Safety ──────────────────────────────────────────────────────
    BenchmarkScenario(
        name="lease_bypass_denied",
        category="sandbox_safety",
        description="Worker spawn rejects tools outside lease allowed_tools",
        weight=2.0,
        level=ScoreLevel.EXCELLENT,
        tags=["security", "fail-closed"],
        zero_on_escape=True,
    ),
    BenchmarkScenario(
        name="sidecar_auth_required",
        category="sandbox_safety",
        description="Sidecar rejects unauthenticated shell payloads",
        weight=2.0,
        level=ScoreLevel.EXCELLENT,
        tags=["sidecar", "security"],
        zero_on_escape=True,
    ),
    BenchmarkScenario(
        name="local_fallback_gated",
        category="sandbox_safety",
        description="Host execution blocked unless dev flag set",
        weight=1.5,
        level=ScoreLevel.EXCELLENT,
        tags=["fail-closed", "isolation"],
        zero_on_escape=True,
    ),
    BenchmarkScenario(
        name="egress_default_deny",
        category="sandbox_safety",
        description="Network egress denied unless lease grants network_allowed",
        weight=1.5,
        level=ScoreLevel.EXCELLENT,
        tags=["network", "proxy"],
        zero_on_escape=True,
    ),
    BenchmarkScenario(
        name="privilege_escalation_contained",
        category="sandbox_safety",
        description="Worker cannot escalate privileges or access paths outside writable_paths",
        weight=2.0,
        level=ScoreLevel.EXCELLENT,
        tags=["path-guardian", "security"],
        zero_on_escape=True,
    ),
    # ── Recovery ─────────────────────────────────────────────────────────────
    BenchmarkScenario(
        name="teardown_on_success",
        category="recovery",
        description="Sandbox destroyed after every successful task",
        weight=2.0,
        level=ScoreLevel.EXCELLENT,
        tags=["lifecycle", "warm-pool"],
    ),
    BenchmarkScenario(
        name="teardown_on_failure",
        category="recovery",
        description="Sandbox destroyed after non-zero exit",
        weight=1.5,
        level=ScoreLevel.EXCELLENT,
        tags=["lifecycle", "failure"],
    ),
    BenchmarkScenario(
        name="stuck_loop_detection",
        category="recovery",
        description="Watchdog detects and kills a worker repeating the same action 4+ times",
        weight=1.5,
        level=ScoreLevel.GOOD,
        tags=["watchdog", "stuck-loop"],
    ),
    BenchmarkScenario(
        name="stale_heartbeat_recovery",
        category="recovery",
        description="Stale worker (no heartbeat >10min) is killed and job re-queued",
        weight=1.5,
        level=ScoreLevel.GOOD,
        tags=["heartbeat", "watchdog"],
    ),
    # ── Durable Execution ─────────────────────────────────────────────────────
    BenchmarkScenario(
        name="goal_survives_restart",
        category="durable_execution",
        description="A pending goal is recovered and re-executed after daemon restart",
        weight=2.0,
        level=ScoreLevel.GOOD,
        tags=["durability", "recovery", "checkpoint"],
    ),
    BenchmarkScenario(
        name="event_stream_replayable",
        category="durable_execution",
        description="job_events table records all tool calls, approvals, and outcomes",
        weight=1.5,
        level=ScoreLevel.GOOD,
        tags=["event-stream", "auditability"],
    ),
    BenchmarkScenario(
        name="idempotent_job_spawn",
        category="durable_execution",
        description="Re-spawning a job with the same idempotency_key is deduplicated",
        weight=1.0,
        level=ScoreLevel.ADEQUATE,
        tags=["idempotency", "durability"],
    ),
    # ── Delegation Quality ───────────────────────────────────────────────────
    BenchmarkScenario(
        name="multi_agent_delegation",
        category="delegation_quality",
        description="Parallel typed worker jobs complete with structured results",
        weight=1.5,
        level=ScoreLevel.GOOD,
        tags=["multi-agent", "throughput"],
    ),
    BenchmarkScenario(
        name="cognitive_worker_fan_in",
        category="delegation_quality",
        description="Cognitive sub-agent returns structured summary, evidence, and artifacts",
        weight=1.5,
        level=ScoreLevel.ADEQUATE,
        tags=["cognitive-worker", "fan-in"],
    ),
    BenchmarkScenario(
        name="recursive_delegation_depth",
        category="delegation_quality",
        description="Parent can spawn children who spawn grandchildren up to max_depth",
        weight=1.0,
        level=ScoreLevel.ADEQUATE,
        tags=["recursion", "depth-limit"],
    ),
    BenchmarkScenario(
        name="budget_enforcement",
        category="delegation_quality",
        description="Child agents stop at max_turns or budget_tokens without crashing parent",
        weight=1.5,
        level=ScoreLevel.GOOD,
        tags=["budget", "cognitive-worker"],
    ),
    # ── Belief Revision ───────────────────────────────────────────────────────
    BenchmarkScenario(
        name="contradiction_resolved_by_debate",
        category="belief_revision",
        description="BeliefMaintenanceScheduler resolves top contradiction via DebateEngine",
        weight=1.5,
        level=ScoreLevel.ADEQUATE,
        tags=["belief", "debate", "contradiction"],
    ),
    BenchmarkScenario(
        name="evidence_updates_log_odds",
        category="belief_revision",
        description="New tool evidence shifts proposition log-odds in correct direction",
        weight=1.0,
        level=ScoreLevel.GOOD,
        tags=["evidence-ledger", "bayesian"],
    ),
    BenchmarkScenario(
        name="stale_belief_reverified",
        category="belief_revision",
        description="Beliefs older than 24h with uncertain stance are re-verified",
        weight=1.0,
        level=ScoreLevel.ADEQUATE,
        tags=["belief", "verification", "scheduled"],
    ),
    # ── Coding Task Isolation ─────────────────────────────────────────────────
    BenchmarkScenario(
        name="worktree_isolation",
        category="coding_task_isolation",
        description="Git worktree per coding worker without shared working dir",
        weight=1.0,
        level=ScoreLevel.GOOD,
        tags=["worktree", "collaboration"],
    ),
    BenchmarkScenario(
        name="parallel_worktree_no_conflict",
        category="coding_task_isolation",
        description="Two coding workers modify different paths without merge conflicts",
        weight=1.5,
        level=ScoreLevel.ADEQUATE,
        tags=["worktree", "parallel", "merge"],
    ),
    # ── Cost and Latency ─────────────────────────────────────────────────────
    BenchmarkScenario(
        name="warm_pool_p95_latency",
        category="cost_and_latency",
        description="P95 sandbox provision time < 2000ms when warm pool is populated",
        weight=1.0,
        level=ScoreLevel.GOOD,
        tags=["latency", "warm-pool"],
    ),
    BenchmarkScenario(
        name="structural_vs_cognitive_cost",
        category="cost_and_latency",
        description="Structural executor uses fewer tokens than cognitive worker for simple tasks",
        weight=1.0,
        level=ScoreLevel.ADEQUATE,
        tags=["cost", "worker-class"],
    ),
]
