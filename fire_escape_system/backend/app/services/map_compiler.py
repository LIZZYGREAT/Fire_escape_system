from __future__ import annotations

import hashlib
import heapq
import io
import json
import math
import zipfile
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from scipy.ndimage import distance_transform_edt

from app.models import (
    BlackBoxEntity,
    CandidateBox,
    EditorProject,
    PlacementReport,
    ProjectDerived,
    ValidationIssue,
    ValidationResult,
)
from .map_rasterizer import MapRasterizer, RasterizationError
from .map_repository import MapRepository
from .placement import PlacementService
from .skeleton_extractor import SkeletonExtractor
from .topology_builder import TopologyBuilder, TopologyResult


@dataclass
class CompiledProject:
    project: EditorProject
    masks: dict[str, np.ndarray]
    skeleton: np.ndarray
    skeleton_points: list[dict[str, int]]
    candidates: list[CandidateBox]
    boxes: list[BlackBoxEntity]
    topology: TopologyResult
    validation: ValidationResult
    topology_version: str


class InvalidMapError(ValueError):
    def __init__(self, validation: ValidationResult):
        super().__init__("map validation failed; export is not allowed")
        self.validation = validation


class MapCompiler:
    """Deterministic map editor compiler and validation facade."""

    def __init__(self, repository: MapRepository):
        self.repository = repository
        self.rasterizer = MapRasterizer(repository)
        self.skeleton_extractor = SkeletonExtractor()
        self.placement = PlacementService(self.skeleton_extractor)
        self.topology_builder = TopologyBuilder(self.skeleton_extractor)

    @staticmethod
    def _issue(
        code: str,
        severity: str,
        message: str,
        *,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        x: Optional[float] = None,
        y: Optional[float] = None,
        suggestion: Optional[str] = None,
    ) -> ValidationIssue:
        geometry = None if x is None or y is None else {"x": x, "y": y}
        return ValidationIssue(
            code=code,
            severity=severity,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            geometry=geometry,
            suggestion=suggestion,
        )

    @staticmethod
    def _all_entities(project: EditorProject) -> list[tuple[str, Any]]:
        return [
            *[("door", value) for value in project.entities.doors],
            *[("exit", value) for value in project.entities.exits],
            *[("refuge", value) for value in project.entities.refuges],
            *[("stair", value) for value in project.entities.stairs],
            *[("elevator", value) for value in project.entities.elevators],
            *[("fire_hydrant", value) for value in project.entities.fire_hydrants],
            *[("extinguisher", value) for value in project.entities.extinguishers],
            *[("gateway", value) for value in project.entities.gateways],
            *[("black_box", value) for value in project.entities.black_boxes],
        ]

    @staticmethod
    def _dijkstra_cutoff(
        adjacency: dict[int, list[tuple[int, float]]],
        start: int,
        cutoff: Optional[float],
    ) -> dict[int, float]:
        distances = {start: 0.0}
        queue = [(0.0, start)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances.get(node):
                continue
            for neighbor, edge_cost in adjacency.get(node, []):
                new_distance = distance + edge_cost
                if cutoff is not None and new_distance > cutoff:
                    continue
                if new_distance + 1e-9 < distances.get(neighbor, math.inf):
                    distances[neighbor] = new_distance
                    heapq.heappush(queue, (new_distance, neighbor))
        return distances

    def _placement_report(
        self,
        project: EditorProject,
        boxes: list[BlackBoxEntity],
        topology: TopologyResult,
    ) -> PlacementReport:
        node_count = len(topology.skeleton_coordinates)
        if not node_count:
            return PlacementReport(
                box_count=len(boxes),
                mandatory_count=sum(box.mandatory for box in boxes),
                chain_break_count=len(boxes),
                n_minus_1_pass_rate=0.0,
                estimated_cost=len(boxes) * project.settings.estimated_box_cost,
            )

        coverage_count = np.zeros(node_count, dtype=np.int32)
        minimum_distance = np.full(node_count, np.inf, dtype=np.float64)
        coverage_radius_px = (
            project.settings.coverage_radius / project.map.meters_per_pixel
        )
        for box in boxes:
            start = topology.snapped_indices.get(box.id)
            if start is None:
                continue
            distances = self._dijkstra_cutoff(
                topology.skeleton_adjacency,
                start,
                coverage_radius_px,
            )
            for index, distance in distances.items():
                coverage_count[index] += 1
                minimum_distance[index] = min(minimum_distance[index], distance)

        covered = int(np.count_nonzero(coverage_count >= 1))
        overlapping = int(np.count_nonzero(coverage_count >= 2))
        coverage_ratio = covered / node_count
        overlap_ratio = overlapping / node_count

        max_blind_distance_m: Optional[float] = 0.0
        if covered < node_count:
            # Obtain the actual nearest-box distance for the uncovered tail.
            for box in boxes:
                start = topology.snapped_indices.get(box.id)
                if start is None:
                    continue
                distances = self._dijkstra_cutoff(topology.skeleton_adjacency, start, None)
                for index, distance in distances.items():
                    minimum_distance[index] = min(minimum_distance[index], distance)
            if np.isinf(minimum_distance).any():
                max_blind_distance_m = None
            else:
                excess = np.maximum(0.0, minimum_distance - coverage_radius_px)
                max_blind_distance_m = round(
                    float(excess.max() * project.map.meters_per_pixel),
                    3,
                )

        n_minus_one_rate, critical = self.topology_builder.n_minus_one(
            topology,
            boxes,
            project,
        )
        return PlacementReport(
            box_count=len(boxes),
            mandatory_count=sum(box.mandatory for box in boxes),
            coverage_ratio=round(coverage_ratio, 6),
            max_blind_distance_m=max_blind_distance_m,
            decision_lead_distance_m=round(project.settings.visible_radius, 3),
            chain_break_count=len(topology.unreachable_boxes),
            ambiguous_direction_count=topology.ambiguous_direction_count,
            n_minus_1_pass_rate=round(n_minus_one_rate, 6),
            sensor_overlap_ratio=round(overlap_ratio, 6),
            estimated_cost=round(
                len(boxes) * project.settings.estimated_box_cost,
                2,
            ),
            critical_single_points=critical,
        )

    def _validate(
        self,
        project: EditorProject,
        masks: dict[str, np.ndarray],
        skeleton: np.ndarray,
        boxes: list[BlackBoxEntity],
        topology: TopologyResult,
    ) -> ValidationResult:
        walkable = masks["walkable"]
        height, width = walkable.shape
        issues: list[ValidationIssue] = []
        if not np.any(walkable):
            issues.append(
                self._issue(
                    "MAP_E001",
                    "error",
                    "地图没有任何可通行网格",
                    suggestion="绘制可通行区域或导入有效掩码",
                )
            )
        if not np.any(skeleton):
            issues.append(
                self._issue(
                    "MAP_E002",
                    "error",
                    "无法从可通行区域提取中心线",
                    suggestion="检查通行区标注是否连续",
                )
            )
        if not project.entities.exits:
            issues.append(
                self._issue(
                    "MAP_E003",
                    "error",
                    "地图至少需要一个安全出口",
                    entity_type="exit",
                )
            )

        seen_ids: set[str] = set()
        for entity_type, entity in self._all_entities(project):
            if entity.id in seen_ids:
                issues.append(
                    self._issue(
                        "MAP_E004",
                        "error",
                        f"实体 ID 重复：{entity.id}",
                        entity_type=entity_type,
                        entity_id=entity.id,
                        x=entity.x,
                        y=entity.y,
                    )
                )
            seen_ids.add(entity.id)
            x, y = int(round(entity.x)), int(round(entity.y))
            if not (0 <= x < width and 0 <= y < height):
                issues.append(
                    self._issue(
                        "MAP_E005",
                        "error",
                        f"{entity_type} 超出地图边界",
                        entity_type=entity_type,
                        entity_id=entity.id,
                        x=entity.x,
                        y=entity.y,
                        suggestion="将实体移动到地图内部",
                    )
                )
            elif entity_type != "gateway" and not walkable[y, x]:
                issues.append(
                    self._issue(
                        "MAP_E006",
                        "error",
                        f"{entity_type} 位于墙体或不可通行区域",
                        entity_type=entity_type,
                        entity_id=entity.id,
                        x=entity.x,
                        y=entity.y,
                        suggestion="移动实体或修正通行掩码",
                    )
                )
            if entity_type in {"stair", "elevator", "fire_hydrant", "extinguisher"}:
                half_width = max(0.5, float(entity.width) / 2)
                half_height = max(0.5, float(entity.height) / 2)
                left = int(math.floor(entity.x - half_width))
                right = int(math.ceil(entity.x + half_width))
                top = int(math.floor(entity.y - half_height))
                bottom = int(math.ceil(entity.y + half_height))
                footprint_valid = (
                    left >= 0 and top >= 0 and right < width and bottom < height
                    and bool(np.all(walkable[top:bottom + 1, left:right + 1]))
                )
                if not footprint_valid:
                    issues.append(
                        self._issue(
                            "MAP_E008",
                            "error",
                            f"{entity_type} 的设施范围覆盖墙体、不可通行区域或地图边界",
                            entity_type=entity_type,
                            entity_id=entity.id,
                            x=entity.x,
                            y=entity.y,
                            suggestion="缩小设施尺寸或移动到完整可放置区域",
                        )
                    )

        facilities = [
            (kind, entity) for kind, entity in self._all_entities(project)
            if kind in {"stair", "elevator", "fire_hydrant", "extinguisher"}
        ]
        for index, (kind, entity) in enumerate(facilities):
            for other_kind, other in facilities[index + 1:]:
                separated = (
                    entity.x + entity.width / 2 <= other.x - other.width / 2
                    or entity.x - entity.width / 2 >= other.x + other.width / 2
                    or entity.y + entity.height / 2 <= other.y - other.height / 2
                    or entity.y - entity.height / 2 >= other.y + other.height / 2
                )
                if not separated:
                    issues.append(
                        self._issue(
                            "MAP_E009",
                            "error",
                            f"设施 {entity.id} 与 {other.id} 范围重叠",
                            entity_type=kind,
                            entity_id=entity.id,
                            x=entity.x,
                            y=entity.y,
                            suggestion="调整设施位置或尺寸，避免相互覆盖",
                        )
                    )

        node_by_id = {node["id"]: node for node in topology.topology.get("nodes", [])}
        for box in boxes:
            node = node_by_id.get(box.id)
            if node and node["snapDistance"] > project.settings.snap_distance:
                issues.append(
                    self._issue(
                        "MAP_W001",
                        "warning",
                        f"黑盒距离中心线 {node['snapDistance']:.2f}px，超过吸附阈值",
                        entity_type="black_box",
                        entity_id=box.id,
                        x=box.x,
                        y=box.y,
                        suggestion="启用中心线吸附或人工复核安装位置",
                    )
                )

        for box_id in topology.unreachable_boxes:
            box = next((value for value in boxes if value.id == box_id), None)
            issues.append(
                self._issue(
                    "MAP_E007",
                    "error",
                    f"黑盒 {box_id} 无法通过静态指示链到达出口或避难点",
                    entity_type="black_box",
                    entity_id=box_id,
                    x=box.x if box else None,
                    y=box.y if box else None,
                    suggestion="补充中间黑盒、增大合法间距或修复断裂通行区",
                )
            )

        clearance = distance_transform_edt(walkable)
        for box in boxes:
            x, y = int(round(box.x)), int(round(box.y))
            if 0 <= x < width and 0 <= y < height and clearance[y, x] < 1.0:
                issues.append(
                    self._issue(
                        "MAP_W002",
                        "warning",
                        f"黑盒 {box.id} 安装净空不足",
                        entity_type="black_box",
                        entity_id=box.id,
                        x=box.x,
                        y=box.y,
                    )
                )

        report = self._placement_report(project, boxes, topology)
        if report.coverage_ratio < 0.999:
            issues.append(
                self._issue(
                    "PLACEMENT_W001",
                    "warning",
                    f"中心线覆盖率为 {report.coverage_ratio:.1%}",
                    suggestion="运行自动布点优化或补充人工黑盒",
                )
            )
        if report.n_minus_1_pass_rate < 1.0:
            issues.append(
                self._issue(
                    "PLACEMENT_W002",
                    "warning",
                    "存在黑盒单点失效会导致静态导航断链",
                    suggestion="在关键咽喉增加冗余指示节点",
                )
            )

        return ValidationResult(
            valid=not any(issue.severity == "error" for issue in issues),
            issues=issues,
            summary={
                "walkableCells": int(np.count_nonzero(walkable)),
                "wallCells": int(np.count_nonzero(masks["wall"])),
                "skeletonCells": int(np.count_nonzero(skeleton)),
                "boxCount": len(boxes),
                "exitCount": len(project.entities.exits),
                "errorCount": sum(issue.severity == "error" for issue in issues),
                "warningCount": sum(issue.severity == "warning" for issue in issues),
            },
            report=report,
        )

    @staticmethod
    def _version(
        project: EditorProject,
        masks: dict[str, np.ndarray],
        topology: dict[str, Any],
    ) -> str:
        digest = hashlib.sha256()
        digest.update(
            json.dumps(
                project.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude={"derived"},
                ),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        for name in ("walkable", "wall", "fire_domain"):
            digest.update(name.encode("ascii"))
            digest.update(np.ascontiguousarray(masks[name]).tobytes())
        digest.update(
            json.dumps(topology, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        return digest.hexdigest()[:16]

    def compile_internal(self, project: EditorProject) -> CompiledProject:
        masks = self.rasterizer.rasterize(project)
        skeleton = self.skeleton_extractor.extract(masks["walkable"])
        skeleton_points = self.skeleton_extractor.points(skeleton)
        candidates = self.placement.generate_candidates(
            project,
            masks["walkable"],
            skeleton,
        )
        boxes = list(project.entities.black_boxes)
        if not boxes:
            candidates = self.placement.optimize(
                project,
                masks["walkable"],
                skeleton,
                candidates,
            )
            boxes = self.placement.selected_as_black_boxes(candidates)
        topology = self.topology_builder.build(
            project,
            masks["walkable"],
            skeleton,
            boxes,
        )
        validation = self._validate(project, masks, skeleton, boxes, topology)
        topology_version = self._version(project, masks, topology.topology)
        return CompiledProject(
            project=project,
            masks=masks,
            skeleton=skeleton,
            skeleton_points=skeleton_points,
            candidates=candidates,
            boxes=boxes,
            topology=topology,
            validation=validation,
            topology_version=topology_version,
        )

    @staticmethod
    def _mask_summary(compiled: CompiledProject) -> dict[str, Any]:
        wall_yx = np.argwhere(compiled.masks["wall"] > 0)
        return {
            "width": compiled.project.map.width,
            "height": compiled.project.map.height,
            "walkableCount": int(np.count_nonzero(compiled.masks["walkable"])),
            "wallCount": int(len(wall_yx)),
            "fireDomainCount": int(np.count_nonzero(compiled.masks["fire_domain"])),
            "wallPixels": [[int(x), int(y)] for y, x in wall_yx],
        }

    def response(self, compiled: CompiledProject) -> dict[str, Any]:
        candidates = [
            value.model_dump(mode="json", by_alias=True) for value in compiled.candidates
        ]
        validation = compiled.validation.model_dump(mode="json", by_alias=True)
        masks = self._mask_summary(compiled)
        derived = ProjectDerived(
            centerline={"points": compiled.skeleton_points},
            candidates=compiled.candidates,
            topology=compiled.topology.topology,
            validation=validation,
            masks=masks,
        )
        project = compiled.project.model_copy(update={"derived": derived})
        return {
            "map_id": compiled.project.map.id,
            "map_version": compiled.project.map.version,
            "topology_version": compiled.topology_version,
            "skeleton_points": compiled.skeleton_points,
            "skeleton": compiled.skeleton_points,
            "centerline": {"points": compiled.skeleton_points},
            "candidate_boxes": candidates,
            "candidates": candidates,
            "selected_boxes": [
                value.model_dump(mode="json", by_alias=True) for value in compiled.boxes
            ],
            "topology": compiled.topology.topology,
            "masks": masks,
            "report": validation["report"],
            "validation": validation,
            "valid": validation["valid"],
            "issues": validation["issues"],
            "project": project.model_dump(mode="json", by_alias=True),
        }

    def compile(self, project: EditorProject) -> dict[str, Any]:
        return self.response(self.compile_internal(project))

    def validate(self, project: EditorProject) -> ValidationResult:
        return self.compile_internal(project).validation

    def candidates(self, project: EditorProject) -> dict[str, Any]:
        masks = self.rasterizer.rasterize(project)
        skeleton = self.skeleton_extractor.extract(masks["walkable"])
        candidates = self.placement.generate_candidates(
            project,
            masks["walkable"],
            skeleton,
        )
        data = [value.model_dump(mode="json", by_alias=True) for value in candidates]
        return {
            "candidate_boxes": data,
            "candidates": data,
            "skeleton_points": self.skeleton_extractor.points(skeleton),
        }

    def optimize(self, project: EditorProject) -> dict[str, Any]:
        masks = self.rasterizer.rasterize(project)
        skeleton = self.skeleton_extractor.extract(masks["walkable"])
        candidates = self.placement.generate_candidates(
            project,
            masks["walkable"],
            skeleton,
        )
        optimized = self.placement.optimize(
            project,
            masks["walkable"],
            skeleton,
            candidates,
        )
        boxes = self.placement.selected_as_black_boxes(optimized)
        topology = self.topology_builder.build(
            project,
            masks["walkable"],
            skeleton,
            boxes,
        )
        validation = self._validate(project, masks, skeleton, boxes, topology)
        return {
            "skeleton_points": self.skeleton_extractor.points(skeleton),
            "candidate_boxes": [
                value.model_dump(mode="json", by_alias=True) for value in optimized
            ],
            "candidates": [
                value.model_dump(mode="json", by_alias=True) for value in optimized
            ],
            "black_boxes": [
                value.model_dump(mode="json", by_alias=True) for value in boxes
            ],
            "topology": topology.topology,
            "report": validation.report.model_dump(mode="json", by_alias=True),
            "issues": [
                value.model_dump(mode="json", by_alias=True) for value in validation.issues
            ],
        }

    @staticmethod
    def _json_bytes(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")

    @staticmethod
    def _npy_bytes(array: np.ndarray) -> bytes:
        stream = io.BytesIO()
        np.save(stream, array, allow_pickle=False)
        return stream.getvalue()

    def export_zip(self, project: EditorProject) -> bytes:
        compiled = self.compile_internal(project)
        if not compiled.validation.valid:
            raise InvalidMapError(compiled.validation)
        response = self.response(compiled)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "map_meta.json",
                self._json_bytes(
                    {
                        "schema_version": project.schema_version,
                        "map_id": project.map.id,
                        "map_version": project.map.version,
                        "topology_version": compiled.topology_version,
                        "width": project.map.width,
                        "height": project.map.height,
                        "resolution_m_per_cell": project.map.meters_per_pixel,
                        "coordinate_origin": "top_left",
                        "x_axis": "east",
                        "y_axis": "south",
                    }
                ),
            )
            archive.writestr("M_walkable.npy", self._npy_bytes(compiled.masks["walkable"]))
            archive.writestr("M_wall.npy", self._npy_bytes(compiled.masks["wall"]))
            archive.writestr(
                "M_fire_domain.npy",
                self._npy_bytes(compiled.masks["fire_domain"]),
            )
            clearance_m = (
                distance_transform_edt(compiled.masks["walkable"])
                * project.map.meters_per_pixel
            ).astype(np.float32)
            archive.writestr("M_clearance.npy", self._npy_bytes(clearance_m))
            archive.writestr(
                "M_skeleton.npy",
                self._npy_bytes(compiled.skeleton.astype(np.uint8)),
            )
            archive.writestr(
                "boxes.json",
                self._json_bytes(
                    [value.model_dump(mode="json", by_alias=True) for value in compiled.boxes]
                ),
            )
            for name, values in (
                ("exits.json", project.entities.exits),
                ("refuges.json", project.entities.refuges),
                ("doors.json", project.entities.doors),
                ("stairs.json", project.entities.stairs),
                ("gateways.json", project.entities.gateways),
            ):
                archive.writestr(
                    name,
                    self._json_bytes(
                        [value.model_dump(mode="json", by_alias=True) for value in values]
                    ),
                )
            archive.writestr("topology.json", self._json_bytes(compiled.topology.topology))
            archive.writestr("placement_report.json", self._json_bytes(response["report"]))
            archive.writestr(
                "validation_report.json",
                self._json_bytes(compiled.validation.model_dump(mode="json", by_alias=True)),
            )
            archive.writestr(
                "editor_project.json",
                self._json_bytes(project.model_dump(mode="json", by_alias=True)),
            )
        return stream.getvalue()


__all__ = [
    "CompiledProject",
    "InvalidMapError",
    "MapCompiler",
    "RasterizationError",
]
