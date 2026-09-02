from __future__ import annotations

from collections import deque
from typing import Protocol

from app.models.hazard import HazardObservation, HazardObservationBatch


class HazardObservationProvider(Protocol):
    """Contract implemented later by LoRa/MQTT/vendor-specific adapters."""

    def read(self) -> list[HazardObservation]: ...


class HazardObservationBuffer:
    """Validation buffer only; observations are not assimilated into forecasts yet."""

    def __init__(self, capacity: int = 5000):
        self._items: deque[tuple[str, HazardObservation]] = deque(maxlen=capacity)

    def ingest(self, batch: HazardObservationBatch) -> int:
        self._items.extend((batch.map_id, item) for item in batch.observations)
        return len(batch.observations)

    @property
    def size(self) -> int:
        return len(self._items)


__all__ = ["HazardObservationBuffer", "HazardObservationProvider"]
