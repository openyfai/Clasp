"""
SQLite database layer for ARIA.

Handles connection management, schema creation, and migrations.
All operations are async via aiosqlite.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
import asyncio
import os
import time

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

import aiosqlite

from clasp.vendor.silex.utils.config import SILEX_DB
from clasp.vendor.silex.utils.logger import setup_logger

log = setup_logger("silex.storage")

transaction_depth_var: ContextVar[int] = ContextVar("transaction_depth_var", default=0)
active_transaction_conn_var: ContextVar[aiosqlite.Connection | None] = ContextVar("active_transaction_conn_var", default=None)

class FileLock:
    """A cross-platform file lock using standard library modules (fcntl on Unix, msvcrt on Windows)."""
    def __init__(self, lock_file_path: str):
        self.lock_file_path = lock_file_path
        self._fd = None

    def acquire(self, timeout: float = 15.0, poll_interval: float = 0.05) -> bool:
        os.makedirs(os.path.dirname(os.path.abspath(self.lock_file_path)), exist_ok=True)
        start_time = time.time()
        while True:
            try:
                self._fd = os.open(self.lock_file_path, os.O_CREAT | os.O_WRONLY)
                if fcntl:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                elif msvcrt:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                return True
            except (IOError, OSError):
                if self._fd is not None:
                    try:
                        os.close(self._fd)
                    except OSError:
                        pass
                    self._fd = None
                
                if time.time() - start_time >= timeout:
                    return False
                time.sleep(poll_interval)

    def release(self):
        if self._fd is not None:
            try:
                if fcntl:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                elif msvcrt:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            except (IOError, OSError):
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

# ---------------------------------------------------------------------------
# Schema — this IS the database definition
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- Memories table
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    memory_type TEXT NOT NULL DEFAULT 'semantic',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '[]',
    level INTEGER NOT NULL DEFAULT 1,
    child_memory_ids TEXT NOT NULL DEFAULT '[]',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    related_memories TEXT NOT NULL DEFAULT '[]',
    archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_accessed ON memories(last_accessed DESC);

-- Goals table
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    priority TEXT NOT NULL DEFAULT 'medium',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sub_goals TEXT NOT NULL DEFAULT '[]',
    completion_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0,
    memories_created INTEGER NOT NULL DEFAULT 0,
    goals_modified INTEGER NOT NULL DEFAULT 0,
    avg_confidence REAL NOT NULL DEFAULT 0.0,
    topics TEXT NOT NULL DEFAULT '[]'
);

-- Turns table (conversation history)
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    user_input TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    response TEXT NOT NULL,
    self_reflection TEXT NOT NULL,
    confidence REAL NOT NULL,
    scratchpad TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, turn_number);

-- =====================================================================
-- Phase 2 — World Model Tables
-- =====================================================================

-- Knowledge nodes (the graph's vertices)
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    node_type TEXT NOT NULL DEFAULT 'fact',
    confidence REAL NOT NULL DEFAULT 0.5,
    source TEXT NOT NULL DEFAULT 'inference',
    created_at TEXT NOT NULL,
    last_validated TEXT NOT NULL,
    validation_count INTEGER NOT NULL DEFAULT 0,
    contradiction_count INTEGER NOT NULL DEFAULT 0,
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON knowledge_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_confidence ON knowledge_nodes(confidence DESC);

-- Causal edges (the graph's typed relationships)
CREATE TABLE IF NOT EXISTS causal_edges (
    id TEXT PRIMARY KEY,
    source_node TEXT NOT NULL,
    target_node TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    strength REAL NOT NULL DEFAULT 0.5,
    evidence TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_node) REFERENCES knowledge_nodes(id),
    FOREIGN KEY (target_node) REFERENCES knowledge_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON causal_edges(source_node);
CREATE INDEX IF NOT EXISTS idx_edges_target ON causal_edges(target_node);
CREATE INDEX IF NOT EXISTS idx_edges_type ON causal_edges(edge_type);

-- Hypotheses (predictions from the world model)
CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    claim TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_hypotheses_status ON hypotheses(status);

-- Contradictions (conflicts between knowledge nodes)
CREATE TABLE IF NOT EXISTS contradictions (
    id TEXT PRIMARY KEY,
    node_a TEXT NOT NULL,
    node_b TEXT NOT NULL,
    analysis TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unresolved',
    resolution TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (node_a) REFERENCES knowledge_nodes(id),
    FOREIGN KEY (node_b) REFERENCES knowledge_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_contradictions_status ON contradictions(status);

-- =====================================================================
-- Phase 7 — Semantic Disambiguation
-- =====================================================================

-- Semantic profiles (learned subjective-to-objective mappings)
CREATE TABLE IF NOT EXISTS semantic_profiles (
    term TEXT PRIMARY KEY,
    objective_proxies TEXT NOT NULL, -- JSON list
    context_tags TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.5,
    updated_at TEXT NOT NULL
);

-- =====================================================================
-- Phase 3 — Self-Improvement Tables
-- =====================================================================

CREATE TABLE IF NOT EXISTS improvement_logs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    original_response TEXT NOT NULL,
    feedback TEXT NOT NULL,
    accuracy_score REAL NOT NULL,
    depth_score REAL NOT NULL,
    honesty_score REAL NOT NULL,
    improved_response TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- =====================================================================
-- Phase 4 — Multi-Agent Debate Tables
-- =====================================================================

CREATE TABLE IF NOT EXISTS debates (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    transcript_json TEXT NOT NULL,
    resolution_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS uncertainties (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    why_uncertain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);

-- =====================================================================
-- Phase 5 — Tool Use & Action Logs
-- =====================================================================

CREATE TABLE IF NOT EXISTS action_logs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    expected_outcome TEXT NOT NULL,
    actual_outcome TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    risk_level TEXT NOT NULL DEFAULT 'read_only',
    model_update TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_approvals (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    tool_name TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    expected_outcome TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    execution_result_json TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_approvals_status ON tool_approvals(status, created_at);

CREATE TABLE IF NOT EXISTS ethical_decisions (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    turn_number INTEGER NOT NULL DEFAULT 0,
    tool_name TEXT NOT NULL,
    principle TEXT NOT NULL,
    action TEXT NOT NULL,
    rationale TEXT NOT NULL,
    risk_level TEXT NOT NULL DEFAULT 'read_only',
    requires_consent BOOLEAN NOT NULL DEFAULT 0,
    uncertainty REAL NOT NULL DEFAULT 0.0,
    context TEXT NOT NULL DEFAULT 'interactive',
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_ethical_decisions_session ON ethical_decisions(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ethical_decisions_action ON ethical_decisions(action, created_at);

CREATE TABLE IF NOT EXISTS recent_failures (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    failure_type TEXT NOT NULL, -- 'critic_rejection', 'tool_error', 'consistency_mismatch'
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_recent_failures_session ON recent_failures(session_id, created_at);

-- =====================================================================
-- Phase 6 — Transfer + Generalization
-- =====================================================================

CREATE TABLE IF NOT EXISTS principles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    statement TEXT NOT NULL,
    original_domain TEXT NOT NULL,
    applicable_domains_json TEXT NOT NULL,
    source_observations_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- =====================================================================
-- Phase 7 — Recursive Self-Improvement
-- =====================================================================

CREATE TABLE IF NOT EXISTS improvement_proposals (
    id TEXT PRIMARY KEY,
    target_system TEXT NOT NULL,
    description TEXT NOT NULL,
    rationale TEXT NOT NULL,
    success_metric TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS benchmark_history (
    id TEXT PRIMARY KEY,
    total_score REAL NOT NULL,
    accuracy_avg REAL NOT NULL,
    depth_avg REAL NOT NULL,
    honesty_avg REAL NOT NULL,
    domains_tested_json TEXT NOT NULL,
    question_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost_usd REAL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    success BOOLEAN NOT NULL DEFAULT 1,
    error TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_provider_model ON llm_usage(provider, model, created_at DESC);

-- =====================================================================
-- Durable Planning
-- =====================================================================

CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    title TEXT NOT NULL,
    user_input TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    success_criteria TEXT NOT NULL DEFAULT '',
    tool_budget INTEGER NOT NULL DEFAULT 8,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_plans_session ON plans(session_id, status);

CREATE TABLE IF NOT EXISTS plan_steps (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    required_tools_json TEXT NOT NULL DEFAULT '[]',
    result TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES plans(id)
);

CREATE INDEX IF NOT EXISTS idx_plan_steps_plan ON plan_steps(plan_id, step_number);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    delivered INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turn_checkpoints (
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    draft_reasoning TEXT NOT NULL,
    draft_plan TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'executing_tools',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, turn_number)
);

CREATE TABLE IF NOT EXISTS response_cache (
    query_hash TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Saga Telemetry Logs
CREATE TABLE IF NOT EXISTS saga_telemetry_logs (
    saga_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_saga_telemetry_saga_id ON saga_telemetry_logs(saga_id);

-- =====================================================================
-- Phase 3 — Epistemic Memory Orchestration Tables
-- =====================================================================

-- User profiles: top-level scope anchor for memory isolation
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    global_preferences TEXT NOT NULL DEFAULT '{}'
);

-- Epistemic nodes: typed decision/hypothesis/fact/dead_end vertices
-- Separate from knowledge_nodes (which tracks world-model semantic facts).
-- This table tracks the AGENT'S OWN reasoning trajectory.
CREATE TABLE IF NOT EXISTS epistemic_nodes (
    node_id TEXT PRIMARY KEY,
    run_id TEXT,
    session_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('decision', 'hypothesis', 'fact', 'dead_end')),
    content TEXT NOT NULL,
    provenance TEXT NOT NULL,
    integrity_hash TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_epistemic_nodes_type_session ON epistemic_nodes(type, session_id);
CREATE INDEX IF NOT EXISTS idx_epistemic_nodes_timestamp ON epistemic_nodes(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_epistemic_nodes_status ON epistemic_nodes(status, timestamp DESC);

-- Epistemic causal edges: directed semantic links between epistemic nodes
-- NOTE: uses different column names than causal_edges (world-model) to avoid confusion
CREATE TABLE IF NOT EXISTS epistemic_edges (
    edge_id TEXT PRIMARY KEY,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    relation_type TEXT NOT NULL CHECK(relation_type IN (
        'triggered_by', 'contradicts', 'prevented', 'caused_failure_in'
    )),
    weight REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_node_id) REFERENCES epistemic_nodes(node_id) ON DELETE CASCADE,
    FOREIGN KEY (target_node_id) REFERENCES epistemic_nodes(node_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_epistemic_edges_source ON epistemic_edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_epistemic_edges_target ON epistemic_edges(target_node_id);
CREATE INDEX IF NOT EXISTS idx_epistemic_edges_relation ON epistemic_edges(relation_type);

-- Admitted memories: A-MAC gated memory store with composite quality scores
CREATE TABLE IF NOT EXISTS admitted_memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT,
    content TEXT NOT NULL,
    content_type TEXT NOT NULL CHECK(content_type IN ('preference', 'fact', 'plan', 'transient')),
    utility_score REAL NOT NULL,
    confidence_score REAL NOT NULL,
    novelty_score REAL NOT NULL,
    recency_score REAL NOT NULL,
    type_prior REAL NOT NULL,
    composite_score REAL NOT NULL,
    admitted_at REAL NOT NULL,
    integrity_hash TEXT NOT NULL,
    origin_trajectory_id TEXT,
    skill_name TEXT,
    category TEXT
);

CREATE INDEX IF NOT EXISTS idx_admitted_memories_scores ON admitted_memories(user_id, composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_admitted_memories_admitted_at ON admitted_memories(admitted_at DESC);

-- Trust state: persisted Bayesian Beta-Binomial trust model per actor
CREATE TABLE IF NOT EXISTS trust_state (
    actor_id TEXT PRIMARY KEY,
    alpha REAL NOT NULL DEFAULT 10.0,
    beta REAL NOT NULL DEFAULT 1.0,
    last_updated REAL NOT NULL,
    anomaly_count INTEGER NOT NULL DEFAULT 0
);

-- =====================================================================
-- Phase 7 — Self-Evolution Tables
-- =====================================================================
CREATE TABLE IF NOT EXISTS trajectories (
    trajectory_id TEXT PRIMARY KEY,
    task_description TEXT NOT NULL,
    is_success INTEGER NOT NULL CHECK (is_success IN (0, 1)),
    cumulative_latency REAL NOT NULL,
    total_tokens INTEGER NOT NULL,
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS trajectory_steps (
    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trajectory_id TEXT NOT NULL,
    step_order INTEGER NOT NULL,
    action_name TEXT NOT NULL,
    tool_input TEXT NOT NULL,
    execution_output TEXT NOT NULL,
    epistemic_category TEXT NOT NULL CHECK (epistemic_category IN ('decision', 'hypothesis', 'fact', 'dead_end')),
    latency_ms REAL NOT NULL,
    token_usage INTEGER NOT NULL,
    FOREIGN KEY (trajectory_id) REFERENCES trajectories(trajectory_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trajectory_steps_order ON trajectory_steps(trajectory_id, step_order);
"""

