from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from scipy.spatial import cKDTree

from app.models import BlackBoxEntity, EditorProject, MapEntity
from .skeleton_extractor import SkeletonExtractor


@dataclass
class TopologyResult:
    topology: dict[str, Any]
    adjacency: dict[str, list[tuple[str, float]]]
    snapped_indices: dict[str, int]
    skeleton_coordinates: list[tuple[int, int]]
    skeleton_adjacency: dict[int, list[tuple[int, float]]]
    unreachable_boxes: list[str]
    ambiguous_direction_count: int


def _simplify_path(path: list[tuple[int, int]]) -> list[dict[str, int]]:
    if len(path) <= 2:
        return [{"x": x, "y": y} for x, y in path]
    result = [path[0]]
    previous_direction: Optional[tuple[int, int]] = None
    for index in range(1, len(path)):
        dx = path[index][0] - path[index - 1][0]
        dy = path[index][1] - path[index - 1][1]
        direction = (int(math.copysign(1, dx)) if dx else 0, int(math.copysign(1, dy)) if dy else 0)
        if previous_direction is not None and direction != previous_direction:
            result.append(path[index - 1])
        previous_direction = direction
    result.append(path[-1])
    return [{"x": int(x), "y": int(y)} for x, y in result]


def _absolute_direction(
    origin: tuple[float, float],
    path: list[dict[str, int]],
    target: tuple[float, float],
) -> str:
    destination = target
    for point in path:
        if math.hypot(point["x"] - origin[0], point["y"] - origin[1]) >= 0.5:
            destination = (point["x"], point["y"])
            break
    dx, dy = destination[0] - origin[0], destination[1] - origin[1]
    if abs(dx) >= abs(dy):
        return "E" if dx >= 0 else "W"
    return "S" if dy >= 0 else "N"


