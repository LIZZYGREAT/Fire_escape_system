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
    "IMPORTED": 2,
    "ROOM_EXIT_GUIDE": 3,
    "JUNCTION": 4,
    "CORNER": 5,
    "DEAD_END": 6,
    "LONG_CORRIDOR": 7,
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

    def _single_access_guides(
        self,
        walkable_yx: np.ndarray,
        skeleton_yx: np.ndarray,
        max_branch_length: float,
    ) -> tuple[list[tuple[float, float]], set[tuple[int, int]]]:
        """Return one guide for each short skeleton branch with one entrance.

        A small enclosed room normally appears in the skeleton graph as one or
        more short leaf branches ending at a junction near its doorway.  The
        previous feature-by-feature strategy put boxes at the leaf, its corners
        and the junction.  Here the branch is treated as one deployment region:
        its feature points are suppressed and a single representative guide is
        offered to the optimizer.

        This is deliberately conservative.  Long dead-end corridors are not
        classified as rooms; the normal coverage pass is still responsible for
        placing as many devices as their length requires.
        """

        coordinates, adjacency = self.skeleton_extractor.graph(
            skeleton_yx,
            walkable_yx,
        )
        if not coordinates:
            return [], set()

        degree = {index: len(adjacency.get(index, [])) for index in range(len(coordinates))}
        branches: list[tuple[tuple[float, float], set[tuple[int, int]]]] = []
        visited_edges: set[tuple[int, int]] = set()

        for leaf in sorted(index for index, value in degree.items() if value == 1):
            path = [leaf]
            previous: Optional[int] = None
            current = leaf
            length = 0.0

            while True:
                options = [
                    (neighbor, cost)
                    for neighbor, cost in adjacency.get(current, [])
                    if neighbor != previous
                ]
                if not options:
                    break
                neighbor, cost = options[0]
                edge = tuple(sorted((current, neighbor)))
                if edge in visited_edges:
                    break
                visited_edges.add(edge)
                length += cost
                previous, current = current, neighbor
                path.append(current)
                if degree.get(current, 0) != 2 or length > max_branch_length:
                    break

            # A branch ending at a junction has exactly one way back to the
            # main route.  Leaf-to-leaf lines and long corridors are excluded.
            if degree.get(current, 0) < 3 or length > max_branch_length or len(path) < 3:
                continue

            cumulative = 0.0
            target = length * 0.5
            guide_index = path[0]
            for first, second in zip(path, path[1:]):
                first_point = coordinates[first]
                second_point = coordinates[second]
                cumulative += _distance(first_point, second_point)
                guide_index = second
                if cumulative >= target:
                    break

            suppressed = {coordinates[index] for index in path[:-1]}
            branches.append((coordinates[guide_index], suppressed))

        return [guide for guide, _ in branches], set().union(
            *(suppressed for _, suppressed in branches)
        ) if branches else set()

    def generate_candidates(
        self,
        project: EditorProject,
        walkable_yx: np.ndarray,
        skeleton_yx: np.ndarray,
    ) -> list[CandidateBox]:
        merge_distance = project.settings.candidate_merge_distance
        meters_per_pixel = project.map.meters_per_pixel
        coverage_radius_px = project.settings.coverage_radius / meters_per_pixel
        max_box_distance_px = project.settings.max_box_distance / meters_per_pixel
        # A six-pixel merge threshold was too small for real floor plans and
        # allowed several feature pixels around one junction to become devices.
        # Scale automatic suppression with the configured physical coverage,
        # while retaining the explicit threshold as a lower bound.
        automatic_spacing = max(
            merge_distance,
            min(coverage_radius_px * 0.8, max_box_distance_px * 0.55),
        )
        raw: list[tuple[float, float, str, bool, bool, Optional[str]]] = []

        for box in project.entities.black_boxes:
            # Re-running optimization must replace its previous output instead
            # of pinning every old automatic box as a new manual requirement.
            if box.source == "automatic":
                continue
            # Imported packages often contain a dense set of generated trial
            # points.  Keep their mandatory anchors, but let optimization prune
            # ordinary imported points.  A point explicitly placed in the
            # editor remains pinned as a genuine manual requirement.
            if box.source == "imported":
                raw.append(
                    (box.x, box.y, "IMPORTED", box.mandatory, box.mandatory, box.id)
                )
            else:
                raw.append((box.x, box.y, "MANUAL", box.mandatory, True, box.id))
        for reason, entities in (
            ("EXIT", project.entities.exits),
            ("REFUGE", project.entities.refuges),
            ("STAIR", project.entities.stairs),
        ):
            for entity in entities:
                raw.append((entity.x, entity.y, reason, True, True, entity.id))

        single_access_limit = max(
            12.0,
            min(max_box_distance_px * 1.25, coverage_radius_px * 1.5),
        )
        room_guides, single_access_points = self._single_access_guides(
            walkable_yx,
            skeleton_yx,
            single_access_limit,
        )
        for x, y in room_guides:
            raw.append((x, y, "ROOM_EXIT_GUIDE", False, False, None))

        for x, y, reason in self.skeleton_extractor.classify_features(skeleton_yx):
            if (x, y) in single_access_points:
                continue
            # Corners and junctions are useful candidates, not compulsory
            # installations.  Coverage optimization decides which are needed.
            raw.append((x, y, reason, False, False, None))

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
            threshold = merge_distance if reason == "MANUAL" else automatic_spacing
            nearby_index = next(
                (
                    idx
                    for idx, existing in enumerate(selected_raw)
                    if _distance((x, y), (existing[0], existing[1])) < threshold
                    and line_is_walkable(
                        walkable_yx,
                        (x, y),
                        (existing[0], existing[1]),
                    )
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
