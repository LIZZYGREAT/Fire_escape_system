from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


router = APIRouter(tags=["runtime"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    app = websocket.app
    manager = app.state.connection_manager
    runtime = app.state.simulation_runtime
    await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "code": "WS_E001", "message": "invalid JSON"}
                )
                continue
            message_type = payload.get("type")
            if message_type == "request_full_sync":
                async with app.state.simulation_lock:
                    snapshot = await asyncio.to_thread(runtime.full_sync)
                await websocket.send_json(snapshot)
                continue
            if message_type != "control":
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "WS_E002",
                        "message": "unsupported message type",
                    }
                )
                continue

            command = payload.get("command")
            if command == "pause":
                app.state.is_paused = True
            elif command == "resume":
                app.state.is_paused = False
            elif command == "reset":
                app.state.is_paused = True
                async with app.state.simulation_lock:
                    snapshot = await asyncio.to_thread(runtime.reset)
                await manager.broadcast(snapshot)
                app.state.is_paused = False
            elif command == "inject_fire":
                try:
                    runtime.inject_fire(
                        int(payload["x"]),
                        int(payload["y"]),
                        float(payload.get("intensity", 100.0)),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    await websocket.send_json(
                        {"type": "error", "code": "WS_E003", "message": str(exc)}
                    )
            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "WS_E004",
                        "message": "unsupported control command",
                    }
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
        raise

