from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request


router = APIRouter(tags=["system"])


@router.get("/api/health")
def health(request: Request) -> dict:
    runtime = request.app.state.simulation_runtime
    return {
        "status": "ok",
        "map": runtime.map_metadata,
        "versions": runtime.state_versions,
        "paused": request.app.state.is_paused,
        "speed": request.app.state.simulation_speed,
    }


@router.get("/api/runtime/snapshot")
async def runtime_snapshot(request: Request) -> dict:
    async with request.app.state.simulation_lock:
        return await asyncio.to_thread(request.app.state.simulation_runtime.full_sync)