MIGRATIONS_SQL = [
    "ALTER TABLE memories ADD COLUMN memory_type TEXT NOT NULL DEFAULT 'semantic'",
    "ALTER TABLE memories ADD COLUMN confidence REAL NOT NULL DEFAULT 0.5",
    "ALTER TABLE memories ADD COLUMN level INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE memories ADD COLUMN child_memory_ids TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE memories ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE memories ADD COLUMN archived_at TEXT",
    "ALTER TABLE action_logs ADD COLUMN risk_level TEXT NOT NULL DEFAULT 'read_only'",
    "ALTER TABLE turns ADD COLUMN scratchpad TEXT",
    "ALTER TABLE knowledge_nodes ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'unverified'",
    "CREATE TABLE IF NOT EXISTS ethical_decisions (id TEXT PRIMARY KEY, session_id TEXT, turn_number INTEGER NOT NULL DEFAULT 0, tool_name TEXT NOT NULL, principle TEXT NOT NULL, action TEXT NOT NULL, rationale TEXT NOT NULL, risk_level TEXT NOT NULL DEFAULT 'read_only', requires_consent BOOLEAN NOT NULL DEFAULT 0, uncertainty REAL NOT NULL DEFAULT 0.0, context TEXT NOT NULL DEFAULT 'interactive', created_at TEXT NOT NULL, FOREIGN KEY (session_id) REFERENCES sessions(id))",
    # Indexes on columns added by migrations must run after ALTERs (older DBs skip CREATE TABLE).
    "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type)",
    "CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived_at)",
    "CREATE INDEX IF NOT EXISTS idx_ethical_decisions_session ON ethical_decisions(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_ethical_decisions_action ON ethical_decisions(action, created_at)",
    "ALTER TABLE tool_approvals ADD COLUMN expected_outcome TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE tool_approvals ADD COLUMN execution_result_json TEXT",
    "CREATE TABLE IF NOT EXISTS llm_usage (id TEXT PRIMARY KEY, session_id TEXT, provider TEXT NOT NULL, model TEXT NOT NULL, request_kind TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER, estimated_cost_usd REAL, duration_ms INTEGER NOT NULL DEFAULT 0, success BOOLEAN NOT NULL DEFAULT 1, error TEXT, created_at TEXT NOT NULL, FOREIGN KEY (session_id) REFERENCES sessions(id))",
    "CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_llm_usage_provider_model ON llm_usage(provider, model, created_at DESC)",
    "CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, message TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'info', delivered INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS turn_checkpoints (session_id TEXT NOT NULL, turn_number INTEGER NOT NULL, draft_reasoning TEXT NOT NULL, draft_plan TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'executing_tools', updated_at TEXT NOT NULL, PRIMARY KEY (session_id, turn_number))",
    "CREATE TABLE IF NOT EXISTS response_cache (query_hash TEXT PRIMARY KEY, response TEXT NOT NULL, created_at TEXT NOT NULL)",
    "ALTER TABLE turns ADD COLUMN priority_tags TEXT NOT NULL DEFAULT '[]'",
    "CREATE TABLE IF NOT EXISTS saga_telemetry_logs (saga_id TEXT NOT NULL, status TEXT NOT NULL, current_step TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_saga_telemetry_saga_id ON saga_telemetry_logs(saga_id)",
    # ----------------------------------------------------------------
    # Phase 1 — Epistemic Memory Orchestration migrations
    # These use CREATE TABLE IF NOT EXISTS so they are safe to re-run
    # on fresh databases that already have the tables from SCHEMA_SQL.
    # ----------------------------------------------------------------
    "CREATE TABLE IF NOT EXISTS user_profiles (user_id TEXT PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, global_preferences TEXT NOT NULL DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS epistemic_nodes (node_id TEXT PRIMARY KEY, run_id TEXT, session_id TEXT NOT NULL, timestamp REAL NOT NULL, type TEXT NOT NULL CHECK(type IN ('decision', 'hypothesis', 'fact', 'dead_end')), content TEXT NOT NULL, provenance TEXT NOT NULL, integrity_hash TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')), FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE)",
    "CREATE INDEX IF NOT EXISTS idx_epistemic_nodes_type_session ON epistemic_nodes(type, session_id)",
    "CREATE INDEX IF NOT EXISTS idx_epistemic_nodes_timestamp ON epistemic_nodes(timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_epistemic_nodes_status ON epistemic_nodes(status, timestamp DESC)",
    "CREATE TABLE IF NOT EXISTS epistemic_edges (edge_id TEXT PRIMARY KEY, source_node_id TEXT NOT NULL, target_node_id TEXT NOT NULL, relation_type TEXT NOT NULL CHECK(relation_type IN ('triggered_by', 'contradicts', 'prevented', 'caused_failure_in')), weight REAL NOT NULL DEFAULT 1.0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (source_node_id) REFERENCES epistemic_nodes(node_id) ON DELETE CASCADE, FOREIGN KEY (target_node_id) REFERENCES epistemic_nodes(node_id) ON DELETE CASCADE)",
    "CREATE INDEX IF NOT EXISTS idx_epistemic_edges_source ON epistemic_edges(source_node_id)",
    "CREATE INDEX IF NOT EXISTS idx_epistemic_edges_target ON epistemic_edges(target_node_id)",
    "CREATE INDEX IF NOT EXISTS idx_epistemic_edges_relation ON epistemic_edges(relation_type)",
    "CREATE TABLE IF NOT EXISTS admitted_memories (memory_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT, content TEXT NOT NULL, content_type TEXT NOT NULL CHECK(content_type IN ('preference', 'fact', 'plan', 'transient')), utility_score REAL NOT NULL, confidence_score REAL NOT NULL, novelty_score REAL NOT NULL, recency_score REAL NOT NULL, type_prior REAL NOT NULL, composite_score REAL NOT NULL, admitted_at REAL NOT NULL, integrity_hash TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_admitted_memories_scores ON admitted_memories(user_id, composite_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_admitted_memories_admitted_at ON admitted_memories(admitted_at DESC)",
    "CREATE TABLE IF NOT EXISTS trust_state (actor_id TEXT PRIMARY KEY, alpha REAL NOT NULL DEFAULT 10.0, beta REAL NOT NULL DEFAULT 1.0, last_updated REAL NOT NULL, anomaly_count INTEGER NOT NULL DEFAULT 0)",
    # ----------------------------------------------------------------
    # Phase 7 — Self-Evolution migrations
    # ----------------------------------------------------------------
    "CREATE TABLE IF NOT EXISTS trajectories (trajectory_id TEXT PRIMARY KEY, task_description TEXT NOT NULL, is_success INTEGER NOT NULL CHECK (is_success IN (0, 1)), cumulative_latency REAL NOT NULL, total_tokens INTEGER NOT NULL, timestamp REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS trajectory_steps (step_id INTEGER PRIMARY KEY AUTOINCREMENT, trajectory_id TEXT NOT NULL, step_order INTEGER NOT NULL, action_name TEXT NOT NULL, tool_input TEXT NOT NULL, execution_output TEXT NOT NULL, epistemic_category TEXT NOT NULL CHECK (epistemic_category IN ('decision', 'hypothesis', 'fact', 'dead_end')), latency_ms REAL NOT NULL, token_usage INTEGER NOT NULL, FOREIGN KEY (trajectory_id) REFERENCES trajectories(trajectory_id) ON DELETE CASCADE)",
    "CREATE INDEX IF NOT EXISTS idx_trajectory_steps_order ON trajectory_steps(trajectory_id, step_order)",
    "ALTER TABLE admitted_memories ADD COLUMN origin_trajectory_id TEXT",
    "ALTER TABLE admitted_memories ADD COLUMN skill_name TEXT",
    "ALTER TABLE admitted_memories ADD COLUMN category TEXT",
    # ----------------------------------------------------------------
    # Durable Autonomy Kernel — durable goal execution tables
    # ----------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS autonomous_jobs (
        goal_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','claimed','running','step_saved','paused','completed','failed','cancelled')),
        idempotency_key TEXT NOT NULL DEFAULT '',
        retry_count INTEGER NOT NULL DEFAULT 0,
        max_retries INTEGER NOT NULL DEFAULT 3,
        timeout_seconds REAL NOT NULL DEFAULT 3600.0,
        created_at REAL NOT NULL,
        started_at REAL,
        completed_at REAL,
        last_heartbeat REAL,
        output_summary TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (goal_id, run_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_autonomous_jobs_status ON autonomous_jobs(status, created_at)",
    """CREATE TABLE IF NOT EXISTS job_events (
        event_id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        payload_hash TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_job_events_goal_run ON job_events(goal_id, run_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_job_events_kind ON job_events(kind, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS job_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        superstep INTEGER NOT NULL DEFAULT 0,
        state_json TEXT NOT NULL DEFAULT '{}',
        summary TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_job_checkpoints_goal ON job_checkpoints(goal_id, run_id, superstep DESC)",
    """CREATE TABLE IF NOT EXISTS agent_heartbeats (
        process_id TEXT PRIMARY KEY,
        goal_id TEXT,
        run_id TEXT,
        last_seen REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""",
    # ----------------------------------------------------------------
    # Epistemic Integrity — evidence ledger and proposition beliefs
    # ----------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS evidence_ledger (
        evidence_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL
            CHECK(source_type IN ('memory','tool_result','web_search','user_statement','agent_observation','world_graph')),
        source_id TEXT,
        claim TEXT NOT NULL,
        supports_positive INTEGER NOT NULL DEFAULT 1,
        confidence REAL NOT NULL DEFAULT 0.5,
        session_id TEXT,
        goal_id TEXT,
        created_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_evidence_ledger_claim ON evidence_ledger(claim)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_ledger_source ON evidence_ledger(source_type, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS proposition_beliefs (
        proposition_id TEXT PRIMARY KEY,
        claim TEXT NOT NULL UNIQUE,
        stance TEXT NOT NULL DEFAULT 'unknown'
            CHECK(stance IN ('true','false','uncertain','unknown','retracted')),
        log_odds REAL NOT NULL DEFAULT 0.0,
        confidence REAL NOT NULL DEFAULT 0.5,
        validity_from REAL,
        validity_until REAL,
        last_verified_at REAL,
        verification_source TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_proposition_beliefs_stance ON proposition_beliefs(stance, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_proposition_beliefs_claim ON proposition_beliefs(claim)",
    # ----------------------------------------------------------------
    # Phase 1 — Memory Engine Hardening
    # ----------------------------------------------------------------
    # Cache-stable memory prefix: frozen per-session digest
    "ALTER TABLE sessions ADD COLUMN memory_summary TEXT",
    # FTS5 full-text search over memories (porter stemmer + unicode)
    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content, id UNINDEXED, tokenize='porter unicode61')",
    # FTS5 full-text search over conversation turns
    "CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(user_input, response, id UNINDEXED, tokenize='porter unicode61')",
    # Index last maintenance pass timestamps in user_profiles.global_preferences (JSON)
    # No schema change needed — stored as JSON key inside existing global_preferences column.
]


# ---------------------------------------------------------------------------
# Database connection management
# ---------------------------------------------------------------------------

class Database:
    """Async SQLite database wrapper for ARIA with serialized background write queue."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(SILEX_DB)
        self._conn: aiosqlite.Connection | None = None
        self.write_queue = asyncio.Queue(maxsize=10000)
        self.worker_task = None
        self.is_running = False
        self._write_conn = None

    async def connect(self) -> None:
        """Open the database connection and ensure schema exists."""
        log.info(f"Connecting to database: {self.db_path}")
        self._conn = await aiosqlite.connect(self.db_path, timeout=15.0)
        self._conn.row_factory = aiosqlite.Row

        # Enable WAL mode + settings for robust multi-process concurrency
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=15000")
        await self._conn.execute("PRAGMA foreign_keys=ON")

        # Create tables if they don't exist
        await self._conn.executescript(SCHEMA_SQL)
        await self._run_migrations()
        # Seed the default user profile to satisfy the foreign key constraint
        await self._conn.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES ('default')")
        await self._conn.commit()
        
        # Start background writer task
        self.is_running = True
        self.worker_task = asyncio.create_task(self._writer_loop_supervisor())
        
        log.info("Database schema initialized and background writer queue started")

    async def _run_migrations(self) -> None:
        """Apply additive migrations for existing local SQLite brains."""
        for sql in MIGRATIONS_SQL:
            try:
                await self._conn.execute(sql)
            except aiosqlite.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise

    async def close(self) -> None:
        """Close the database connection and shut down the writer loop."""
        self.is_running = False
        # Stop background writer worker task
        await self.write_queue.put(None)
        if self.worker_task:
            try:
                await self.worker_task
            except Exception:
                pass
            self.worker_task = None
            
        if self._conn:
            await self._conn.close()
            self._conn = None
            log.info("Database connection closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        """Get the active connection or fail."""
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    def _is_write_query(self, sql: str) -> bool:
        sql_stripped = sql.strip().upper()
        # Any query modifying data is enqueued
        write_keywords = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "BEGIN", "COMMIT", "ROLLBACK")
        return sql_stripped.startswith(write_keywords)

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """Execute a single SQL statement. Auto-commits or routes writes to the background queue."""
        tx_conn = active_transaction_conn_var.get()
        if tx_conn is not None:
            # We are inside a transaction: run directly on the transaction writer connection
            return await tx_conn.execute(sql, params)

        if self._is_write_query(sql):
            # Write query outside transaction: execute through background writer queue
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            await self.write_queue.put((sql, params, future))
            return await future
        else:
            # Read query: run on main read-only connection
            return await self.conn.execute(sql, params)

    @asynccontextmanager
    async def transaction(self):
        """
        Atomic transaction context manager.
        Supports nested transactions via ContextVar.
        Outermost transaction enqueues a transaction lease task, executes BEGIN IMMEDIATE
        on the writer connection, and blocks other queue operations.
        """
        depth = transaction_depth_var.get()
        transaction_depth_var.set(depth + 1)
        
        if depth == 0:
            start_event = asyncio.Event()
            done_event = asyncio.Event()
            finish_event = asyncio.Event()
            action_var = ContextVar("tx_action", default="commit")
            
            # Put transaction request in queue
            await self.write_queue.put((None, None, start_event, done_event, (action_var, finish_event)))
            await start_event.wait()
            
            # Store connection in ContextVar
            active_transaction_conn_var.set(self._write_conn)
            
            try:
                yield
                action_var.set("commit")
            except Exception:
                action_var.set("rollback")
                raise
            finally:
                done_event.set()
                await finish_event.wait()
                active_transaction_conn_var.set(None)
                transaction_depth_var.set(0)
        else:
            try:
                yield
            finally:
                transaction_depth_var.set(transaction_depth_var.get() - 1)

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        """Fetch a single row as a dict."""
        cursor = await self.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        """Fetch all rows as dicts."""
        cursor = await self.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _writer_loop_supervisor(self):
        """Supervises the background database writer loop, automatically re-spawning it if it fails/cancels."""
        log.info("Database writer loop supervisor started")
        while self.is_running:
            try:
                # Run the actual writer queue loop
                await self._process_write_queue_loop()
            except asyncio.CancelledError:
                log.info("Database writer loop supervisor cancelled")
                break
            except Exception as e:
                log.critical(
                    f"CRITICAL TELEMETRY: Database writer loop failed with exception: {e}. "
                    f"Re-spawning consumer task connection cleanly...",
                    exc_info=True
                )
                if self._write_conn:
                    try:
                        await self._write_conn.close()
                    except Exception:
                        pass
                    self._write_conn = None
                await asyncio.sleep(0.5)

    async def _process_write_queue_loop(self):
        """Dedicated writer loop consuming writes sequentially over a single connection."""
        from clasp.vendor.silex.utils.config import KRONOS_HOME
        lock_file_path = str(KRONOS_HOME / "runtime" / "db_write.lock")
        file_lock = FileLock(lock_file_path)

        self._write_conn = await aiosqlite.connect(self.db_path, timeout=15.0)
        self._write_conn.row_factory = aiosqlite.Row
        
        await self._write_conn.execute("PRAGMA journal_mode=WAL")
        await self._write_conn.execute("PRAGMA synchronous=NORMAL")
        await self._write_conn.execute("PRAGMA busy_timeout=15000")
        await self._write_conn.execute("PRAGMA foreign_keys=ON")
        await self._write_conn.commit()
        
        while self.is_running:
            try:
                item = await self.write_queue.get()
                if item is None:
                    self.write_queue.task_done()
                    break
                
                # Check if it is a transaction request
                if item[0] is None:
                    _, _, start_event, done_event, payload = item
                    action_var, finish_event = payload
                    
                    lock_acquired = await asyncio.to_thread(file_lock.acquire, timeout=15.0)
                    if not lock_acquired:
                        log.error("Failed to acquire database file lock for transaction.")
                        start_event.set()
                        finish_event.set()
                        self.write_queue.task_done()
                        continue
                        
                    try:
                        await self._write_conn.execute("BEGIN IMMEDIATE;")
                        start_event.set()
                        await done_event.wait()
                        
                        action = action_var.get("commit")
                        if action == "commit":
                            await self._write_conn.commit()
                        else:
                            await self._write_conn.rollback()
                    except Exception as e:
                        log.error(f"Transaction in background writer failed: {e}")
                        try:
                            await self._write_conn.rollback()
                        except Exception:
                            pass
                    finally:
                        await asyncio.to_thread(file_lock.release)
                        finish_event.set()
                        self.write_queue.task_done()
                    continue
                
                # Regular single query write
                query, params, future = item
                lock_acquired = await asyncio.to_thread(file_lock.acquire, timeout=15.0)
                if not lock_acquired:
                    future.set_exception(aiosqlite.OperationalError("Database is locked (file lock timeout)"))
                    self.write_queue.task_done()
                    continue
                    
                try:
                    await self._write_conn.execute("BEGIN IMMEDIATE;")
                    cursor = await self._write_conn.execute(query, params)
                    await self._write_conn.commit()
                    future.set_result(cursor)
                except Exception as ex:
                    try:
                        await self._write_conn.rollback()
                    except Exception:
                        pass
                    future.set_exception(ex)
                finally:
                    await asyncio.to_thread(file_lock.release)
                    self.write_queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"Error in background writer loop: {e}")
                raise
                
        await self._write_conn.close()
        self._write_conn = None
