from __future__ import annotations

import math
from typing import Optional

import numpy as np

from app.models import BlackBoxEntity, CandidateBox, EditorProject
from .skeleton_extractor import SkeletonExtractor


_REASON_PRIORITY = {
    "MANUAL": 0,
    "EXIT": 1,
    "REFUGE": 1,
    "STAIR": 1,
    "JUNCTION": 2,
    "CORNER": 3,
    "DEAD_END": 4,
    "LONG_CORRIDOR": 5,
}


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _line_points(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[int, int]]:
    x0, y0 = int(round(a[0])), int(round(a[1]))
    x1, y1 = int(round(b[0])), int(round(b[1]))
    points: list[tuple[int, int]] = []
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        points.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy
    return points


def line_is_walkable(
    walkable_yx: np.ndarray,
    a: tuple[float, float],
    b: tuple[float, float],
) -> bool:
    height, width = walkable_yx.shape
    previous: Optional[tuple[int, int]] = None
    for x, y in _line_points(a, b):
        if not (0 <= x < width and 0 <= y < height) or not walkable_yx[y, x]:
            return False
        if previous is not None:
            px, py = previous
            if px != x and py != y:
                # A diagonal step may not pass between two orthogonal walls.
                if not walkable_yx[py, x] or not walkable_yx[y, px]:
                    return False
        previous = (x, y)
    return True


class PlacementService:
    def __init__(self, skeleton_extractor: SkeletonExtractor):
        self.skeleton_extractor = skeleton_extractor

    @staticmethod
    def _candidate(
        x: float,
        y: float,
        reason: str,
        index: int,
        *,
        mandatory: bool = False,
        selected: bool = False,
        source_entity_id: Optional[str] = None,
    ) -> CandidateBox:
        return CandidateBox(
            id=f"CAND-{index:04d}",
            x=round(float(x), 3),
            y=round(float(y), 3),
            reason=reason,
            mandatory=mandatory,
            selected=selected,
            source_entity_id=source_entity_id,
        )

    def generate_candidates(
        self,
        project: EditorProject,
        walkable_yx: np.ndarray,
        skeleton_yx: np.ndarray,
    ) -> list[CandidateBox]:
        merge_distance = project.settings.candidate_merge_distance
        raw: list[tuple[float, float, str, bool, bool, Optional[str]]] = []

        for box in project.entities.black_boxes:
            raw.append((box.x, box.y, "MANUAL", box.mandatory, True, box.id))
        for reason, entities in (
            ("EXIT", project.entities.exits),
            ("REFUGE", project.entities.refuges),
            ("STAIR", project.entities.stairs),
        ):
            for entity in entities:
                raw.append((entity.x, entity.y, reason, True, True, entity.id))

        for x, y, reason in self.skeleton_extractor.classify_features(skeleton_yx):
            raw.append((x, y, reason, reason in {"JUNCTION", "CORNER"}, False, None))

        raw.sort(
            key=lambda item: (
                _REASON_PRIORITY.get(item[2], 99),
                round(item[1], 6),
                round(item[0], 6),
            )
        )

        selected_raw: list[tuple[float, float, str, bool, bool, Optional[str]]] = []
        for item in raw:
            x, y, reason, mandatory, selected, entity_id = item
            nearby_index = next(
                (
                    idx
                    for idx, existing in enumerate(selected_raw)
                    if _distance((x, y), (existing[0], existing[1])) < merge_distance
                ),
                None,
            )
            if nearby_index is None:
                selected_raw.append(item)
                continue
            existing = selected_raw[nearby_index]
            if _REASON_PRIORITY.get(reason, 99) < _REASON_PRIORITY.get(existing[2], 99):
                selected_raw[nearby_index] = item
            elif mandatory and not existing[3]:
                selected_raw[nearby_index] = (
                    existing[0],
                    existing[1],
                    existing[2],
                    True,
                    existing[4] or selected,
                    existing[5] or entity_id,
                )

        # Fill long corridor gaps.  Euclidean proximity only suppresses a point
        # when a direct walkable line exists, so parallel corridors across a wall
        # remain independently covered.
        max_box_distance_px = (
            project.settings.max_box_distance / project.map.meters_per_pixel
        )
        interval = max(4.0, max_box_distance_px * 0.75)
        skeleton_points = [(int(x), int(y)) for y, x in np.argwhere(skeleton_yx)]
        scan_step = max(1, int(interval // 3))
        for x, y in skeleton_points[::scan_step]:
            has_nearby_visible = any(
                _distance((x, y), (existing[0], existing[1])) <= interval
                and line_is_walkable(walkable_yx, (x, y), (existing[0], existing[1]))
                for existing in selected_raw
            )
            if not has_nearby_visible:
                selected_raw.append((x, y, "LONG_CORRIDOR", False, False, None))

        return [
            self._candidate(
                x,
                y,
                reason,
                idx + 1,
                mandatory=mandatory,
                selected=selected,
                source_entity_id=entity_id,
            )
            for idx, (x, y, reason, mandatory, selected, entity_id) in enumerate(selected_raw)
        ]

    def optimize(
        self,
        project: EditorProject,
        walkable_yx: np.ndarray,
        skeleton_yx: np.ndarray,
        candidates: list[CandidateBox],
    ) -> list[CandidateBox]:
        points = [(int(x), int(y)) for y, x in np.argwhere(skeleton_yx)]
        sample_step = project.settings.coverage_sample_step
        samples = points[::sample_step]
        radius = project.settings.coverage_radius / project.map.meters_per_pixel

        coverage: dict[str, set[int]] = {}
        for candidate in candidates:
            covered: set[int] = set()
            origin = (candidate.x, candidate.y)
            for index, sample in enumerate(samples):
                if _distance(origin, sample) > radius:
                    continue
                if line_is_walkable(walkable_yx, origin, sample):
                    covered.add(index)
            coverage[candidate.id] = covered

        selected_ids = {
            candidate.id for candidate in candidates if candidate.mandatory or candidate.selected
        }
        covered = set().union(*(coverage[cid] for cid in selected_ids)) if selected_ids else set()
        uncovered = set(range(len(samples))) - covered
        remaining = [candidate for candidate in candidates if candidate.id not in selected_ids]

        while uncovered and remaining:
            best = max(
                remaining,
                key=lambda candidate: (
                    len(coverage[candidate.id] & uncovered),
                    -_REASON_PRIORITY.get(candidate.reason, 99),
                    candidate.id,
                ),
            )
            gain = coverage[best.id] & uncovered
            if not gain:
                break
            selected_ids.add(best.id)
            uncovered -= gain
            remaining.remove(best)

        return [
            candidate.model_copy(update={"selected": candidate.id in selected_ids})
            for candidate in candidates
        ]

    @staticmethod
    def selected_as_black_boxes(candidates: list[CandidateBox]) -> list[BlackBoxEntity]:
        selected = [candidate for candidate in candidates if candidate.selected]
        return [
            BlackBoxEntity(
                id=f"AUTO-{index:03d}",
                x=candidate.x,
                y=candidate.y,
                label=f"A{index:03d}",
                locked=candidate.mandatory,
                mandatory=candidate.mandatory,
                source="automatic",
            )
            for index, candidate in enumerate(selected, start=1)
        ]
