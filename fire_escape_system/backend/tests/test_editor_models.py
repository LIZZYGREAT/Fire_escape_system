from app.models import EditorProject


def test_editor_project_accepts_frontend_camel_case_contract():
    project = EditorProject.model_validate(
        {
            "schemaVersion": "1.0.0",
            "revision": 2,
            "map": {
                "id": "floor_a",
                "name": "Floor A",
                "version": "1.2.0",
                "width": 20,
                "height": 10,
                "metersPerPixel": 0.25,
            },
            "annotations": {
                "strokes": {
                    "walkable": [
                        {
                            "id": "walk-1",
                            "points": [{"x": 1, "y": 1}, {"x": 18, "y": 1}],
                            "size": 3,
                        }
                    ],
                    "walls": [],
                },
                "wallPixels": [[0, 0]],
                "fireDomains": [],
            },
            "entities": {
                "exits": [{"id": "E1", "x": 18, "y": 1}],
                "blackBoxes": [
                    {
                        "id": "B1",
                        "x": 4,
                        "y": 1,
                        "mandatory": True,
                        "source": "manual",
                    }
                ],
            },
            "settings": {
                "coverageRadius": 5,
                "visibleRadius": 8,
                "maxBoxDistance": 8,
                "snapDistance": 4,
            },
        }
    )

    assert project.map.meters_per_pixel == 0.25
    assert project.entities.black_boxes[0].id == "B1"
    dumped = project.model_dump(mode="json", by_alias=True)
    assert dumped["schemaVersion"] == "1.0.0"
    assert dumped["entities"]["blackBoxes"][0]["mandatory"] is True
    assert dumped["settings"]["maxBoxDistance"] == 8

