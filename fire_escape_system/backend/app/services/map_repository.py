from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Union

import numpy as np

from app.models import EditorProject


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class MapRepositoryError(RuntimeError):
    pass


class MapRepository:
    """Filesystem repository for immutable source configs and editable drafts."""

    def __init__(self, maps_root: Union[str, Path]):
        self.maps_root = Path(maps_root).resolve()
        self.backend_root = self.maps_root.parent.resolve()
        self.data_root = self.backend_root / "data"
        self.maps_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_map_id(map_id: str) -> str:
        if not _SAFE_ID.fullmatch(map_id):
            raise MapRepositoryError(f"invalid map id: {map_id!r}")
        return map_id

    def map_dir(self, map_id: str) -> Path:
        map_id = self.validate_map_id(map_id)
        return self.maps_root / map_id

    def source_config_path(self, map_id: str) -> Path:
        return self.map_dir(map_id) / "map_config.json"

    def draft_path(self, map_id: str) -> Path:
        return self.map_dir(map_id) / "drafts" / "latest.json"

    def list_map_ids(self) -> list[str]:
        configured = {
            path.name
            for path in self.maps_root.iterdir()
            if path.is_dir() and (path / "map_config.json").exists()
        } if self.maps_root.exists() else set()
        compiled = {
            path.name
            for path in self.data_root.iterdir()
            if path.is_dir() and (path / "compiled_map.json").exists()
        } if self.data_root.exists() else set()
        return sorted(configured | compiled)

    def default_map_id(self) -> str:
        ids = self.list_map_ids()
        if "demo_building" in ids:
            return "demo_building"
        if not ids:
            raise MapRepositoryError("no map packages are installed")
        return ids[0]

    def load_project(self, map_id: str, *, prefer_draft: bool = True) -> EditorProject:
        draft_path = self.draft_path(map_id)
        source_path = self.source_config_path(map_id)
        path = draft_path if prefer_draft and draft_path.exists() else source_path
        if not path.exists():
            project = self._load_compiled_data_package(map_id)
        else:
            try:
                project = EditorProject.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise MapRepositoryError(f"cannot load map project {map_id}: {exc}") from exc

        if project.map.id != map_id:
            project = project.model_copy(update={"map": project.map.model_copy(update={"id": map_id})})

        # The default project is directly useful to the browser even without a
        # separately hosted background image.  Hydrate the compact source mask
        # into wallPixels only for transport; the source JSON remains compact.
        if not project.annotations.wall_pixels and project.map.source_mask_path:
            try:
                mask = self.load_source_mask(project)
            except MapRepositoryError:
                pass
            else:
                wall_yx = np.argwhere(mask == 0)
                annotations = project.annotations.model_copy(
                    update={
                        "wall_pixels": [
                            (int(x), int(y)) for y, x in wall_yx
                        ]
                    }
                )
                project = project.model_copy(update={"annotations": annotations})
        return project

    def _load_compiled_data_package(self, map_id: str) -> EditorProject:
        """Adapt a compiled ``backend/data/<map_id>`` package for the editor."""
        package_dir = self.data_root / self.validate_map_id(map_id)
        manifest_path = package_dir / "compiled_map.json"
        if not manifest_path.exists():
            raise MapRepositoryError(f"map package does not exist: {map_id}")

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            height, width = (int(value) for value in manifest["shape"])
            resolution = float(manifest["resolution_m_per_cell"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise MapRepositoryError(f"invalid compiled map manifest {map_id}: {exc}") from exc

        if width < 1 or height < 1 or resolution <= 0:
            raise MapRepositoryError(f"invalid compiled map dimensions or resolution: {map_id}")

        def read_entities(name: str) -> list[dict]:
            filename = manifest.get("entity_files", {}).get(name)
            if not filename:
                return []
            try:
                values = json.loads((package_dir / filename).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise MapRepositoryError(f"cannot load {filename}: {exc}") from exc
            return [value for value in values if value.get("enabled", True)]

        def semantic(name: str, id_key: str) -> list[dict]:
            return [
                {
                    "id": str(value[id_key]),
                    "x": float(value["grid_x"]),
                    "y": float(value["grid_y"]),
                    "label": str(value.get(id_key, "")),
                    "locked": False,
                }
                for value in read_entities(name)
            ]

        boxes = read_entities("black_boxes")
        coverage_radius = float(boxes[0].get("sensor_radius_m", 5.0)) if boxes else 5.0
        visible_radius = float(boxes[0].get("visibility_radius_m", 8.0)) if boxes else 8.0
        relative_mask = f"../../data/{map_id}/M_walkable.npy"
        base_image_path = package_dir / "floors" / "F01_base.png"
        image_data_url = None
        if base_image_path.exists():
            try:
                encoded_image = base64.b64encode(base_image_path.read_bytes()).decode("ascii")
                image_data_url = f"data:image/png;base64,{encoded_image}"
            except OSError as exc:
                raise MapRepositoryError(f"cannot load map base image: {exc}") from exc
        payload = {
            "schemaVersion": "1.0.0",
            "revision": 0,
            "map": {
                "id": map_id,
                "name": str(manifest.get("map_id", map_id)),
                "version": str(manifest.get("map_version", "1.0.0")),
                "width": width,
                "height": height,
                "metersPerPixel": resolution,
                "imageDataUrl": image_data_url,
                "sourceMaskPath": relative_mask,
                "coordinateOrigin": "top_left",
                "xAxis": "east",
                "yAxis": "south",
            },
            "entities": {
                "doors": semantic("doors", "door_id"),
                "exits": semantic("exits", "exit_id"),
                "refuges": semantic("refuges", "refuge_id"),
                "stairs": semantic("stairs", "stair_id"),
                "gateways": semantic("gateways", "gateway_id"),
                "blackBoxes": [
                    {
                        "id": str(value["box_id"]),
                        "x": float(value["grid_x"]),
                        "y": float(value["grid_y"]),
                        "label": str(value.get("box_id", "")),
                        "locked": False,
                        "mandatory": bool(value.get("mandatory", False)),
                        "source": "imported",
                    }
                    for value in boxes
                ],
            },
            "settings": {
                "coverageRadius": coverage_radius,
                "visibleRadius": visible_radius,
            },
        }
        try:
            return EditorProject.model_validate(payload)
        except ValueError as exc:
            raise MapRepositoryError(f"cannot adapt compiled map {map_id}: {exc}") from exc

    def save_project(self, project: EditorProject) -> EditorProject:
        map_id = self.validate_map_id(project.map.id)
        next_revision = project.revision + 1
        path = self.draft_path(map_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        saved = project.model_copy(update={"revision": next_revision})
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                saved.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
        return saved

    def resolve_project_file(self, project: EditorProject, relative_path: str) -> Path:
        candidate = (self.map_dir(project.map.id) / relative_path).resolve()
        try:
            candidate.relative_to(self.backend_root)
        except ValueError as exc:
            raise MapRepositoryError("map source path escapes the backend directory") from exc
        return candidate

    def load_source_mask(self, project: EditorProject) -> np.ndarray:
        relative_path = project.map.source_mask_path
        if not relative_path:
            raise MapRepositoryError("project does not define sourceMaskPath")
        path = self.resolve_project_file(project, relative_path)
        if not path.exists():
            raise MapRepositoryError(f"source mask does not exist: {path}")
        try:
            mask = np.load(path, allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise MapRepositoryError(f"cannot load source mask: {exc}") from exc
        if mask.ndim != 2:
            raise MapRepositoryError("source mask must be a two-dimensional array")
        return (mask > 0).astype(np.uint8)
