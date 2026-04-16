"""
VIP AI Platform — WebSocket Manager
Manages connected dashboard clients and broadcasts real-time events.
"""

import json
import asyncio
from datetime import datetime
from typing import Any

from fastapi import WebSocket
from services.logger import log


class ConnectionManager:
    """Manages WebSocket connections and broadcasts events to all clients."""

    def __init__(self):
        self._connections: list[WebSocket] = []
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)
        log.info(f"ws: client connected ({len(self._connections)} total)", extra={"action": "ws.connect"})

    def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)
        log.info(f"ws: client disconnected ({len(self._connections)} total)", extra={"action": "ws.disconnect"})

    async def broadcast(self, event_type: str, data: dict[str, Any]):
        """Broadcast an event to all connected clients."""
        if not self._connections:
            return

        message = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        }, default=str)

        disconnected = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

    def broadcast_sync(self, event_type: str, data: dict[str, Any]):
        """Sync wrapper for broadcasting from non-async code (event bus handlers)."""
        if not self._connections:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.broadcast(event_type, data))
            else:
                loop.run_until_complete(self.broadcast(event_type, data))
        except RuntimeError:
            pass

    @property
    def client_count(self) -> int:
        return len(self._connections)


# Singleton instance
ws_manager = ConnectionManager()
