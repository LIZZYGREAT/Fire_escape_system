from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.models.hazard import HazardObservationBatch


router = APIRouter(prefix="/api/hazards", tags=["hazards"])


@router.post("/observations", status_code=status.HTTP_202_ACCEPTED)
def ingest_observations(batch: HazardObservationBatch, request: Request) -> dict:
    """Accept normalized gateway data without changing the default forecast yet."""
    accepted = request.app.state.hazard_observations.ingest(batch)
    return {
        "accepted": accepted,
        "map_id": batch.map_id,
        "assimilation": "buffered_not_enabled",
    }


@router.get("/integration-status")
def integration_status(request: Request) -> dict:
    return {
        "contract": "HazardObservationBatch/1.0",
        "buffered": request.app.state.hazard_observations.size,
        "assimilation": "not_enabled",
        "supported_sources": ["lora", "mqtt", "http", "manual", "simulation"],
    }