class TopologyBuilder:
    def __init__(self, skeleton_extractor: SkeletonExtractor):
        self.skeleton_extractor = skeleton_extractor

    @staticmethod
    def _dijkstra(
        adjacency: dict[int, list[tuple[int, float]]],
        start: int,
        cutoff: Optional[float] = None,
    ) -> tuple[dict[int, float], dict[int, int]]:
        distances = {start: 0.0}
        predecessors: dict[int, int] = {}
        queue = [(0.0, start)]
        while queue:
            current_distance, node = heapq.heappop(queue)
            if current_distance != distances.get(node):
                continue
            if cutoff is not None and current_distance > cutoff:
                continue
            for neighbor, edge_cost in adjacency.get(node, []):
                new_distance = current_distance + edge_cost
                if cutoff is not None and new_distance > cutoff:
                    continue
                if new_distance + 1e-9 < distances.get(neighbor, math.inf):
                    distances[neighbor] = new_distance
                    predecessors[neighbor] = node
                    heapq.heappush(queue, (new_distance, neighbor))
        return distances, predecessors

    @staticmethod
    def _restore_path(
        start: int,
        target: int,
        predecessors: dict[int, int],
        coordinates: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        if start == target:
            return [coordinates[start]]
        if target not in predecessors:
            return []
        reverse = [target]
        while reverse[-1] != start:
            parent = predecessors.get(reverse[-1])
            if parent is None:
                return []
            reverse.append(parent)
        reverse.reverse()
        return [coordinates[index] for index in reverse]

    @staticmethod
    def _entity_records(
        project: EditorProject,
        boxes: list[BlackBoxEntity],
    ) -> list[tuple[str, str, MapEntity]]:
        records: list[tuple[str, str, MapEntity]] = []
        records.extend((box.id, "BLACK_BOX", box) for box in boxes)
        records.extend((entity.id, "EXIT", entity) for entity in project.entities.exits)
        records.extend((entity.id, "REFUGE", entity) for entity in project.entities.refuges)
        records.extend((entity.id, "STAIR", entity) for entity in project.entities.stairs)
        unique: dict[str, tuple[str, str, MapEntity]] = {}
        for record in records:
            unique.setdefault(record[0], record)
        return list(unique.values())

    def build(
        self,
        project: EditorProject,
        walkable_yx: np.ndarray,
        skeleton_yx: np.ndarray,
        boxes: list[BlackBoxEntity],
    ) -> TopologyResult:
        coordinates, skeleton_adjacency = self.skeleton_extractor.graph(
            skeleton_yx,
            walkable_yx,
        )
        records = self._entity_records(project, boxes)
        if not coordinates:
            return TopologyResult(
                topology={"nodes": [], "edges": [], "instructions": {}},
                adjacency={record[0]: [] for record in records},
                snapped_indices={},
                skeleton_coordinates=[],
                skeleton_adjacency={},
                unreachable_boxes=[box.id for box in boxes],
                ambiguous_direction_count=0,
            )

        tree = cKDTree(np.asarray(coordinates, dtype=np.float64))
        snapped_indices: dict[str, int] = {}
        nodes: list[dict[str, Any]] = []
        record_by_id = {record[0]: record for record in records}
        for entity_id, kind, entity in records:
            snap_distance, snap_index = tree.query([entity.x, entity.y], k=1)
            snap_index = int(snap_index)
            snapped_indices[entity_id] = snap_index
            snap_x, snap_y = coordinates[snap_index]
            nodes.append(
                {
                    "id": entity_id,
                    "kind": kind,
                    "x": float(entity.x),
                    "y": float(entity.y),
                    "snapX": snap_x,
                    "snapY": snap_y,
                    "snapDistance": round(float(snap_distance), 3),
                }
            )

        max_distance = (
            project.settings.max_box_distance / project.map.meters_per_pixel
        )
        adjacency: dict[str, list[tuple[str, float]]] = {record[0]: [] for record in records}
        edges: list[dict[str, Any]] = []
        record_ids = [record[0] for record in records]

        for source_position, source_id in enumerate(record_ids):
            source_kind = record_by_id[source_id][1]
            if source_kind not in {"BLACK_BOX", "STAIR"}:
                continue
            start_index = snapped_indices[source_id]
            distances, predecessors = self._dijkstra(
                skeleton_adjacency,
                start_index,
                cutoff=max_distance,
            )
            for target_id in record_ids[source_position + 1 :]:
                target_index = snapped_indices[target_id]
                distance = distances.get(target_index)
                if distance is None or distance <= 0 or distance > max_distance:
                    continue
                path = self._restore_path(start_index, target_index, predecessors, coordinates)
                if not path:
                    continue
                simplified = _simplify_path(path)
                edge = {
                    "id": f"{source_id}--{target_id}",
                    "source": source_id,
                    "target": target_id,
                    "distancePixels": round(float(distance), 3),
                    "distanceMeters": round(float(distance * project.map.meters_per_pixel), 3),
                    "path": simplified,
                }
                edges.append(edge)
                adjacency[source_id].append((target_id, float(distance)))
                adjacency[target_id].append((source_id, float(distance)))

        # A source-order restriction above keeps each pair unique, but can skip
        # an edge when the earlier node is an exit.  Add those missing box/goal
        # pairs deterministically.
        existing_pairs = {frozenset((edge["source"], edge["target"])) for edge in edges}
        for box in boxes:
            start_index = snapped_indices.get(box.id)
            if start_index is None:
                continue
            distances, predecessors = self._dijkstra(skeleton_adjacency, start_index, cutoff=max_distance)
            for goal_id, goal_kind, _ in records:
                if goal_kind not in {"EXIT", "REFUGE"}:
                    continue
                pair = frozenset((box.id, goal_id))
                if pair in existing_pairs:
                    continue
                target_index = snapped_indices[goal_id]
                distance = distances.get(target_index)
                if distance is None or distance <= 0 or distance > max_distance:
                    continue
                path = self._restore_path(start_index, target_index, predecessors, coordinates)
                simplified = _simplify_path(path)
                edges.append(
                    {
                        "id": f"{box.id}--{goal_id}",
                        "source": box.id,
                        "target": goal_id,
                        "distancePixels": round(float(distance), 3),
                        "distanceMeters": round(float(distance * project.map.meters_per_pixel), 3),
                        "path": simplified,
                    }
                )
                adjacency[box.id].append((goal_id, float(distance)))
                adjacency[goal_id].append((box.id, float(distance)))
                existing_pairs.add(pair)

        goal_ids = {
            entity_id
            for entity_id, kind, _ in records
            if kind in {"EXIT", "REFUGE"}
        }
        goal_kind = {entity_id: kind for entity_id, kind, _ in records if entity_id in goal_ids}
        graph_distance: dict[str, float] = {goal_id: 0.0 for goal_id in goal_ids}
        selected_goal: dict[str, str] = {goal_id: goal_id for goal_id in goal_ids}
        queue = [(0.0, goal_id) for goal_id in sorted(goal_ids)]
        heapq.heapify(queue)
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != graph_distance.get(node):
                continue
            for neighbor, edge_cost in adjacency.get(node, []):
                new_cost = cost + edge_cost
                if new_cost + 1e-9 < graph_distance.get(neighbor, math.inf):
                    graph_distance[neighbor] = new_cost
                    selected_goal[neighbor] = selected_goal[node]
                    heapq.heappush(queue, (new_cost, neighbor))

        edge_lookup: dict[frozenset[str], dict[str, Any]] = {
            frozenset((edge["source"], edge["target"])): edge for edge in edges
        }
        instructions: dict[str, dict[str, Any]] = {}
        ambiguous = 0
        unreachable: list[str] = []
        box_by_id = {box.id: box for box in boxes}
        for box in boxes:
            if box.id not in graph_distance:
                unreachable.append(box.id)
                instructions[box.id] = {
                    "mode": "SOS",
                    "direction": "NONE",
                    "nextAnchorId": None,
                    "pathCost": None,
                }
                continue
            options = [
                (edge_cost + graph_distance.get(neighbor, math.inf), neighbor, edge_cost)
                for neighbor, edge_cost in adjacency.get(box.id, [])
                if graph_distance.get(neighbor, math.inf) + 1e-9 < graph_distance[box.id]
            ]
            options.sort(key=lambda option: (round(option[0], 9), option[1]))
            if not options:
                unreachable.append(box.id)
                instructions[box.id] = {
                    "mode": "SOS",
                    "direction": "NONE",
                    "nextAnchorId": None,
                    "pathCost": None,
                }
                continue
            if len(options) > 1 and abs(options[0][0] - options[1][0]) < 1e-6:
                ambiguous += 1
            _, next_id, _ = options[0]
            edge = edge_lookup[frozenset((box.id, next_id))]
            path = edge["path"]
            if edge["target"] == box.id:
                path = list(reversed(path))
            target_entity = record_by_id[next_id][2]
            goal_id = selected_goal.get(box.id)
            mode = "ESCAPE" if goal_kind.get(goal_id) == "EXIT" else "REFUGE"
            instructions[box.id] = {
                "mode": mode,
                "direction": _absolute_direction(
                    (box.x, box.y),
                    path,
                    (target_entity.x, target_entity.y),
                ),
                "nextAnchorId": next_id,
                "pathCost": round(float(graph_distance[box.id]), 3),
                "goalId": goal_id,
            }

        return TopologyResult(
            topology={
                "nodes": nodes,
                "edges": edges,
                "instructions": instructions,
            },
            adjacency=adjacency,
            snapped_indices=snapped_indices,
            skeleton_coordinates=coordinates,
            skeleton_adjacency=skeleton_adjacency,
            unreachable_boxes=sorted(set(unreachable)),
            ambiguous_direction_count=ambiguous,
        )

    @staticmethod
    def n_minus_one(
        topology: TopologyResult,
        boxes: list[BlackBoxEntity],
        project: EditorProject,
    ) -> tuple[float, list[str]]:
        if not boxes:
            return 1.0, []
        goals = {entity.id for entity in project.entities.exits + project.entities.refuges}
        failures: list[str] = []
        for removed in boxes:
            remaining_ids = {box.id for box in boxes if box.id != removed.id}
            if not remaining_ids:
                continue
            reachable = set(goals)
            queue = list(goals)
            while queue:
                node = queue.pop()
                for neighbor, _ in topology.adjacency.get(node, []):
                    if neighbor == removed.id or neighbor in reachable:
                        continue
                    reachable.add(neighbor)
                    queue.append(neighbor)
            if not remaining_ids.issubset(reachable):
                failures.append(removed.id)
        return (len(boxes) - len(failures)) / len(boxes), failures
