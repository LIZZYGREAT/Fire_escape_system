import numpy as np

from core import config
from core.fire_dynamics import FireDynamicsEngine


def test_fire_and_smoke_do_not_cut_a_blocked_diagonal_corner():
    mask = np.ones((5, 5), dtype=np.uint8)
    mask[2, 1] = 0
    mask[1, 2] = 0
    engine = FireDynamicsEngine(5, 5, mask)

    engine.tick_update([(1, 1, 100.0)], iterations=1)

    assert engine.heat_matrix[2, 2] == config.W_BASE
    assert engine.smoke_matrix[2, 2] == 0.0


def test_non_250_map_edges_are_not_artificially_cleared():
    mask = np.ones((12, 7), dtype=np.uint8)
    engine = FireDynamicsEngine(12, 7, mask)

    engine.tick_update([(0, 0, 100.0)], iterations=1)

    assert engine.heat_matrix[0, 0] > config.W_BASE
    assert engine.smoke_matrix[0, 0] > 0.0


def test_wall_cells_remain_at_baseline():
    mask = np.ones((7, 9), dtype=np.uint8)
    mask[3, 4] = 0
    engine = FireDynamicsEngine(7, 9, mask)

    engine.tick_update([(3, 3, 100.0), (3, 4, 100.0)], iterations=2)

    assert engine.heat_matrix[3, 4] == config.W_BASE
    assert engine.smoke_matrix[3, 4] == 0.0
    assert engine.current_risk_matrix[3, 4] == config.W_BASE


def test_default_spatial_variance_is_deterministic():
    mask = np.ones((8, 6), dtype=np.uint8)

    first = FireDynamicsEngine(8, 6, mask)
    second = FireDynamicsEngine(8, 6, mask)

    np.testing.assert_array_equal(first.spatial_variance, second.spatial_variance)


def test_local_spread_rate_map_changes_propagation_speed():
    mask = np.ones((9, 9), dtype=np.uint8)
    rates = np.ones((9, 9), dtype=np.float32)
    rates[3, 4] = 0.2
    rates[5, 4] = 2.0
    engine = FireDynamicsEngine(9, 9, mask, spread_rate_map=rates)
    engine.update_wind((0.0, 0.0))

    for _ in range(4):
        engine.tick_update([(4, 4, 100.0)], iterations=1)

    assert engine.heat_matrix[5, 4] > engine.heat_matrix[3, 4]


def test_fire_fringe_is_not_promoted_to_artificial_fatal_risk():
    mask = np.ones((9, 9), dtype=np.uint8)
    engine = FireDynamicsEngine(9, 9, mask)

    engine.tick_update([(4, 4, 100.0)], iterations=1)

    assert float(engine.current_risk_matrix.max()) < 500.0
