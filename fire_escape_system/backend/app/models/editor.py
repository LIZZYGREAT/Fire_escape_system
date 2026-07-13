from __future__ import annotations

from typing import Any, Dict, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class APIModel(BaseModel):
    """Base model accepting both Python snake_case and editor camelCase."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="allow",
    )


class Point(APIModel):
    x: float
    y: float


class Stroke(APIModel):
    id: str = Field(default_factory=lambda: f"stroke-{uuid4().hex[:10]}")
    points: list[Point] = Field(default_factory=list)
    size: float = Field(default=6.0, gt=0)
    closed: bool = False
    locked: bool = False


class StrokeLayers(APIModel):
    walkable: list[Stroke] = Field(default_factory=list)
    walls: list[Stroke] = Field(default_factory=list)


class AnnotationCollection(APIModel):
    strokes: StrokeLayers = Field(default_factory=StrokeLayers)
    wall_pixels: list[tuple[int, int]] = Field(default_factory=list)
    fire_domains: list[Stroke] = Field(default_factory=list)


class MapDescriptor(APIModel):
    id: str = "untitled_map"
    name: str = "Untitled map"
    version: str = "0.1.0"
    image_data_url: Optional[str] = None
    width: int = Field(default=250, ge=1, le=4096)
    height: int = Field(default=250, ge=1, le=4096)
    meters_per_pixel: float = Field(default=0.1, gt=0)
    source_mask_path: Optional[str] = None
    coordinate_origin: Literal["top_left"] = "top_left"
    x_axis: Literal["east"] = "east"
    y_axis: Literal["south"] = "south"


class MapEntity(APIModel):
    id: str
    x: float
    y: float
    label: str = ""
    locked: bool = False


class DoorEntity(MapEntity):
    door_type: Literal["normal", "fire"] = "normal"
    state: Literal["open", "closed"] = "open"


class BlackBoxEntity(MapEntity):
    mandatory: bool = False
    source: Literal["manual", "automatic", "imported"] = "manual"


class EntityCollection(APIModel):
    doors: list[DoorEntity] = Field(default_factory=list)
    exits: list[MapEntity] = Field(default_factory=list)
    refuges: list[MapEntity] = Field(default_factory=list)
    stairs: list[MapEntity] = Field(default_factory=list)
    gateways: list[MapEntity] = Field(default_factory=list)
    black_boxes: list[BlackBoxEntity] = Field(default_factory=list)


class CandidateBox(APIModel):
    id: str
    x: float
    y: float
    reason: str = "LONG_CORRIDOR"
    mandatory: bool = False
    selected: bool = False
    source_entity_id: Optional[str] = None


class ValidationIssue(APIModel):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    geometry: Optional[Dict[str, Any]] = None
    suggestion: Optional[str] = None


class PlacementReport(APIModel):
    box_count: int = 0
    mandatory_count: int = 0
    coverage_ratio: float = 0.0
    max_blind_distance_m: Optional[float] = None
    decision_lead_distance_m: Optional[float] = None
    chain_break_count: int = 0
    ambiguous_direction_count: int = 0
    n_minus_1_pass_rate: float = 0.0
    sensor_overlap_ratio: float = 0.0
    estimated_cost: float = 0.0
    critical_single_points: list[str] = Field(default_factory=list)


class ValidationResult(APIModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    report: PlacementReport = Field(default_factory=PlacementReport)


class ProjectDerived(APIModel):
    centerline: Any = Field(default_factory=list)
    candidates: list[CandidateBox] = Field(default_factory=list)
    topology: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    masks: dict[str, Any] = Field(default_factory=dict)


class EditorSettings(APIModel):
    brush_size: float = Field(default=6.0, gt=0)
    coverage_radius: float = Field(default=5.0, gt=0)
    visible_radius: float = Field(default=8.0, gt=0)
    max_box_distance: float = Field(default=8.0, gt=0)
    snap_distance: float = Field(default=8.0, ge=0)
    snap_to_centerline: bool = True
    snap_to_grid: bool = False
    grid_size: float = Field(default=5.0, gt=0)
    coverage_sample_step: int = Field(default=4, ge=1, le=100)
    candidate_merge_distance: float = Field(default=6.0, ge=1)
    estimated_box_cost: float = Field(default=1.0, ge=0)


class InitialFire(APIModel):
    x: float
    y: float
    intensity: float = Field(default=100.0, ge=0)


class SimulationSettings(APIModel):
    initial_fires: list[InitialFire] = Field(default_factory=list)
    ignition_tick: int = Field(default=10, ge=0)
    tick_interval_seconds: float = Field(default=1.0, gt=0)


class EditorProject(APIModel):
    schema_version: str = "1.0.0"
    revision: int = Field(default=0, ge=0)
    map: MapDescriptor = Field(default_factory=MapDescriptor)
    annotations: AnnotationCollection = Field(default_factory=AnnotationCollection)
    entities: EntityCollection = Field(default_factory=EntityCollection)
    derived: ProjectDerived = Field(default_factory=ProjectDerived)
    settings: EditorSettings = Field(default_factory=EditorSettings)
    simulation: SimulationSettings = Field(default_factory=SimulationSettings)

    @field_validator("schema_version")
    @classmethod
    def _schema_version_must_exist(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("schemaVersion cannot be empty")
        return value
