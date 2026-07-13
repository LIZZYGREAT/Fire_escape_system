from __future__ import annotations

import copy
import logging
from typing import Any, Optional

import numpy as np

from core import config
from core.dstar_lite import DStarLite
from core.fire_dynamics import FireDynamicsEngine
from core.lbb_manager import LBBManager
from core.system_controller import SystemTickController

from .map_compiler import MapCompiler
from .map_repository import MapRepository


logger = logging.getLogger("SimulationRuntime")


class SimulationRuntime:
    """Compatibility runtime backed by an active compiled map package."""

    def __init__(
        self,
        repository: MapRepository,
        compiler: MapCompiler,
        map_id: Optional[str] = None,
    ):
        self.repository = repository
        self.compiler = compiler
        self.map_id = map_id or repository.default_map_id()
        self.project = repository.load_project(self.map_id, prefer_draft=False)
        self.compiled = compiler.compile_internal(self.project)
        if not self.compiled.validation.valid:
            raise RuntimeError("active simulation map failed validation")

        self.walkable_yx = self.compiled.masks["walkable"]
        self.mask_matrix = self.walkable_yx.T
        self.width = self.project.map.width
        self.height = self.project.map.height
        self.exits = [
            (int(round(entity.x)), int(round(entity.y)))
            for entity in self.project.entities.exits
        ]
        self.black_boxes = [
            (int(round(entity.x)), int(round(entity.y)))
            for entity in self.compiled.boxes
        ]
        self.initial_fires = [
            (int(round(value.x)), int(round(value.y)), float(value.intensity))
            for value in self.project.simulation.initial_fires
        ]
        self.topology_version = self.compiled.topology_version
        self.risk_version = 0
        self.plan_version = 0
        self.command_version = 0
        self.tick_count = 0
        self.ground_truth_fires: list[tuple[int, int, float]] = []
        self.previous_topology_tree: dict[str, dict[str, Any]] = {}
        self.fire_engine: FireDynamicsEngine
        self.lbb_manager: LBBManager
        self.dstar_engine: DStarLite
        self.system_controller: SystemTickController
        self._initialize_engines()

    @property
    def map_metadata(self) -> dict[str, Any]:
        return {
            "map_id": self.project.map.id,
            "map_version": self.project.map.version,
            "topology_version": self.topology_version,
            "width": self.width,
            "height": self.height,
            "meters_per_pixel": self.project.map.meters_per_pixel,
            "coordinate_origin": "top_left",
            "x_axis": "east",
            "y_axis": "south",
        }

    @property
    def state_versions(self) -> dict[str, Any]:
        return {
            "topology_version": self.topology_version,
            "risk_version": self.risk_version,
            "plan_version": self.plan_version,
            "command_version": self.command_version,
        }

    def _initialize_engines(self) -> None:
        logger.info("initializing simulation from map package %s", self.map_id)
        self.fire_engine = FireDynamicsEngine(
            self.width,
            self.height,
            self.mask_matrix,
            seed=0,
        )
        self.lbb_manager = LBBManager(self.black_boxes, self.mask_matrix, self.exits)
        self.dstar_engine = DStarLite(self.exits)
        self.system_controller = SystemTickController(self.dstar_engine, self.lbb_manager)
        self.system_controller.initialize_baseline()
        self.tick_count = 0
        self.ground_truth_fires = []
        self.previous_topology_tree = {}

    @staticmethod
    def separate_public_and_rescue(
        tree: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Enforce that SOS never exposes a public escape next hop."""

        separated: dict[str, dict[str, Any]] = {}
        for node_id, original in tree.items():
            item = copy.deepcopy(original)
            status = int(item.get("status", 2))
            original_next = item.get("next")
            original_dir = item.get("dir", -1)
            if status in {1, 2} or not original_next:
                item.update(
                    {
                        "mode": "SOS",
                        "next": None,
                        "dir": -1,
                        "rescue_next": original_next,
                        "rescue_dir": original_dir,
                    }
                )
            else:
                item.update(
                    {
                        "mode": "ESCAPE_WARNING" if status == 3 else "ESCAPE",
                        "rescue_next": None,
                        "rescue_dir": -1,
                    }
                )
            separated[node_id] = item
        return separated

    def current_tree(self) -> dict[str, dict[str, Any]]:
        raw = self.system_controller.extract_topology_tree(
            self.black_boxes,
            self.fire_engine.current_risk_matrix,
        )
        return self.separate_public_and_rescue(raw)

    def tick_once(self) -> dict[str, Any]:
        self.tick_count += 1
        if self.tick_count == self.project.simulation.ignition_tick:
            self.ground_truth_fires.extend(self.initial_fires)

        updates = self.fire_engine.tick_update(self.ground_truth_fires, 2)
        if updates:
            self.risk_version += 1
            self.system_controller.sync_physical_to_graph(
                updates,
                self.fire_engine.current_risk_matrix,
            )

        current_tree = self.current_tree()
        tree_diff: dict[str, dict[str, Any]] = {}
        for node_id, value in current_tree.items():
            if self.previous_topology_tree.get(node_id) != value:
                tree_diff[node_id] = value
        if tree_diff:
            self.plan_version += 1
            self.command_version += 1
            self.previous_topology_tree.update(tree_diff)

        return {
            "type": "tick_update",
            "map_metadata": self.map_metadata,
            "state_versions": self.state_versions,
            "tick": self.tick_count,
            "fire_diff": [
                [int(x), int(y), round(float(value), 1)]
                for x, y, value in updates
            ],
            "topology_tree": tree_diff,
        }

    def full_sync(self) -> dict[str, Any]:
        current_tree = self.current_tree()
        self.previous_topology_tree = copy.deepcopy(current_tree)
        risk = self.fire_engine.current_risk_matrix
        wall_yx = np.argwhere(self.walkable_yx == 0)
        fire_xy = np.argwhere(risk > config.W_BASE)
        return {
            "type": "full_sync",
            "map_metadata": self.map_metadata,
            "state_versions": self.state_versions,
            "tick": self.tick_count,
            "wall_data": [[int(x), int(y)] for y, x in wall_yx],
            "fire_data": [
                [int(x), int(y), float(risk[x, y])]
                for x, y in fire_xy
            ],
            "topology_tree": current_tree,
            "exits_data": [list(value) for value in self.exits],
        }

    def reset(self) -> dict[str, Any]:
        self.risk_version += 1
        self.plan_version += 1
        self.command_version += 1
        self._initialize_engines()
        return self.full_sync()

    def inject_fire(self, x: int, y: int, intensity: float = 100.0) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError("fire coordinate is outside the active map")
        if not self.mask_matrix[x, y]:
            raise ValueError("fire coordinate is not in the propagation domain")
        self.ground_truth_fires.append((x, y, float(intensity)))

