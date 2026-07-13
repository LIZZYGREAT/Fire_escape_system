from app.services.simulation import SimulationRuntime


def test_sos_suppresses_public_next_and_preserves_rescue_next():
    tree = {
        "10,10": {"status": 2, "next": "20,10", "dir": 1},
        "20,10": {"status": 0, "next": "30,10", "dir": 1},
    }
    separated = SimulationRuntime.separate_public_and_rescue(tree)

    assert separated["10,10"]["mode"] == "SOS"
    assert separated["10,10"]["next"] is None
    assert separated["10,10"]["dir"] == -1
    assert separated["10,10"]["rescue_next"] == "20,10"
    assert separated["10,10"]["rescue_dir"] == 1

    assert separated["20,10"]["mode"] == "ESCAPE"
    assert separated["20,10"]["next"] == "30,10"
    assert separated["20,10"]["rescue_next"] is None

