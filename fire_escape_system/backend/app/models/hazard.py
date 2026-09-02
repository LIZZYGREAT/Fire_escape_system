from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field

from .editor import APIModel


class HazardObservation(APIModel):
    """Normalized observation accepted from a future LoRa gateway adapter."""

    sensor_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    source: Literal["lora", "manual", "mqtt", "http", "simulation"] = "lora"
    floor: str = "F01"
    x: Optional[float] = None
    y: Optional[float] = None
    temperature_c: Optional[float] = None
    smoke_ppm: Optional[float] = Field(default=None, ge=0)
    co_ppm: Optional[float] = Field(default=None, ge=0)
    confidence: float = Field(default=1.0, ge=0, le=1)


class HazardObservationBatch(APIModel):
    map_id: str
    observations: list[HazardObservation] = Field(min_length=1, max_length=1000)


__all__ = ["HazardObservation", "HazardObservationBatch"]
