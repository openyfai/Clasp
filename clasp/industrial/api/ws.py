# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/api/ws.py
===========================
WebSocket connection manager for real-time alerting.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("clasp.api.ws")


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages."""
    
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._broadcast_task: asyncio.Task | None = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        log.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            log.info(f"WebSocket client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, message: dict[str, Any]):
        """Queue a message to be broadcasted to all connected clients."""
        await self._queue.put(message)

    async def _broadcast_loop(self):
        """Background loop that processes the broadcast queue."""
        while True:
            try:
                message = await self._queue.get()
                for connection in list(self.active_connections):
                    try:
                        await connection.send_json(message)
                    except Exception as e:
                        log.warning(f"Failed to send to WebSocket client: {e}")
                        self.disconnect(connection)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in broadcast loop: {e}")
                
    def start_background_task(self):
        if self._broadcast_task is None:
            self._broadcast_task = asyncio.create_task(self._broadcast_loop())
            
    def stop_background_task(self):
        if self._broadcast_task:
            self._broadcast_task.cancel()
            self._broadcast_task = None


manager = ConnectionManager()
