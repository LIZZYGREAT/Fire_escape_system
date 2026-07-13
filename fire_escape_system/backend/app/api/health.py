from __future__ import annotations

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
    }

