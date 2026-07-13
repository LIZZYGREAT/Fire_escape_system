from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

import numpy as np
from skimage.morphology import skeletonize


_NEIGHBORS_8 = (
    (-1, -1, math.sqrt(2.0)),
    (0, -1, 1.0),
    (1, -1, math.sqrt(2.0)),
    (-1, 0, 1.0),
    (1, 0, 1.0),
    (-1, 1, math.sqrt(2.0)),
    (0, 1, 1.0),
    (1, 1, math.sqrt(2.0)),
)


class SkeletonExtractor:
    def extract(self, walkable_yx: np.ndarray) -> np.ndarray:
        return skeletonize(walkable_yx.astype(bool))

    @staticmethod
    def points(skeleton_yx: np.ndarray) -> list[dict[str, int]]:
        return [
            {"x": int(x), "y": int(y)}
            for y, x in np.argwhere(skeleton_yx)
        ]

    @staticmethod
    def graph(
        skeleton_yx: np.ndarray,
        walkable_yx: Optional[np.ndarray] = None,
    ) -> tuple[list[tuple[int, int]], dict[int, list[tuple[int, float]]]]:
        coordinates = [(int(x), int(y)) for y, x in np.argwhere(skeleton_yx)]
        index = {coordinate: idx for idx, coordinate in enumerate(coordinates)}
        adjacency: dict[int, list[tuple[int, float]]] = defaultdict(list)
        height, width = skeleton_yx.shape
        for idx, (x, y) in enumerate(coordinates):
            for dx, dy, cost in _NEIGHBORS_8:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                neighbor_idx = index.get((nx, ny))
                if neighbor_idx is None:
                    continue
                # Do not let an 8-neighbor edge cut between two orthogonal
                # walls.  The walkable mask, rather than the one-pixel
                # skeleton, is the physical authority for this check.
                if dx and dy and walkable_yx is not None:
                    if not walkable_yx[y, nx] or not walkable_yx[ny, x]:
                        continue
                adjacency[idx].append((neighbor_idx, cost))
        return coordinates, dict(adjacency)

    @staticmethod
    def classify_features(skeleton_yx: np.ndarray) -> list[tuple[int, int, str]]:
        height, width = skeleton_yx.shape
        features: list[tuple[int, int, str]] = []
        for y, x in np.argwhere(skeleton_yx):
            neighbors: list[tuple[int, int]] = []
            for dx, dy, _ in _NEIGHBORS_8:
                nx, ny = int(x + dx), int(y + dy)
                if 0 <= nx < width and 0 <= ny < height and skeleton_yx[ny, nx]:
                    neighbors.append((dx, dy))
            if len(neighbors) <= 1:
                features.append((int(x), int(y), "DEAD_END"))
            elif len(neighbors) >= 3:
                features.append((int(x), int(y), "JUNCTION"))
            elif len(neighbors) == 2:
                (dx1, dy1), (dx2, dy2) = neighbors
                if dx1 != -dx2 or dy1 != -dy2:
                    features.append((int(x), int(y), "CORNER"))
        return features
