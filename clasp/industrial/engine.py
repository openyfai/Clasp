# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/engine.py
===========================
IndustrialSilexEngine — the top-level entry point for Clasp.

Wraps:
  - Database (aiosqlite async SQLite)
  - KnowledgeGraph (NetworkX + SQLite causal graph)
  - TimeBuffer (rolling in-memory time-series)
  - CausalLearner (discovers causal edges from observations)

Does NOT subclass any Silex internals — pure composition.

Usage:
    engine = IndustrialSilexEngine()
    await engine.initialize()

    # Register nodes (once, at startup)
    node_id = await engine.register_node("XMEAS_1", "Reactor Feed A Flow", "ProcessVariable")

    # Feed observations (continuously)
    await engine.observe("xmeas_1", value=42.3, timestamp=time.time())

    # Query
    state = await engine.get_current_state()
    stats = await engine.get_graph_stats()
    chain = await engine.root_cause_analysis("xmeas_35", event_time=1234567890.0)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from clasp.config import (
    CAUSAL_MIN_CONFIDENCE,
    CAUSAL_MIN_OCCURRENCES,
    CAUSAL_SIGNIFICANCE_Z,
    CAUSAL_TIME_WINDOW,
    CLASP_DB,
    CLASP_WORKSPACE,
)
from clasp.industrial.causal_learner import CausalLearner
from clasp.industrial.schemas import (
    Alert,
    GraphStats,
    IndustrialEdgeType,
    IndustrialNodeType,
    RootCauseResult,
    RootCauseStep,
    SensorObservation,
)
from clasp.industrial.time_buffer import TimeBuffer
from clasp.vendor.silex.models.schemas import CausalEdge, KnowledgeNode
from clasp.vendor.silex.storage.database import Database
from clasp.vendor.silex.world.graph import KnowledgeGraph

log = logging.getLogger("clasp.engine")


