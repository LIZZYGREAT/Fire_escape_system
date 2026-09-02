"""Deterministic indoor heat and smoke propagation model."""

from typing import Optional

import numpy as np
from scipy.ndimage import gaussian_filter

from . import config


class FireDynamicsEngine:
    """Evolve separate heat and smoke fields without crossing solid walls.

    The default model is deliberately deterministic. ``spread_rate_map`` is a
    public integration seam for future material/ventilation/LoRa inference: a
    value below 1 slows propagation and a value above 1 accelerates it.
    """

    IGNITION_TEMP = 50.0
    WIND_INFLUENCE = 0.55
    HEAT_MIX_RATE = 0.12
    SMOKE_MIX_RATE = 0.30
    SMOKE_DISSIPATION = 0.992
    AMBIENT_COOLING = 0.992
    BURNOUT_COOLING = 0.94

    def __init__(
        self,
        width: int,
        height: int,
        mask_matrix: np.ndarray,
        seed: Optional[int] = 0,
        spread_rate_map: Optional[np.ndarray] = None,
    ):
        self.width = width
        self.height = height
        self.mask_matrix = (np.asarray(mask_matrix) > 0).astype(np.uint8)
        if self.mask_matrix.shape != (width, height):
            raise ValueError("mask_matrix shape must match (width, height)")

        shape = (width, height)
        self.heat_matrix = np.full(shape, config.W_BASE, dtype=np.float32)
        self.smoke_matrix = np.zeros(shape, dtype=np.float32)
        self.fuel_matrix = np.full(shape, 100.0, dtype=np.float32)
        self.current_risk_matrix = np.full(shape, config.W_BASE, dtype=np.float32)
        self.reported_risk_matrix = np.full(shape, config.W_BASE, dtype=np.float32)
        self.reported_heat_matrix = self.heat_matrix.copy()
        self.reported_smoke_matrix = self.smoke_matrix.copy()
        self.last_environment_updates: list[tuple[int, int, float, float, float]] = []

        rng = np.random.default_rng(seed)
        noise = gaussian_filter(rng.uniform(0.0, 1.0, size=shape), sigma=4.0)
        normalized = (noise - noise.min()) / (noise.max() - noise.min() + 1e-6)
        default_rates = (0.78 + normalized * 0.44).astype(np.float32)
        self.spread_rate_map = default_rates
        self.spatial_variance = self.spread_rate_map  # compatibility alias
        if spread_rate_map is not None:
            self.update_spread_rate_map(spread_rate_map)

        self.wind_vector = (0.35, 0.2)
        self.base_kernel = np.array(
            [[0.06, 0.16, 0.06], [0.16, 0.0, 0.16], [0.06, 0.16, 0.06]],
            dtype=np.float32,
        )
        self.dynamic_kernel = self._generate_wind_kernel()
        self._refresh_transport_normalizer()

    def update_spread_rate_map(self, values: np.ndarray) -> None:
        """Replace local propagation multipliers inferred by a future adapter."""
        array = np.asarray(values, dtype=np.float32)
        if array.shape != (self.width, self.height):
            raise ValueError("spread rate map shape must match the simulation grid")
        self.spread_rate_map = np.clip(array, 0.15, 3.0)
        self.spatial_variance = self.spread_rate_map

    def _generate_wind_kernel(self) -> np.ndarray:
        wx, wy = self.wind_vector
        kernel = np.zeros_like(self.base_kernel)
        for ix in range(3):
            for iy in range(3):
                if ix == 1 and iy == 1:
                    continue
                dx, dy = ix - 1, iy - 1
                bias = 1.0 + self.WIND_INFLUENCE * (wx * dx + wy * dy)
                kernel[ix, iy] = self.base_kernel[ix, iy] * max(0.05, bias)
        total = float(kernel.sum())
        return kernel / total if total > 0 else self.base_kernel / self.base_kernel.sum()

    def update_wind(self, new_wind_vector: tuple[float, float]) -> None:
        """Update the global airflow vector used by the default model."""
        if len(new_wind_vector) != 2:
            raise ValueError("wind vector must contain x and y components")
        self.wind_vector = (float(new_wind_vector[0]), float(new_wind_vector[1]))
        self.dynamic_kernel = self._generate_wind_kernel()
        self._refresh_transport_normalizer()

    def _diffuse_through_walkable(self, field: np.ndarray) -> np.ndarray:
        incoming = np.zeros_like(field, dtype=np.float32)
        walkable = self.mask_matrix == 1
        size_x, size_y = field.shape

        for kernel_x in range(3):
            for kernel_y in range(3):
                if kernel_x == 1 and kernel_y == 1:
                    continue
                weight = float(self.dynamic_kernel[kernel_x, kernel_y])
                if weight <= 0:
                    continue
                dx, dy = kernel_x - 1, kernel_y - 1
                sx0, sx1 = max(0, -dx), size_x - max(0, dx)
                sy0, sy1 = max(0, -dy), size_y - max(0, dy)
                if sx0 >= sx1 or sy0 >= sy1:
                    continue
                tx0, tx1, ty0, ty1 = sx0 + dx, sx1 + dx, sy0 + dy, sy1 + dy
                source = (slice(sx0, sx1), slice(sy0, sy1))
                target = (slice(tx0, tx1), slice(ty0, ty1))
                allowed = walkable[source] & walkable[target]
                if dx and dy:
                    allowed &= (
                        walkable[slice(tx0, tx1), slice(sy0, sy1)]
                        & walkable[slice(sx0, sx1), slice(ty0, ty1)]
                    )
                target_values = incoming[target]
                target_values[allowed] += field[source][allowed] * weight
        return incoming

    def _refresh_transport_normalizer(self) -> None:
        walkable = (self.mask_matrix == 1).astype(np.float32)
        self.transport_normalizer = self._diffuse_through_walkable(walkable)

    def _neighbor_average(self, field: np.ndarray) -> np.ndarray:
        incoming = self._diffuse_through_walkable(field)
        return np.divide(
            incoming,
            self.transport_normalizer,
            out=np.zeros_like(incoming),
            where=self.transport_normalizer > 1e-6,
        )

    def tick_update(self, active_fire_sources: list, iterations: int = 2) -> list:
        walkable = self.mask_matrix == 1
        for fx, fy, intensity in active_fire_sources:
            if 0 <= fx < self.width and 0 <= fy < self.height and walkable[fx, fy]:
                self.heat_matrix[fx, fy] = max(self.heat_matrix[fx, fy], float(intensity))
                self.smoke_matrix[fx, fy] = max(self.smoke_matrix[fx, fy], 35.0)

        for _ in range(max(1, int(iterations))):
            excess_heat = np.maximum(0.0, self.heat_matrix - config.W_BASE)
            heat_neighbors = self._neighbor_average(excess_heat)
            heat_rate = np.clip(self.HEAT_MIX_RATE * self.spread_rate_map, 0.025, 0.32)
            excess_heat += (heat_neighbors - excess_heat) * heat_rate
            self.heat_matrix = config.W_BASE + np.maximum(0.0, excess_heat)

            combusting = (self.heat_matrix >= self.IGNITION_TEMP) & (self.fuel_matrix > 0) & walkable
            burned_out = (self.fuel_matrix <= 0) & walkable
            self.fuel_matrix[combusting] -= 1.6 * self.spread_rate_map[combusting]
            np.clip(self.fuel_matrix, 0.0, 100.0, out=self.fuel_matrix)
            self.heat_matrix[combusting] += 7.5 * self.spread_rate_map[combusting]
            self.smoke_matrix[combusting] += 8.0 * self.spread_rate_map[combusting]
            self.heat_matrix[~combusting & walkable] = config.W_BASE + (
                self.heat_matrix[~combusting & walkable] - config.W_BASE
            ) * self.AMBIENT_COOLING
            self.heat_matrix[burned_out] = config.W_BASE + (
                self.heat_matrix[burned_out] - config.W_BASE
            ) * self.BURNOUT_COOLING

            smoke_neighbors = self._neighbor_average(self.smoke_matrix)
            smoke_rate = np.clip(self.SMOKE_MIX_RATE * self.spread_rate_map, 0.06, 0.55)
            self.smoke_matrix += (smoke_neighbors - self.smoke_matrix) * smoke_rate
            self.smoke_matrix *= self.SMOKE_DISSIPATION

            self.heat_matrix = np.where(
                walkable,
                np.clip(self.heat_matrix, config.W_BASE, config.W_FIRE_MAX),
                config.W_BASE,
            )
            self.smoke_matrix = np.where(walkable, np.clip(self.smoke_matrix, 0.0, 100.0), 0.0)

        self.current_risk_matrix = np.where(
            walkable,
            np.clip(self.heat_matrix + self.smoke_matrix * 0.55, config.W_BASE, config.W_FIRE_MAX),
            config.W_BASE,
        )

        heat_delta = np.abs(self.heat_matrix - self.reported_heat_matrix)
        smoke_delta = np.abs(self.smoke_matrix - self.reported_smoke_matrix)
        environment_mask = (heat_delta > 0.35) | (smoke_delta > 0.35)
        self.last_environment_updates = []
        for x, y in np.argwhere(environment_mask):
            values = (
                int(x), int(y), float(self.heat_matrix[x, y]),
                float(self.smoke_matrix[x, y]), float(self.current_risk_matrix[x, y]),
            )
            self.last_environment_updates.append(values)
            self.reported_heat_matrix[x, y] = values[2]
            self.reported_smoke_matrix[x, y] = values[3]

        risk_delta = np.abs(self.current_risk_matrix - self.reported_risk_matrix)
        crossed = (
            ((self.reported_risk_matrix < 35.0) & (self.current_risk_matrix >= 35.0))
            | ((self.reported_risk_matrix < 70.0) & (self.current_risk_matrix >= 70.0))
        )
        updates = []
        for x, y in np.argwhere((risk_delta > 3.0) | crossed):
            value = float(self.current_risk_matrix[x, y])
            updates.append((int(x), int(y), value))
            self.reported_risk_matrix[x, y] = value
        return updates
