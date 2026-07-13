import io
import zipfile
from pathlib import Path

import numpy as np
import pytest

from app.models import EditorProject
from app.services.map_compiler import InvalidMapError, MapCompiler
from app.services.map_repository import MapRepository


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _demo_services():
    repository = MapRepository(BACKEND_ROOT / "maps")
    return repository, MapCompiler(repository)


def test_demo_map_compiles_deterministically_and_preserves_baseline():
    repository, compiler = _demo_services()
    project = repository.load_project("demo_building", prefer_draft=False)

    first = compiler.compile_internal(project)
    second = compiler.compile_internal(project)

    assert first.validation.valid
    assert first.topology_version == second.topology_version
    assert first.masks["walkable"].shape == (250, 250)
    assert int(first.masks["walkable"].sum()) == 54263
    assert len(first.boxes) == 32
    assert len(project.entities.exits) == 4
    assert not first.topology.unreachable_boxes
    assert all(
        instruction["mode"] in {"ESCAPE", "REFUGE"}
        and instruction["nextAnchorId"]
        for instruction in first.topology.topology["instructions"].values()
    )


def test_standard_zip_contains_compiled_layers_and_reports():
    repository, compiler = _demo_services()
    project = repository.load_project("demo_building", prefer_draft=False)
    archive = compiler.export_zip(project)
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        names = set(package.namelist())
        assert {
            "map_meta.json",
            "M_walkable.npy",
            "M_wall.npy",
            "M_fire_domain.npy",
            "M_clearance.npy",
            "M_skeleton.npy",
            "boxes.json",
            "exits.json",
            "topology.json",
            "placement_report.json",
            "validation_report.json",
        }.issubset(names)
        clearance = np.load(io.BytesIO(package.read("M_clearance.npy")))
        assert clearance.shape == (250, 250)
        assert clearance.dtype == np.float32


def test_invalid_map_can_preview_but_cannot_export():
    repository, compiler = _demo_services()
    project = repository.load_project("demo_building", prefer_draft=False)
    invalid = project.model_copy(
        update={
            "entities": project.entities.model_copy(update={"exits": []}),
        }
    )
    preview = compiler.compile(invalid)
    assert preview["valid"] is False
    assert any(issue["code"] == "MAP_E003" for issue in preview["issues"])
    with pytest.raises(InvalidMapError):
        compiler.export_zip(invalid)


def test_draft_round_trip_increments_revision(tmp_path):
    repository = MapRepository(tmp_path / "maps")
    project = EditorProject.model_validate(
        {
            "map": {
                "id": "draft_map",
                "name": "Draft",
                "width": 12,
                "height": 8,
                "metersPerPixel": 1,
            },
            "annotations": {
                "strokes": {
                    "walkable": [
                        {
                            "id": "corridor",
                            "points": [{"x": 1, "y": 4}, {"x": 10, "y": 4}],
                            "size": 3,
                        }
                    ]
                }
            },
            "entities": {
                "exits": [{"id": "exit", "x": 10, "y": 4}],
                "blackBoxes": [{"id": "box", "x": 2, "y": 4}],
            },
            "settings": {"maxBoxDistance": 20, "coverageRadius": 20},
        }
    )
    saved = repository.save_project(project)
    loaded = repository.load_project("draft_map")
    assert saved.revision == 1
    assert loaded.revision == 1
    assert loaded.entities.black_boxes[0].id == "box"

