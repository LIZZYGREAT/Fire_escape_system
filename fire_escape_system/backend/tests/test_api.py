import copy
import io
import zipfile

from fastapi.testclient import TestClient

from app.main import app


def receive_type(websocket, message_type: str) -> dict:
    for _ in range(20):
        message = websocket.receive_json()
        if message.get("type") == message_type:
            return message
    raise AssertionError(f"did not receive websocket message type {message_type}")


def test_static_monitor_and_editor_are_served_same_origin():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        editor = client.get("/editor/")
        assert editor.status_code == 200
        assert "text/html" in editor.headers["content-type"]
        snapshot = client.get("/api/runtime/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["map_metadata"]["map_id"] == "map_1"


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
        assert len(project["entities"]["stairs"]) == 2
        assert len(project["entities"]["elevators"]) == 1
        assert len(project["entities"]["fireHydrants"]) == 2
        assert len(project["entities"]["extinguishers"]) == 2
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
            assert snapshot["map_metadata"]["map_id"] == "map_1"
            assert snapshot["map_metadata"]["demo_mode"] is True
            assert snapshot["state_versions"]["topology_version"]
            assert snapshot["topology_tree"]
            assert "environment_data" in snapshot
            assert all(
                value["next"] is None
                for value in snapshot["topology_tree"].values()
                if value["mode"] == "SOS"
            )


def test_hazard_observation_contract_buffers_but_does_not_assimilate():
    with TestClient(app) as client:
        response = client.post(
            "/api/hazards/observations",
            json={
                "mapId": "demo_building",
                "observations": [
                    {
                        "sensorId": "lora-smoke-01",
                        "observedAt": "2026-07-13T10:00:00+08:00",
                        "source": "lora",
                        "floor": "F01",
                        "x": 60,
                        "y": 57,
                        "temperatureC": 48.2,
                        "smokePpm": 380,
                        "confidence": 0.93,
                    }
                ],
            },
        )
        assert response.status_code == 202
        assert response.json()["accepted"] == 1
        assert response.json()["assimilation"] == "buffered_not_enabled"


def test_runtime_speed_and_map_can_be_changed_over_websocket():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "control", "command": "set_speed", "speed": 4})
            settings = receive_type(websocket, "runtime_settings")
            assert settings == {"type": "runtime_settings", "speed": 4.0}

            websocket.send_json({"type": "control", "command": "select_map", "map_id": "demo_building"})
            snapshot = receive_type(websocket, "full_sync")
            assert snapshot["type"] == "full_sync"
            assert snapshot["map_metadata"]["map_id"] == "demo_building"

            websocket.send_json({"type": "control", "command": "select_map", "map_id": "map_1"})
            snapshot = receive_type(websocket, "full_sync")
            assert snapshot["type"] == "full_sync"
            assert snapshot["map_metadata"]["map_id"] == "map_1"

            websocket.send_json({"type": "control", "command": "set_speed", "speed": 2})
            assert receive_type(websocket, "runtime_settings")["speed"] == 2.0


def test_facility_footprint_on_wall_is_rejected():
    with TestClient(app) as client:
        project = client.get("/api/maps/default").json()
        project["entities"]["elevators"] = [
            {
                "id": "EL-INVALID",
                "x": 10,
                "y": 10,
                "shape": "rectangle",
                "width": 30,
                "height": 30,
            }
        ]
        response = client.post("/api/maps/validate", json=project)
        assert response.status_code == 200
        assert any(issue["code"] == "MAP_E008" for issue in response.json()["issues"])
