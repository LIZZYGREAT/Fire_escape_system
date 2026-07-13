from __future__ import annotations

import base64
import binascii

import cv2
import numpy as np

from app.models import EditorProject, Stroke
from .map_repository import MapRepository, MapRepositoryError


class RasterizationError(ValueError):
    pass


class MapRasterizer:
    """Convert editor vectors and optional source imagery into y/x masks."""

    def __init__(self, repository: MapRepository):
        self.repository = repository

    @staticmethod
    def _decode_image(data_url: str, width: int, height: int) -> np.ndarray:
        try:
            encoded = data_url.split(",", 1)[1] if "," in data_url else data_url
            raw = base64.b64decode(encoded, validate=True)
        except (IndexError, ValueError, binascii.Error) as exc:
            raise RasterizationError("imageDataUrl is not valid base64 data") from exc
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise RasterizationError("imageDataUrl cannot be decoded as an image")
        if image.shape != (height, width):
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        return (image > 127).astype(np.uint8)

    @staticmethod
    def _draw_stroke(mask: np.ndarray, stroke: Stroke, value: int) -> None:
        if not stroke.points:
            return
        height, width = mask.shape
        points = np.asarray(
            [
                [
                    int(np.clip(round(point.x), 0, width - 1)),
                    int(np.clip(round(point.y), 0, height - 1)),
                ]
                for point in stroke.points
            ],
            dtype=np.int32,
        )
        thickness = max(1, int(round(stroke.size)))
        if stroke.closed and len(points) >= 3:
            cv2.fillPoly(mask, [points], int(value))
            return
        if len(points) == 1:
            cv2.circle(mask, tuple(points[0]), max(1, thickness // 2), int(value), -1)
            return
        cv2.polylines(
            mask,
            [points],
            isClosed=stroke.closed,
            color=int(value),
            thickness=thickness,
            lineType=cv2.LINE_8,
        )

    def rasterize(self, project: EditorProject) -> dict[str, np.ndarray]:
        width = project.map.width
        height = project.map.height

        if project.map.source_mask_path:
            try:
                walkable = self.repository.load_source_mask(project)
            except MapRepositoryError as exc:
                raise RasterizationError(str(exc)) from exc
            if walkable.shape != (height, width):
                raise RasterizationError(
                    f"source mask shape {walkable.shape} does not match "
                    f"map height/width {(height, width)}"
                )
        elif project.map.image_data_url:
            walkable = self._decode_image(project.map.image_data_url, width, height)
        else:
            walkable = np.zeros((height, width), dtype=np.uint8)

        # wallPixels describes the imported/base raster.  Walkable brush edits
        # are intentionally applied afterwards so an engineer can reopen a door
        # or repair a bad threshold result.  Explicit wall strokes win last.
        for x, y in project.annotations.wall_pixels:
            if 0 <= x < width and 0 <= y < height:
                walkable[y, x] = 0

        for stroke in project.annotations.strokes.walkable:
            self._draw_stroke(walkable, stroke, 1)
        for stroke in project.annotations.strokes.walls:
            self._draw_stroke(walkable, stroke, 0)

        walkable = (walkable > 0).astype(np.uint8)
        wall = (1 - walkable).astype(np.uint8)

        if project.annotations.fire_domains:
            fire_domain = np.zeros_like(walkable)
            for stroke in project.annotations.fire_domains:
                self._draw_stroke(fire_domain, stroke, 1)
            fire_domain &= walkable
        else:
            fire_domain = walkable.copy()

        return {
            "walkable": walkable,
            "wall": wall,
            "fire_domain": fire_domain,
        }

