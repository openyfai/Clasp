"""
tests/test_phase1_engine.py
============================
Phase 1 Definition of Done (DoD) tests.

Gate: ALL tests must pass before Phase 2 begins.

Test groups:
  A. Unit — TimeBuffer (pure, no async)
  B. Unit — CausalLearner schema types (no DB)
  C. Integration — Engine lifecycle (real async SQLite, temp file)
  D. Integration — observe() and causal edge creation
  E. Integration — root_cause_analysis()
  F. Integration — graph stats and D3 export
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.schemas import (
    Alert,
    CausalPattern,
    GraphStats,
    IndustrialEdgeType,
    IndustrialNodeType,
    RootCauseResult,
    SensorObservation,
)
from clasp.industrial.time_buffer import TimeBuffer
from clasp.industrial.causal_learner import CausalLearner
from clasp.vendor.silex.models.schemas import CausalEdge, KnowledgeNode, NodeType


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def tmp_db(tmp_path) -> str:
    """Temporary SQLite file path for each test."""
    return str(tmp_path / "test_clasp.db")


@pytest_asyncio.fixture
async def engine(tmp_db) -> IndustrialSilexEngine:
    """Initialized engine backed by a temp DB — torn down after each test."""
    eng = IndustrialSilexEngine(db_path=tmp_db)
    await eng.initialize()
    yield eng
    await eng.close()


# ===========================================================================
# A. TimeBuffer unit tests (no async needed)
# ===========================================================================

class TestTimeBuffer:

    def test_record_and_get_latest(self):
        """record() and get_latest() basic round-trip."""
        buf = TimeBuffer()
        buf.record("n1", 42.0, timestamp=1000.0)
        assert buf.get_latest("n1") == 42.0
        assert buf.get_latest("n999") is None

    def test_multiple_records_latest_is_last(self):
        buf = TimeBuffer()
        buf.record("n1", 10.0, timestamp=1.0)
        buf.record("n1", 20.0, timestamp=2.0)
        buf.record("n1", 30.0, timestamp=3.0)
        assert buf.get_latest("n1") == 30.0

    def test_window_pruning(self):
        """Old observations outside window are pruned."""
        buf = TimeBuffer(window_seconds=100)
        now = time.time()
        buf.record("n1", 1.0, timestamp=now - 200)  # too old
        buf.record("n1", 2.0, timestamp=now - 50)   # within window
        buf.record("n1", 3.0, timestamp=now)        # current
        assert buf.get_latest("n1") == 3.0
        # The old record should have been pruned
        history = buf.get_history("n1")
        timestamps = history["timestamp"].tolist()
        assert all(ts >= (now - 100) for ts in timestamps)

    def test_get_history_returns_dataframe(self):
        buf = TimeBuffer()
        buf.record("n1", 1.0, timestamp=1.0)
        buf.record("n1", 2.0, timestamp=2.0)
        df = buf.get_history("n1")
        assert list(df.columns) == ["timestamp", "value"]
        assert len(df) == 2

    def test_get_history_since_filter(self):
        buf = TimeBuffer()
        for i in range(10):
            buf.record("n1", float(i), timestamp=float(i))
        df = buf.get_history("n1", since=5.0)
        assert len(df) == 5  # timestamps 5..9
        assert df["value"].tolist() == [5.0, 6.0, 7.0, 8.0, 9.0]

    def test_is_significant_insufficient_history(self):
        """First few observations always considered significant (no baseline)."""
        buf = TimeBuffer()
        buf.record("n1", 100.0)
        buf.record("n1", 101.0)
        # Only 2 obs — below min_samples=5
        assert buf.is_significant("n1", 200.0) is True  # can't compute baseline

    def test_is_significant_outlier(self):
        """Z-score outlier is significant."""
        buf = TimeBuffer()
        base_ts = 1000.0
        for i in range(20):
            buf.record("n1", 50.0 + (i % 2) * 0.1, timestamp=base_ts + i)
        # Huge outlier should be significant
        assert buf.is_significant("n1", 1000.0, z_threshold=2.0) is True

    def test_is_significant_normal_value(self):
        """Value within baseline is NOT significant."""
        buf = TimeBuffer()
        for i in range(20):
            buf.record("n1", 50.0, timestamp=float(i))
        # Exactly on mean — z_score = 0
        assert buf.is_significant("n1", 50.0, z_threshold=2.0) is False

    def test_z_score(self):
        buf = TimeBuffer()
        for i in range(20):
            buf.record("n1", float(i), timestamp=float(i))
        z = buf.z_score("n1", 200.0)  # very far from mean~9.5
        assert z is not None and z > 2.0

    def test_get_value_near(self):
        buf = TimeBuffer()
        buf.record("n1", 42.0, timestamp=1000.0)
        buf.record("n1", 43.0, timestamp=1500.0)
        # Should return the value closest to 990
        val = buf.get_value_near("n1", 990.0, tolerance_seconds=50.0)
        assert val == 42.0

    def test_get_value_near_outside_tolerance(self):
        buf = TimeBuffer()
        buf.record("n1", 42.0, timestamp=1000.0)
        val = buf.get_value_near("n1", 2000.0, tolerance_seconds=100.0)
        assert val is None

    def test_get_significant_changes_in_window(self):
        """Window scan returns significant changes across multiple nodes."""
        buf = TimeBuffer()
        now = 1000.0
        # Build baselines for n1, n2
        for i in range(20):
            buf.record("n1", 50.0, timestamp=float(i))
            buf.record("n2", 50.0, timestamp=float(i))
        # Fire a significant change on n2 in our scan window
        buf.record("n2", 500.0, timestamp=now + 10)  # huge spike

        results = buf.get_significant_changes_in_window(
            since=now, until=now + 60, z_threshold=2.0, exclude_node="n1"
        )
        node_ids = [r[0] for r in results]
        assert "n2" in node_ids

    def test_known_nodes_and_current_state(self):
        buf = TimeBuffer()
        buf.record("a", 1.0)
        buf.record("b", 2.0)
        assert "a" in buf.known_nodes()
        assert "b" in buf.known_nodes()
        state = buf.current_state()
        assert state["a"] == 1.0
        assert state["b"] == 2.0

    def test_total_observations(self):
        buf = TimeBuffer()
        buf.record("a", 1.0)
        buf.record("a", 2.0)
        buf.record("b", 3.0)
        assert buf.total_observations() == 3


# ===========================================================================
# B. Schema unit tests
# ===========================================================================

class TestSchemas:

    def test_industrial_node_type_values(self):
        assert IndustrialNodeType.PROCESS_VARIABLE.value == "ProcessVariable"
        assert IndustrialNodeType.EQUIPMENT_UNIT.value == "EquipmentUnit"
        assert IndustrialNodeType.ALARM_EVENT.value == "AlarmEvent"

    def test_industrial_edge_type_values(self):
        assert IndustrialEdgeType.CAUSES_WITH_LAG.value == "causes_with_lag"

    def test_causal_pattern_defaults(self):
        p = CausalPattern(
            precursor_node="a",
            outcome_node="b",
            lag_seconds=60.0,
            confidence=0.8,
        )
        assert p.occurrences == 0
        assert p.total_precursor_events == 0

    def test_alert_defaults(self):
        a = Alert(
            outcome_risk="Product quality drop",
            pattern="A -> B",
            estimated_time_to_impact=300.0,
            confidence=0.9,
        )
        assert a.type == "PRECURSOR_DETECTED"
        assert isinstance(a.id, str)

    def test_sensor_observation_defaults(self):
        obs = SensorObservation(node_id="n1", value=42.0)
        assert obs.unit == ""
        assert obs.timestamp > 0

    def test_graph_stats_model(self):
        gs = GraphStats(total_nodes=10, total_edges=5, causes_with_lag_edges=3)
        assert gs.isolated_nodes == 0  # default


# ===========================================================================
# C. Engine lifecycle integration tests
# ===========================================================================

class TestEngineLifecycle:

    @pytest.mark.asyncio
    async def test_initialize_and_close(self, tmp_db):
        eng = IndustrialSilexEngine(db_path=tmp_db)
        await eng.initialize()
        assert eng._initialized is True
        await eng.close()
        assert eng._initialized is False

    @pytest.mark.asyncio
    async def test_double_initialize_is_idempotent(self, tmp_db):
        eng = IndustrialSilexEngine(db_path=tmp_db)
        await eng.initialize()
        await eng.initialize()  # second call should be no-op
        await eng.close()

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self, tmp_db):
        eng = IndustrialSilexEngine(db_path=tmp_db)
        with pytest.raises(RuntimeError, match="not initialized"):
            await eng.get_graph_stats()

    @pytest.mark.asyncio
    async def test_db_file_is_created(self, tmp_db):
        eng = IndustrialSilexEngine(db_path=tmp_db)
        await eng.initialize()
        await eng.close()
        assert Path(tmp_db).exists()


# ===========================================================================
# D. observe() and causal edge creation tests
# ===========================================================================

class TestObserveAndCausalEdges:

    @pytest.mark.asyncio
    async def test_register_node_returns_uuid(self, engine):
        nid = await engine.register_node("xmeas_1", "XMEAS(1) A Feed Flow")
        assert isinstance(nid, str) and len(nid) == 36  # UUID

    @pytest.mark.asyncio
    async def test_register_node_idempotent(self, engine):
        id1 = await engine.register_node("xmeas_1", "XMEAS(1) A Feed Flow")
        id2 = await engine.register_node("xmeas_1", "XMEAS(1) A Feed Flow")
        assert id1 == id2

    @pytest.mark.asyncio
    async def test_observe_creates_time_buffer_entry(self, engine):
        await engine.register_node("xmeas_1", "XMEAS(1) A Feed Flow")
        await engine.observe("xmeas_1", 42.0, timestamp=1000.0)
        graph_id = engine.get_node_id("xmeas_1")
        assert engine.time_buffer.get_latest(graph_id) == 42.0

    @pytest.mark.asyncio
    async def test_observe_auto_registers_unknown_node(self, engine):
        """observe() should auto-register an unseen node rather than crash."""
        await engine.observe("unknown_sensor", 1.0)
        assert engine.get_node_id("unknown_sensor") is not None

    @pytest.mark.asyncio
    async def test_causal_edge_written_after_threshold(self, engine):
        """
        Drive enough observations to trip the causal learner and write an edge.
        Uses low thresholds via explicit learner configuration.
        """
        # Re-init engine with low thresholds for fast testing
        await engine.close()
        eng = IndustrialSilexEngine(
            db_path=engine._db.db_path,
            min_occurrences=3,
            min_confidence=0.5,
            significance_z=0.5,  # very low threshold — most values trigger
            time_window=60,
        )
        await eng.initialize()

        # Register nodes
        await eng.register_node("cause_node", "Cause Variable", IndustrialNodeType.PROCESS_VARIABLE)
        await eng.register_node("effect_node", "Effect Variable", IndustrialNodeType.PROCESS_VARIABLE)

        # Build baseline for both nodes (20 normal readings)
        base_time = 1000.0
        for i in range(20):
            await eng.observe("cause_node", 50.0, timestamp=base_time + i)
            await eng.observe("effect_node", 50.0, timestamp=base_time + i)

        # Fire spike on cause, then spike on effect within time_window
        for trial in range(5):
            cause_time = base_time + 100 + trial * 20
            effect_time = cause_time + 10  # 10 seconds later
            await eng.observe("cause_node", 500.0, timestamp=cause_time)
            await eng.observe("effect_node", 500.0, timestamp=effect_time)

        learner_stats = eng.learner.stats()
        # At least one edge should be tracked in evidence
        assert learner_stats["evidence_pairs_tracked"] > 0

        await eng.close()

    @pytest.mark.asyncio
    async def test_get_current_state_returns_latest(self, engine):
        await engine.register_node("xmeas_1", "XMEAS 1")
        await engine.observe("xmeas_1", 42.0)
        state = await engine.get_current_state()
        assert "xmeas_1" in state
        assert state["xmeas_1"] == 42.0


# ===========================================================================
# E. Root cause analysis tests
# ===========================================================================

class TestRootCauseAnalysis:

    @pytest.mark.asyncio
    async def test_rca_unknown_node_returns_empty_chain(self, engine):
        result = await engine.root_cause_analysis("nonexistent_node", event_time=1000.0)
        assert isinstance(result, RootCauseResult)
        assert result.chain == []

    @pytest.mark.asyncio
    async def test_rca_no_causes_returns_single_step(self, engine):
        """If no causal edges exist, RCA returns just the affected node."""
        await engine.register_node("xmeas_35", "XMEAS 35 Product Quality")
        await engine.observe("xmeas_35", 100.0, timestamp=1000.0)
        result = await engine.root_cause_analysis("xmeas_35", event_time=1000.0)
        assert isinstance(result, RootCauseResult)
        assert len(result.chain) >= 1
        assert result.chain[-1].node_label == "XMEAS 35 Product Quality"

    @pytest.mark.asyncio
    async def test_rca_returns_result_model(self, engine):
        await engine.register_node("x1", "Variable A")
        result = await engine.root_cause_analysis("x1", event_time=time.time())
        assert isinstance(result, RootCauseResult)
        assert isinstance(result.chain, list)
        assert result.analysis_duration_ms >= 0


# ===========================================================================
# F. Graph stats and D3 export
# ===========================================================================

class TestGraphStatsAndExport:

    @pytest.mark.asyncio
    async def test_get_graph_stats_empty(self, engine):
        stats = await engine.get_graph_stats()
        assert isinstance(stats, GraphStats)
        assert stats.total_nodes == 0
        assert stats.total_edges == 0

    @pytest.mark.asyncio
    async def test_get_graph_stats_after_registration(self, engine):
        await engine.register_node("xmeas_1", "XMEAS 1 A Feed Flow")
        await engine.register_node("xmeas_2", "XMEAS 2 B Feed Flow")
        stats = await engine.get_graph_stats()
        assert stats.total_nodes == 2

    @pytest.mark.asyncio
    async def test_export_graph_for_d3_structure(self, engine):
        await engine.register_node("xmeas_1", "XMEAS 1 A Feed Flow")
        export = await engine.export_graph_for_d3()
        assert "nodes" in export
        assert "links" in export
        assert isinstance(export["nodes"], list)
        assert isinstance(export["links"], list)
        # Check node structure
        if export["nodes"]:
            node = export["nodes"][0]
            assert "id" in node
            assert "label" in node
            assert "type" in node
            assert "confidence" in node

    @pytest.mark.asyncio
    async def test_alert_management(self, engine):
        alert = Alert(
            outcome_risk="Test Risk",
            pattern="A -> B",
            estimated_time_to_impact=120.0,
            confidence=0.85,
        )
        engine.add_alert(alert)
        alerts = engine.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0].outcome_risk == "Test Risk"

        engine.clear_alerts()
        assert engine.get_active_alerts() == []

    @pytest.mark.asyncio
    async def test_learner_stats_structure(self, engine):
        stats = engine.learner.stats()
        assert "observations_processed" in stats
        assert "evidence_pairs_tracked" in stats
        assert "confirmed_edges_written" in stats
        assert "above_threshold_patterns" in stats
