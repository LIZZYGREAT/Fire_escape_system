import copy
import io
import zipfile

from fastapi.testclient import TestClient

from app.main import app


def test_static_monitor_and_editor_are_served_same_origin():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        editor = client.get("/editor/")
        assert editor.status_code == 200
        assert "text/html" in editor.headers["content-type"]


def test_map_api_compile_validate_candidates_and_export():
    with TestClient(app) as client:
        response = client.get("/api/maps/default")
        assert response.status_code == 200
        project = response.json()
        assert project["map"]["id"] == "demo_building"
        assert len(project["entities"]["blackBoxes"]) == 32

        validation = client.post("/api/maps/validate", json=project)
        assert validation.status_code == 200
        assert validation.json()["valid"] is True

        candidates = client.post("/api/placement/candidates", json=project)
        assert candidates.status_code == 200
        assert candidates.json()["candidate_boxes"]

        preview = client.post("/api/maps/compile", json={"project": project})
        assert preview.status_code == 200
        compiled = preview.json()
        assert compiled["skeleton_points"]
        assert compiled["candidate_boxes"]
        assert compiled["topology"]["instructions"]
        assert compiled["report"]["boxCount"] == 32

        exported = client.post("/api/maps/demo_building/export", json=project)
        assert exported.status_code == 200
        assert exported.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
            assert "M_walkable.npy" in package.namelist()


def test_compiled_data_package_is_exposed_as_an_editor_map():
    with TestClient(app) as client:
        listing = client.get("/api/maps")
        assert listing.status_code == 200
        assert "map_1" in listing.json()["maps"]

        response = client.get("/api/maps/map_1")
        assert response.status_code == 200
        project = response.json()
        assert project["map"]["id"] == "map_1"
        assert project["map"]["width"] == 512
        assert project["map"]["height"] == 512
        assert project["map"]["metersPerPixel"] == 0.1
        assert project["map"]["imageDataUrl"].startswith("data:image/png;base64,")
        assert len(project["entities"]["exits"]) == 4
        assert len(project["entities"]["refuges"]) == 2
        assert len(project["entities"]["blackBoxes"]) == 80
        assert project["annotations"]["wallPixels"]


def test_invalid_export_is_rejected_but_preview_is_returned():
    with TestClient(app) as client:
        project = client.get("/api/maps/default").json()
        invalid = copy.deepcopy(project)
        invalid["entities"]["exits"] = []

        preview = client.post("/api/maps/compile", json=invalid)
        assert preview.status_code == 200
        assert preview.json()["valid"] is False

        exported = client.post("/api/maps/demo_building/export", json=invalid)
        assert exported.status_code == 422
        assert exported.json()["detail"]["issues"]


def test_websocket_full_sync_includes_map_and_state_versions():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "request_full_sync"})
            snapshot = websocket.receive_json()
            assert snapshot["type"] == "full_sync"
            assert snapshot["map_metadata"]["map_id"] == "demo_building"
            assert snapshot["state_versions"]["topology_version"]
            assert snapshot["topology_tree"]
            assert all(
                value["next"] is None
                for value in snapshot["topology_tree"].values()
                if value["mode"] == "SOS"
            )
