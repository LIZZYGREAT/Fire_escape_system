import numpy as np

from app.models import EditorProject
from app.services.placement import PlacementService
from app.services.skeleton_extractor import SkeletonExtractor
from app.services.topology_builder import _absolute_direction


def _project(black_boxes=None) -> EditorProject:
    return EditorProject.model_validate(
        {
            "map": {
                "id": "placement-test",
                "name": "Placement test",
                "width": 60,
                "height": 31,
                "metersPerPixel": 1,
            },
            "entities": {"blackBoxes": black_boxes or []},
            "settings": {
                "candidateMergeDistance": 3,
                "coverageRadius": 8,
                "maxBoxDistance": 12,
                "coverageSampleStep": 1,
            },
        }
    )


def _single_entrance_branch() -> tuple[np.ndarray, np.ndarray]:
    skeleton = np.zeros((31, 60), dtype=bool)
    skeleton[15, 2:58] = True
    skeleton[5:16, 25] = True
    return skeleton.copy(), skeleton


def test_short_single_entrance_branch_creates_one_room_guide():
    walkable, skeleton = _single_entrance_branch()
    service = PlacementService(SkeletonExtractor())

    candidates = service.generate_candidates(_project(), walkable, skeleton)
    room_guides = [candidate for candidate in candidates if candidate.reason == "ROOM_EXIT_GUIDE"]

    assert len(room_guides) == 1
    assert room_guides[0].x == 25
    assert 8 <= room_guides[0].y <= 12


def test_imported_trial_points_are_pruned_and_old_automatic_points_are_rebuilt():
    walkable = np.zeros((31, 60), dtype=bool)
    walkable[15, 1:59] = True
    skeleton = walkable.copy()
    project = _project(
        [
            {"id": "I-1", "x": 10, "y": 15, "source": "imported"},
            {"id": "I-2", "x": 12, "y": 15, "source": "imported"},
            {"id": "I-3", "x": 14, "y": 15, "source": "imported"},
            {"id": "I-4", "x": 30, "y": 15, "source": "imported"},
            {"id": "OLD-AUTO", "x": 45, "y": 15, "source": "automatic"},
        ]
    )

    candidates = PlacementService(SkeletonExtractor()).generate_candidates(
        project,
        walkable,
        skeleton,
    )
    imported = [candidate for candidate in candidates if candidate.reason == "IMPORTED"]

    assert len(imported) == 2
    assert {candidate.source_entity_id for candidate in imported}.isdisjoint({"I-2", "I-3"})
    assert all(candidate.source_entity_id != "OLD-AUTO" for candidate in candidates)


def test_topology_instructions_are_always_cardinal():
    cases = [
        ([{"x": 14, "y": 14}], (14, 14)),
        ([{"x": 6, "y": 14}], (6, 14)),
        ([{"x": 12, "y": 17}], (12, 17)),
        ([{"x": 8, "y": 3}], (8, 3)),
    ]

    directions = {
        _absolute_direction((10, 10), path, target)
        for path, target in cases
    }

    assert directions == {"N", "E", "S", "W"}

