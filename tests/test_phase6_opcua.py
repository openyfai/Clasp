"""
tests/test_phase6_opcua.py
==========================
Phase 6 Definition of Done (DoD) tests.
Verifies Milestone M5 ("A real plant can use it"): The system can connect
to an OPC-UA server, discover tags, and ingest telemetry into the graph.
"""

import asyncio
import pytest
import pytest_asyncio
from asyncua import Server

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.ingest.opcua_connector import OPCUAConnector

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest_asyncio.fixture
async def local_opcua_server():
    """Sets up a local mock OPC-UA server for testing."""
    import random
    port = random.randint(40000, 50000)
    server = Server()
    await server.init()
    endpoint = f"opc.tcp://127.0.0.1:{port}/freeopcua/server/"
    server.set_endpoint(endpoint)
    
    # Setup our own namespace
    uri = "http://clasp.tests"
    idx = await server.register_namespace(uri)
    
    # Create an object and some variables
    myobj = await server.nodes.objects.add_object(idx, "MyDevice")
    var1 = await myobj.add_variable(idx, "XMEAS_1", 25.5)
    var2 = await myobj.add_variable(idx, "XMV_10", 90.0)
    var3 = await myobj.add_variable(idx, "XMEAS_35", 0.99)
    
    # Set variables to be writable
    await var1.set_writable()
    await var2.set_writable()
    await var3.set_writable()
    
    # Start server
    async with server:
        yield server, idx, (var1, var2, var3), endpoint

@pytest_asyncio.fixture
async def test_engine(tmp_path):
    """Creates a fresh IndustrialSilexEngine."""
    db_path = tmp_path / "test_opcua.db"
    engine = IndustrialSilexEngine(db_path=str(db_path))
    await engine.initialize()
    yield engine
    await engine.close()

# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_opcua_connector_discovers_nodes(local_opcua_server, test_engine):
    """M5 Goal: Connector crawls the OPC-UA tree and registers nodes."""
    server, idx, _, endpoint = local_opcua_server
    connector = OPCUAConnector(test_engine)
    
    await connector.connect(endpoint)
    try:
        discovered = await connector.discover_nodes()
        
        # We should find at least the 3 custom variables we added
        assert "xmeas_1" in discovered
        assert "xmv_10" in discovered
        assert "xmeas_35" in discovered
        
        # And they should be registered in the engine
        assert test_engine.get_node_id("xmeas_1") is not None
        assert test_engine.get_node_id("xmv_10") is not None
    finally:
        await connector.disconnect()

@pytest.mark.asyncio
async def test_opcua_stream_calls_observe(local_opcua_server, test_engine):
    """M5 Goal: Data streams from OPC-UA to the engine's time buffer."""
    server, idx, (var1, var2, var3), endpoint = local_opcua_server
    connector = OPCUAConnector(test_engine)
    
    await connector.connect(endpoint)
    try:
        await connector.discover_nodes()
        
        # Start streaming in the background
        stream_task = asyncio.create_task(connector.stream(interval=0.1))
        
        # Change a value on the server
        await var1.write_value(42.0)
        
        # Wait a moment for the stream to pick it up
        await asyncio.sleep(0.3)
        
        # Check if the engine's time buffer got the new value
        graph_id = test_engine.get_node_id("xmeas_1")
        latest_val = test_engine._time_buffer.get_latest(graph_id)
        
        assert latest_val == 42.0, f"Engine should have received the streamed value, got {latest_val}"
        
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass
    finally:
        await connector.disconnect()

@pytest.mark.asyncio
async def test_full_stack_opcua_to_graph(local_opcua_server, test_engine):
    """M5 Goal: Data flows end-to-end and creates causal edges."""
    server, idx, (var1, var2, var3), endpoint = local_opcua_server
    
    # We lower thresholds so 2 observations make an edge
    test_engine._learner.min_occurrences = 1
    test_engine._learner.min_confidence = 0.1
    
    connector = OPCUAConnector(test_engine)
    await connector.connect(endpoint)
    
    try:
        await connector.discover_nodes()
        stream_task = asyncio.create_task(connector.stream(interval=0.1))
        
        # We need to simulate a sequential change to trigger the CausalLearner
        # Step 1: V2 changes
        await var2.write_value(10.0)
        await asyncio.sleep(0.2)
        
        # Step 2: V3 changes
        await var3.write_value(20.0)
        await asyncio.sleep(0.2)
        
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass
        
        # Check if graph has edges
        stats = await test_engine.get_graph_stats()
        assert stats.causes_with_lag_edges >= 1, f"Should have learned at least one edge, got {stats}"
    finally:
        await connector.disconnect()
