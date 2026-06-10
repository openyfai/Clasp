# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/ingest/opcua_connector.py
==========================================
Connects to an OPC-UA server, discovers nodes, and streams telemetry to the Engine.
"""

import asyncio
import logging
from typing import Any

from asyncua import Client, Node
from asyncua.ua import DataValue

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.schemas import IndustrialNodeType

log = logging.getLogger("clasp.ingest.opcua")


class SubHandler:
    """Handles DataChange notifications from the OPC-UA subscription."""
    def __init__(self, engine, node_map_reversed):
        self.engine = engine
        self.node_map_reversed = node_map_reversed

    async def datachange_notification(self, node: Node, val: Any, data: Any):
        node_id_str = node.nodeid.to_string()
        node_key = self.node_map_reversed.get(node_id_str)
        log.info(f"Datachange: {node_id_str} ({node_key}) = {val}")
        if node_key and isinstance(val, (int, float)):
            dv = getattr(data.monitored_item, "Value", None) if hasattr(data, "monitored_item") else None
            ts = dv.SourceTimestamp.timestamp() if dv and hasattr(dv, "SourceTimestamp") and dv.SourceTimestamp else None
            await self.engine.observe(node_key=node_key, value=float(val), timestamp=ts)


class OPCUAConnector:
    """Streams data from an OPC-UA server to the Silex Engine."""

    def __init__(self, engine: IndustrialSilexEngine):
        self.engine = engine
        self.client: Client | None = None
        self._streaming_task: asyncio.Task | None = None
        self._node_map: dict[str, Node] = {} # node_key -> OPC UA Node
        self._target_url: str | None = None

    async def connect(self, url: str):
        """Connect to the OPC-UA server."""
        log.info(f"Connecting to OPC-UA server at {url}...")
        self._target_url = url
        self.client = Client(url=url)
        await self.client.connect()
        log.info(f"Connected to {url}")

    async def disconnect(self):
        """Disconnect from the OPC-UA server."""
        if self._streaming_task:
            self._streaming_task.cancel()
        if self.client:
            await self.client.disconnect()
            log.info("Disconnected from OPC-UA server.")

    async def discover_nodes(self, namespace_idx: int = 2) -> list[str]:
        """
        Walks the OPC-UA address space to find variables and registers them with the engine.
        Returns the list of discovered node keys.
        """
        if not self.client:
            raise RuntimeError("Must connect before discovering nodes.")

        log.info("Discovering OPC-UA nodes...")
        objects = self.client.nodes.objects
        
        # In a real plant, you'd traverse the folder structure.
        # For simplicity, we assume variables are direct children of Objects,
        # or we just recursively search for Variables.
        
        from asyncua.ua import NodeClass
        
        discovered_keys = []
        
        async def _browse_recursive(node: Node):
            try:
                node_class = await node.read_node_class()
                if node_class == NodeClass.Variable:
                    # Register this node
                    node_id_obj = node.nodeid
                    if namespace_idx is not None and node_id_obj.NamespaceIndex != namespace_idx:
                        return
                    
                    name = (await node.read_browse_name()).Name
                    node_id = node_id_obj.to_string()
                    
                    # Heuristics for type
                    name_lower = name.lower()
                    if "quality" in name_lower or "product" in name_lower or name_lower == "xmeas_35":
                        node_type = IndustrialNodeType.QUALITY_METRIC
                    elif "xmv" in name_lower or "valve" in name_lower or "setpoint" in name_lower:
                        node_type = IndustrialNodeType.OPERATOR_ACTION
                    else:
                        node_type = IndustrialNodeType.PROCESS_VARIABLE
                        
                    # We use the name as the node_key
                    node_key = name.lower()
                    
                    if "server" not in node_key:
                        await self.engine.register_node(
                            node_id=node_key,
                            node_type=node_type,
                            label=name
                        )
                        self._node_map[node_key] = node
                        discovered_keys.append(node_key)
                        
                else:
                    children = await node.get_children()
                    for child in children:
                        await _browse_recursive(child)
            except Exception as e:
                log.warning(f"Error browsing {node}: {e}")

        await _browse_recursive(objects)
        log.info(f"Discovered {len(discovered_keys)} industrial variables.")
        return discovered_keys

    async def stream(self, interval: float = 1.0):
        """Continuously reads node values using push subscriptions and auto-reconnects."""
        if not self._target_url:
            raise RuntimeError("Must call connect() before streaming.")
            
        log.info(f"Starting OPC-UA telemetry stream (interval={interval}s)...")
        backoff = 2.0
        max_backoff = 30.0

        try:
            while True:
                try:
                    if not self.client:
                        log.info(f"Attempting to reconnect to {self._target_url}...")
                        self.client = Client(url=self._target_url)
                        await self.client.connect()
                        log.info("Connection established.")
                        self._node_map.clear()
                        
                    if not self._node_map:
                        await self.discover_nodes()

                    # Set up subscription
                    node_map_reversed = {node.nodeid.to_string(): key for key, node in self._node_map.items()}
                    handler = SubHandler(self.engine, node_map_reversed)
                    sub = await self.client.create_subscription(interval * 1000, handler)
                    nodes = list(self._node_map.values())
                    if nodes:
                        for n in nodes:
                            await sub.subscribe_data_change(n)
                    log.info("Successfully subscribed to OPC-UA nodes.")
                    
                    # Reset backoff on success
                    backoff = 2.0
                    
                    # Keep-alive loop
                    while True:
                        await asyncio.sleep(5)
                        # Reading server time acts as a heartbeat to detect disconnects
                        await self.client.nodes.server_time.read_value()

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error(f"OPC-UA connection error: {e}. Reconnecting in {backoff}s...")
                    self.client = None
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)

        except asyncio.CancelledError:
            log.info("Streaming task cancelled.")
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