class IndustrialSilexEngine:
    """
    The heart of Clasp.

    One instance per process. Initialize with await engine.initialize()
    before calling any other method.
    """

    def __init__(
        self,
        db_path: str | None = None,
        time_window: int | None = None,
        min_occurrences: int | None = None,
        min_confidence: float | None = None,
        significance_z: float | None = None,
        buffer_window_seconds: int = 7200,
    ):
        """
        Args:
            db_path:               Path to SQLite file. Defaults to ~/.clasp/storage/clasp.db.
            time_window:           Causal scan window in seconds (default: CAUSAL_TIME_WINDOW).
            min_occurrences:       Min co-occurrences to assert causality.
            min_confidence:        Min confidence threshold for causal edges.
            significance_z:        Z-score threshold for significant observations.
            buffer_window_seconds: How much history to keep in TimeBuffer.
        """
        # Resolve DB path
        _db_path = db_path or str(CLASP_DB)
        Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

        # Silex components (initialized in initialize())
        self._db: Database = Database(db_path=_db_path)
        self._graph: KnowledgeGraph | None = None
        self._initialized = False

        # Industrial components
        self._time_buffer = TimeBuffer(window_seconds=buffer_window_seconds)
        self._learner: CausalLearner | None = None

        # Causal engine parameters (resolved from args or config)
        self._time_window = time_window or CAUSAL_TIME_WINDOW
        self._min_occurrences = min_occurrences or CAUSAL_MIN_OCCURRENCES
        self._min_confidence = min_confidence or CAUSAL_MIN_CONFIDENCE
        self._significance_z = significance_z or CAUSAL_SIGNIFICANCE_Z

        # Node registry: label -> node_id (for fast lookup without graph queries)
        self._node_registry: dict[str, str] = {}  # label -> id
        self._node_id_to_label: dict[str, str] = {}  # id -> label
        self._node_id_to_type: dict[str, str] = {}   # id -> node_type string

        # Active alerts (in-memory, cleared on restart)
        self._active_alerts: list[Alert] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Initialize the engine: connect to DB, load graph, wire up learner.
        Must be called before any other method.
        """
        if self._initialized:
            return

        log.info("Initializing IndustrialSilexEngine...")

        # 1. Connect to SQLite and run schema migrations
        await self._db.connect()
        log.info("Database connected: %s", self._db.db_path)

        # 2. Initialize and load the knowledge graph
        self._graph = KnowledgeGraph(self._db)
        await self._graph.load()
        log.info(
            "KnowledgeGraph loaded: %d nodes, %d edges",
            self._graph.graph.number_of_nodes(),
            self._graph.graph.number_of_edges(),
        )

        # 3. Rebuild node registry from loaded graph
        for node_id, data in self._graph.graph.nodes(data=True):
            label = data.get("content", node_id)
            self._node_registry[label] = node_id
            self._node_id_to_label[node_id] = label
            self._node_id_to_type[node_id] = data.get("node_type", "ProcessVariable")

        # 4. Wire up CausalLearner
        self._learner = CausalLearner(
            graph=self._graph,
            time_buffer=self._time_buffer,
            time_window=self._time_window,
            min_occurrences=self._min_occurrences,
            min_confidence=self._min_confidence,
            significance_z=self._significance_z,
        )

        self._initialized = True
        log.info(
            "IndustrialSilexEngine ready (time_window=%ds, min_occ=%d, min_conf=%.2f, z=%.1f)",
            self._time_window, self._min_occurrences, self._min_confidence, self._significance_z,
        )

    async def close(self) -> None:
        """Gracefully close the database connection."""
        if self._db:
            await self._db.close()
        self._initialized = False
        log.info("IndustrialSilexEngine closed.")

    def _require_initialized(self) -> None:
        if not self._initialized or self._graph is None or self._learner is None:
            raise RuntimeError("Engine not initialized. Call await engine.initialize() first.")

    # ------------------------------------------------------------------
    # Node Registration
    # ------------------------------------------------------------------

    async def register_node(
        self,
        node_id: str,
        label: str,
        node_type: str | IndustrialNodeType = IndustrialNodeType.PROCESS_VARIABLE,
    ) -> str:
        """
        Register a plant variable as a node in the causal graph.

        If a node with the same label already exists (from a previous run),
        the existing node is returned without creating a duplicate.

        Args:
            node_id:   Deterministic short ID (e.g. "xmeas_1", "xmv_10").
                       Used as the primary lookup key in observe() calls.
            label:     Human-readable label (e.g. "XMEAS(1) A Feed Flow").
                       Stored as the node's content in the KnowledgeGraph.
            node_type: IndustrialNodeType or its string value.

        Returns:
            The actual node UUID in the KnowledgeGraph.
        """
        self._require_initialized()
        node_type_str = node_type.value if isinstance(node_type, IndustrialNodeType) else node_type

        # Check if this node_id was already registered in this session
        if node_id in self._node_registry:
            return self._node_registry[node_id]

        # Check if a node with this exact label already exists in the graph.
        # Do NOT use graph.find_node_by_content() — it does partial/word-overlap matching
        # and creates false positives between similarly named nodes (e.g. "XMEAS 1 A Flow"
        # matches "XMEAS 2 B Flow" because they share many words).
        # Use an exact content match loop over the in-memory graph instead.
        existing_graph_id = None
        for nid, data in self._graph.graph.nodes(data=True):
            if data.get("content", "") == label:
                existing_graph_id = nid
                break

        if existing_graph_id:
            self._node_registry[node_id] = existing_graph_id
            self._node_id_to_label[existing_graph_id] = label
            self._node_id_to_type[existing_graph_id] = node_type_str
            return existing_graph_id

        # Map industrial type to the nearest valid NodeType enum value.
        # KnowledgeNode.node_type must be one of: fact, concept, entity, hypothesis, principle.
        # We store the full industrial type in metadata for downstream queries.
        _INDUSTRIAL_TO_NODE_TYPE = {
            IndustrialNodeType.PROCESS_VARIABLE.value: "entity",   # named measurable thing
            IndustrialNodeType.EQUIPMENT_UNIT.value:   "entity",   # named physical device
            IndustrialNodeType.ALARM_EVENT.value:      "fact",     # observed event
            IndustrialNodeType.OPERATOR_ACTION.value:  "fact",     # observed action
            IndustrialNodeType.QUALITY_METRIC.value:   "concept",  # abstract quality measure
        }
        graph_node_type = _INDUSTRIAL_TO_NODE_TYPE.get(node_type_str, "entity")

        # Create new KnowledgeNode
        # Use a deterministic UUID based on the node_id so that re-registering
        # the same sensor across restarts always gets the same DB row.
        deterministic_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"clasp.node.{node_id}"))
        node = KnowledgeNode(
            id=deterministic_id,
            content=label,
            node_type=graph_node_type,           # must be valid NodeType enum value
            confidence=1.0,                       # sensor nodes are certain (they exist)
            source="plant_registration",
            metadata={"industrial_type": node_type_str},  # full type stored here
        )
        added_node = await self._graph.add_node(node)

        actual_id = added_node.id
        self._node_registry[node_id] = actual_id
        self._node_id_to_label[actual_id] = label
        self._node_id_to_type[actual_id] = node_type_str

        log.debug("Registered node: %s (%s) -> %s", node_id, node_type_str, actual_id[:8])
        return actual_id

    def get_node_id(self, node_key: str) -> str | None:
        """
        Resolve a node key (short id like 'xmeas_1') to its graph UUID.
        Returns None if the node is not registered.
        """
        return self._node_registry.get(node_key)

    # ------------------------------------------------------------------
    # Main ingestion point
    # ------------------------------------------------------------------

    async def observe(
        self,
        node_key: str,
        value: float,
        timestamp: float | None = None,
    ) -> list:
        """
        Feed a new sensor reading into the engine.

        This is the hot path — called once per variable per timestep.
        It: records to TimeBuffer → gates on significance → runs CausalLearner.

        Args:
            node_key:  Short node key (e.g. "xmeas_1"), as passed to register_node().
            value:     The sensor reading value.
            timestamp: Unix seconds. If None, uses current time.

        Returns:
            List of newly confirmed CausalPattern objects (usually empty).
        """
        self._require_initialized()
        ts = timestamp if timestamp is not None else time.time()

        # Resolve node_key -> graph UUID
        graph_id = self._node_registry.get(node_key)
        if graph_id is None:
            # Auto-register unknown nodes as ProcessVariable
            log.warning("Auto-registering unknown node: %s", node_key)
            graph_id = await self.register_node(
                node_key, node_key, IndustrialNodeType.PROCESS_VARIABLE
            )

        # Record to TimeBuffer
        self._time_buffer.record(graph_id, value, ts)

        # Run CausalLearner
        obs = SensorObservation(node_id=graph_id, value=value, timestamp=ts)
        new_patterns = await self._learner.on_observation(obs)

        return new_patterns

    # ------------------------------------------------------------------
    # Root Cause Analysis
    # ------------------------------------------------------------------

    async def root_cause_analysis(
        self,
        node_key: str,
        event_time: float,
        max_depth: int = 5,
    ) -> RootCauseResult:
        """
        Backward BFS from an affected node to find the root cause chain.

        Algorithm:
          For each depth level, look for incoming causes_with_lag edges.
          For each candidate cause, check if an actual change occurred in the
          TimeBuffer at approximately (event_time - lag_seconds).
          Score candidates by confidence × observed magnitude.
          Follow the highest-scoring candidate.

        Args:
            node_key:   Short node key (e.g. "xmeas_35").
            event_time: Unix timestamp when the problem was first observed.
            max_depth:  Maximum chain depth to search (default 5).

        Returns:
            RootCauseResult with ordered chain (root cause first) + empty explanation
            (explanation is filled by RootCauseAgent using LLM).
        """
        self._require_initialized()
        start_ms = time.time() * 1000

        graph_id = self._node_registry.get(node_key)
        if graph_id is None:
            return RootCauseResult(
                affected_node=node_key,
                event_time=event_time,
                chain=[],
                explanation=f"Node '{node_key}' not registered in this engine.",
            )

        chain: list[RootCauseStep] = []
        current_id = graph_id
        current_time = event_time

        # Start step = the affected node itself
        chain.append(RootCauseStep(
            node_id=current_id,
            node_label=self._node_id_to_label.get(current_id, current_id),
            value=self._time_buffer.get_value_near(current_id, event_time),
            timestamp=event_time,
            lag_seconds=None,
            confidence=None,
        ))

        for depth in range(max_depth):
            # Find incoming causes_with_lag edges for current node
            candidate_causes = self._find_incoming_causal_edges(current_id)
            if not candidate_causes:
                break

            # Score each candidate cause
            best_score = -1.0
            best_cause_id = None
            best_lag = 0.0
            best_conf = 0.0

            for cause_id, lag_seconds, edge_strength in candidate_causes:
                expected_cause_time = current_time - lag_seconds
                # Look for an actual observation near that time
                observed_value = self._time_buffer.get_value_near(
                    cause_id, expected_cause_time, tolerance_seconds=max(lag_seconds * 0.5, 300.0)
                )
                if observed_value is None:
                    # No observation near expected time — score lower but don't discard
                    magnitude = 0.1
                else:
                    z = self._time_buffer.z_score(cause_id, observed_value)
                    magnitude = z if z is not None else 0.5

                score = edge_strength * magnitude
                if score > best_score:
                    best_score = score
                    best_cause_id = cause_id
                    best_lag = lag_seconds
                    best_conf = edge_strength

            if best_cause_id is None or best_cause_id == current_id:
                break

            # Avoid cycles
            if any(step.node_id == best_cause_id for step in chain):
                break

            # Step backward
            cause_time = current_time - best_lag
            cause_value = self._time_buffer.get_value_near(best_cause_id, cause_time)

            chain.append(RootCauseStep(
                node_id=best_cause_id,
                node_label=self._node_id_to_label.get(best_cause_id, best_cause_id),
                value=cause_value,
                timestamp=cause_time,
                lag_seconds=best_lag,
                confidence=best_conf,
            ))

            current_id = best_cause_id
            current_time = cause_time

        # Reverse: root cause first, observed effect last
        chain.reverse()

        elapsed_ms = time.time() * 1000 - start_ms
        return RootCauseResult(
            affected_node=node_key,
            event_time=event_time,
            chain=chain,
            explanation="",  # filled by RootCauseAgent
            analysis_duration_ms=elapsed_ms,
        )

    def _find_incoming_causal_edges(self, node_id: str) -> list[tuple[str, float, float]]:
        """
        Find all incoming causes_with_lag edges for a node.

        Returns list of (source_node_id, lag_seconds, strength).
        lag_seconds is approximated from the edge strength (higher strength = more observations).

        Note: The silex KnowledgeGraph stores edges without lag metadata in the edge schema.
        We embed lag info in the `evidence` string field at write time and use the
        CausalLearner's in-memory evidence table as the primary lag source.
        """
        results = []

        if not self._graph or node_id not in self._graph.graph:
            return results

        # Query in-memory NetworkX graph for incoming causal edges.
        # We write edges with edge_type="causes" (the valid EdgeType enum value).
        # The lag is stored in the evidence string and in the CausalLearner evidence table.
        for source, target, data in self._graph.graph.in_edges(node_id, data=True):
            if data.get("edge_type") == "causes":
                strength = data.get("strength", 0.5)
                # Get lag from CausalLearner evidence (in-memory)
                ev = self._learner._evidence.get((source, node_id))
                if ev and ev.occurrences > 0:
                    lag = ev.lag_sum / ev.occurrences
                else:
                    # Estimate lag from evidence string if available
                    lag = self._parse_lag_from_evidence(data.get("evidence", ""))
                results.append((source, lag, strength))

        # Sort by strength desc
        results.sort(key=lambda x: x[2], reverse=True)
        return results

    def _parse_lag_from_evidence(self, evidence: str) -> float:
        """Extract lag from evidence string 'Observed N times with avg lag Xs'."""
        try:
            # Format: "Observed N times with avg lag Xs (confidence C)"
            if "lag" in evidence and "s" in evidence:
                parts = evidence.split("lag")
                if len(parts) > 1:
                    lag_part = parts[1].strip().split("s")[0].strip()
                    return float(lag_part)
        except Exception:
            pass
        return 300.0  # default 5 min if parsing fails

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    async def get_current_state(self) -> dict[str, float]:
        """
        Return the latest reading for every registered node.
        Maps short node_key -> latest_value.
        """
        self._require_initialized()
        result = {}
        raw_state = self._time_buffer.current_state()
        # Map graph UUIDs back to short node keys
        id_to_key = {v: k for k, v in self._node_registry.items()}
        for graph_id, value in raw_state.items():
            key = id_to_key.get(graph_id, graph_id)
            result[key] = value
        return result

    async def get_graph_stats(self) -> GraphStats:
        """Return statistics about the current state of the causal graph."""
        self._require_initialized()
        raw = self._graph.stats()
        return GraphStats(
            total_nodes=raw["total_nodes"],
            total_edges=raw["total_edges"],
            # Edges are stored with edge_type="causes" (the valid EdgeType enum value).
            # The IndustrialEdgeType.CAUSES_WITH_LAG semantic is encoded in the evidence string.
            causes_with_lag_edges=raw["edge_types"].get("causes", 0),
            node_types=raw["node_types"],
            edge_types=raw["edge_types"],
            isolated_nodes=raw["isolated_nodes"],
        )

    async def export_graph_for_d3(self) -> dict:
        """
        Serialize the causal graph as D3.js-ready JSON.

        Returns:
            {
                "nodes": [{"id": ..., "label": ..., "type": ..., "confidence": ...}],
                "links": [{"source": ..., "target": ..., "type": ..., "strength": ...}]
            }
        """
        self._require_initialized()
        nodes = []
        for node_id, data in self._graph.graph.nodes(data=True):
            nodes.append({
                "id": node_id,
                "label": data.get("content", node_id)[:60],
                "type": data.get("node_type", "ProcessVariable"),
                "confidence": data.get("confidence", 0.5),
            })

        links = []
        for src, tgt, data in self._graph.graph.edges(data=True):
            links.append({
                "source": src,
                "target": tgt,
                "type": data.get("edge_type", "unknown"),
                "strength": data.get("strength", 0.5),
            })

        return {"nodes": nodes, "links": links}

    # ------------------------------------------------------------------
    # Alert management (used by WatcherAgent in Phase 3)
    # ------------------------------------------------------------------

    def add_alert(self, alert: Alert) -> None:
        """Add an alert to the active alerts list (called by WatcherAgent)."""
        self._active_alerts.append(alert)
        # Keep only the 100 most recent alerts
        if len(self._active_alerts) > 100:
            self._active_alerts = self._active_alerts[-100:]

    def get_active_alerts(self) -> list[Alert]:
        """Return the current list of active alerts."""
        return list(self._active_alerts)

    def clear_alerts(self) -> None:
        """Clear all active alerts."""
        self._active_alerts.clear()

    # ------------------------------------------------------------------
    # Expose internals for agents (read-only access)
    # ------------------------------------------------------------------

    @property
    def graph(self) -> KnowledgeGraph:
        self._require_initialized()
        return self._graph

    @property
    def time_buffer(self) -> TimeBuffer:
        return self._time_buffer

    @property
    def learner(self) -> CausalLearner:
        self._require_initialized()
        return self._learner

    @property
    def node_registry(self) -> dict[str, str]:
        """Short key -> graph UUID mapping."""
        return self._node_registry
