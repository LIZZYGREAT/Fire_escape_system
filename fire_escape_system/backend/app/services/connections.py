from __future__ import annotations

import json

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        if not self.active_connections:
            return
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        failed: list[WebSocket] = []
        for connection in tuple(self.active_connections):
            try:
                await connection.send_text(payload)
            except Exception:
                failed.append(connection)
        for connection in failed:
            self.disconnect(connection)

