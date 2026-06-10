"""
tests/test_phase2_m1.py
========================
Phase 2 Definition of Done (DoD) tests.
Verifies Milestone M1 ("It learns"): The industrial engine can process
the TEP simulation data and automatically build a causal graph.
"""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.ingest.tep_simulator import TEPSimulator
from clasp.industrial.schemas import IndustrialEdgeType


@pytest_asyncio.fixture
async def tep_engine(tmp_path):
    """
    Creates an engine, runs the simulator on 600 rows (10 simulated minutes)
    of normal data at max speed, and returns the populated engine.
    """
    db_path = tmp_path / "test_clasp_phase2.db"
    
    # Use low thresholds to ensure we capture edges quickly in 600 rows
    engine = IndustrialSilexEngine(
        db_path=str(db_path),
        min_occurrences=2,
        min_confidence=0.5,
        significance_z=1.0,
        time_window=3600
    )
    await engine.initialize()
    
    data_path = Path("data/tep/d00.dat")
    if not data_path.exists():
        pytest.skip("TEP dataset not downloaded. Run scripts/download_tep.py first.")
        
    simulator = TEPSimulator(engine=engine, data_path=data_path, speed=0) # speed=0 for instant playback
    await simulator.register_tep_nodes()
    await simulator.run(max_rows=600)
    
    yield engine
    
    await engine.close()


@pytest.mark.asyncio
async def test_graph_has_20_plus_edges(tep_engine):
    """M1 Goal: After 10 simulated minutes, graph has >= 20 edges."""
    stats = await tep_engine.get_graph_stats()
    
    assert stats.total_nodes >= 53  # All 53 TEP nodes should be registered
    assert stats.causes_with_lag_edges >= 20, f"Expected >= 20 causal edges, got {stats.causes_with_lag_edges}"

@pytest.mark.asyncio
async def test_xmeas35_has_incoming_edges(tep_engine):
    """Quality metric (XMEAS 35) should have inferred root causes."""
    # XMEAS 35 is node key 'xmeas_35'
    node_id = tep_engine.get_node_id("xmeas_35")
    assert node_id is not None
    
    edges = tep_engine._find_incoming_causal_edges(node_id)
    assert len(edges) > 0, "XMEAS_35 should have discovered at least one causal precursor"

@pytest.mark.asyncio
async def test_xmv10_has_outgoing_edges(tep_engine):
    """Manipulated variable (cooling water XMV 10) should have downstream effects."""
    node_id = tep_engine.get_node_id("xmv_10")
    assert node_id is not None
    
    # Look for any 'causes' edge where source is XMV 10
    graph = tep_engine.graph.graph
    out_edges = []
    for src, tgt, data in graph.out_edges(node_id, data=True):
        if data.get("edge_type") == "causes":
            out_edges.append(tgt)
            
    assert len(out_edges) > 0, "XMV_10 should have discovered downstream effects"
