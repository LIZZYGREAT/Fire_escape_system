#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将黑白/灰度建筑平面图转换为智能消防逃生系统的标准测试地图包。

输出：
- floors/F01_base.png
- floors/F01_wall.png
- floors/F01_walkable.png
- floors/F01_material.npy
- floors/F01_semantic.json
- M_wall.npy
- M_walkable.npy
- M_fire_domain.npy
- M_material.npy
- M_clearance.npy
- M_box.npy
- M_exit.npy
- M_refuge.npy
- exits.json / refuges.json / black_boxes.manual.json
- compiled_map.npz / compiled_map.json
- validation_report.json
- preview.png

注意：
1. 本脚本适用于墙体较深、背景较白的测试平面图。
2. 自动出口、避难点和黑盒仅供测试，正式建筑必须人工复核。
3. 运行时路径规划器应读取编译产物，不应直接读取原始 PNG。
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from skimage.morphology import skeletonize


DIRECTIONS = {
    "N": (0, -1),
    "E": (1, 0),
    "S": (0, 1),
    "W": (-1, 0),
}


def save_json(path: Path, obj: object) -> None:
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def crop_to_drawing(gray: np.ndarray, threshold: int, margin: int = 12) -> np.ndarray:
    dark = gray < threshold
    ys, xs = np.where(dark)
    if len(xs) == 0:
        raise ValueError("输入图像中没有检测到墙体，请调整 --wall-threshold。")

    x0 = max(0, int(xs.min()) - margin)
    x1 = min(gray.shape[1], int(xs.max()) + margin + 1)
    y0 = max(0, int(ys.min()) - margin)
    y1 = min(gray.shape[0], int(ys.max()) + margin + 1)
    return gray[y0:y1, x0:x1]


def resize_gray(gray: np.ndarray, size: int) -> np.ndarray:
    image = Image.fromarray(gray.astype(np.uint8), mode="L")
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    return np.asarray(image)


def keep_main_free_component(free_mask: np.ndarray) -> np.ndarray:
    labels, count = ndi.label(free_mask)
    if count == 0:
        raise ValueError("没有检测到可通行区域。")

    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    main_label = int(np.argmax(sizes))
    return labels == main_label


def carve_exit(
    wall: np.ndarray,
    walkable: np.ndarray,
    side: str,
    clearance: np.ndarray,
    gap_width: int = 7,
    search_depth: int = 36,
) -> tuple[int, int]:
    """
    在指定边界自动选择一个内部较开阔的位置，并凿开边界形成出口。
    返回出口锚点 (x, y)。
    """
    h, w = wall.shape
    half = gap_width // 2

    if side in {"TOP", "BOTTOM"}:
        candidates = []
        for x in range(20, w - 20):
            if side == "TOP":
                ys = range(3, min(search_depth, h))
            else:
                ys = range(h - 4, max(h - search_depth, 0), -1)

            found_y = None
            for y in ys:
                if walkable[y, x]:
                    found_y = y
                    break

            if found_y is not None:
                score = float(clearance[found_y, x])
                candidates.append((score, x, found_y))

        if not candidates:
            raise ValueError(f"无法在 {side} 侧生成出口。")

        _, x, y = max(candidates)
        x0, x1 = max(0, x - half), min(w, x + half + 1)

        if side == "TOP":
            wall[0 : y + 1, x0:x1] = False
            walkable[0 : y + 1, x0:x1] = True
            return x, min(y, 4)
        else:
            wall[y:h, x0:x1] = False
            walkable[y:h, x0:x1] = True
            return x, max(y, h - 5)

    candidates = []
    for y in range(20, h - 20):
        if side == "LEFT":
            xs = range(3, min(search_depth, w))
        else:
            xs = range(w - 4, max(w - search_depth, 0), -1)

        found_x = None
        for x in xs:
            if walkable[y, x]:
                found_x = x
                break

        if found_x is not None:
            score = float(clearance[y, found_x])
            candidates.append((score, found_x, y))

    if not candidates:
        raise ValueError(f"无法在 {side} 侧生成出口。")

    _, x, y = max(candidates)
    y0, y1 = max(0, y - half), min(h, y + half + 1)

    if side == "LEFT":
        wall[y0:y1, 0 : x + 1] = False
        walkable[y0:y1, 0 : x + 1] = True
        return min(x, 4), y

    wall[y0:y1, x:w] = False
    walkable[y0:y1, x:w] = True
    return max(x, w - 5), y


def choose_refuges(
    walkable: np.ndarray,
    clearance: np.ndarray,
    exits_xy: list[tuple[int, int]],
    count: int = 2,
    min_separation: int = 90,
) -> list[tuple[int, int]]:
    valid = walkable.copy()
    h, w = valid.shape

    # 避开地图边界和出口附近
    border = 24
    valid[:border, :] = False
    valid[-border:, :] = False
    valid[:, :border] = False
    valid[:, -border:] = False

    yy, xx = np.indices(valid.shape)
    for ex, ey in exits_xy:
        valid &= ((xx - ex) ** 2 + (yy - ey) ** 2) >= (min_separation // 2) ** 2

    selected: list[tuple[int, int]] = []
    score = np.where(valid, clearance, -1.0)

    for _ in range(count):
        if score.max() < 0:
            break
        y, x = np.unravel_index(np.argmax(score), score.shape)
        selected.append((int(x), int(y)))
        score[((xx - x) ** 2 + (yy - y) ** 2) < min_separation**2] = -1.0

    return selected


def skeleton_neighbor_count(skeleton: np.ndarray) -> np.ndarray:
    kernel = np.ones((3, 3), dtype=np.uint8)
    count = ndi.convolve(skeleton.astype(np.uint8), kernel, mode="constant", cval=0)
    return count - skeleton.astype(np.uint8)


def cluster_feature_points(
    skeleton: np.ndarray,
    feature_mask: np.ndarray,
    dilation_iterations: int = 3,
) -> list[tuple[int, int]]:
    expanded = ndi.binary_dilation(feature_mask, iterations=dilation_iterations)
    labels, count = ndi.label(expanded)
    points: list[tuple[int, int]] = []

    for label_id in range(1, count + 1):
        region = labels == label_id
        skeleton_region = region & skeleton
        ys, xs = np.where(skeleton_region)
        if len(xs) == 0:
            continue

        cx = float(xs.mean())
        cy = float(ys.mean())
        idx = int(np.argmin((xs - cx) ** 2 + (ys - cy) ** 2))
        points.append((int(xs[idx]), int(ys[idx])))

    return points


def is_corner_pixel(skeleton: np.ndarray, x: int, y: int) -> bool:
    coords = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            ny, nx = y + dy, x + dx
            if (
                0 <= ny < skeleton.shape[0]
                and 0 <= nx < skeleton.shape[1]
                and skeleton[ny, nx]
            ):
                coords.append((dx, dy))

    if len(coords) != 2:
        return False

    (dx1, dy1), (dx2, dy2) = coords
    # 两向量互为反向时属于直线，不是转弯
    return not (dx1 == -dx2 and dy1 == -dy2)


def nearest_skeleton_point(
    point: tuple[int, int],
    skeleton: np.ndarray,
) -> tuple[int, int]:
    x, y = point
    ys, xs = np.where(skeleton)
    if len(xs) == 0:
        return point
    idx = int(np.argmin((xs - x) ** 2 + (ys - y) ** 2))
    return int(xs[idx]), int(ys[idx])


def generate_black_boxes(
    walkable: np.ndarray,
    exits_xy: list[tuple[int, int]],
    refuges_xy: list[tuple[int, int]],
    spacing_cells: int = 34,
    max_boxes: int = 80,
) -> tuple[np.ndarray, list[dict]]:
    skeleton = skeletonize(walkable)
    neighbor_count = skeleton_neighbor_count(skeleton)

    junction_mask = skeleton & (neighbor_count >= 3)

    corner_mask = np.zeros_like(skeleton)
    ys, xs = np.where(skeleton & (neighbor_count == 2))
    for y, x in zip(ys.tolist(), xs.tolist()):
        if is_corner_pixel(skeleton, x, y):
            corner_mask[y, x] = True

    junctions = cluster_feature_points(skeleton, junction_mask, 3)
    corners = cluster_feature_points(skeleton, corner_mask, 2)

    exit_anchor_points = [
        nearest_skeleton_point(p, skeleton) for p in exits_xy
    ]
    refuge_anchor_points = [
        nearest_skeleton_point(p, skeleton) for p in refuges_xy
    ]

    # 按安全重要性加入候选点。路口、转角骨架可能包含连续像素，
    # 通过最小距离过滤，避免同一物理位置生成多个黑盒。
    selected: list[tuple[int, int]] = []
    seen = set()

    def add_point(point: tuple[int, int], min_distance: float) -> None:
        x, y = point
        if point in seen or not walkable[y, x]:
            return
        if selected:
            nearest = min(math.hypot(x - px, y - py) for px, py in selected)
            if nearest < min_distance:
                return
        selected.append(point)
        seen.add(point)

    # 出口和避难点优先，允许相对接近
    for point in exit_anchor_points:
        add_point(point, min_distance=4.0)
    for point in refuge_anchor_points:
        add_point(point, min_distance=6.0)

    # 路口优先级高于普通转角
    for point in junctions:
        add_point(point, min_distance=11.0)
    for point in corners:
        add_point(point, min_distance=13.0)

    # 若强制候选仍过多，则保留前 max_boxes 个稳定排序点
    if len(selected) > max_boxes:
        selected = sorted(selected, key=lambda p: (p[1], p[0]))[:max_boxes]
        seen = set(selected)

    selected_mask = np.zeros_like(skeleton)
    for x, y in selected:
        selected_mask[y, x] = True

    # 最远点采样：补足长距离无黑盒区域
    while len(selected) < max_boxes:
        if not selected:
            ys, xs = np.where(skeleton)
            if len(xs) == 0:
                break
            x, y = int(xs[0]), int(ys[0])
            selected.append((x, y))
            selected_mask[y, x] = True

        distance_to_selected = ndi.distance_transform_edt(~selected_mask)
        score = np.where(skeleton, distance_to_selected, -1.0)
        y, x = np.unravel_index(np.argmax(score), score.shape)
        max_distance = float(score[y, x])

        if max_distance <= spacing_cells:
            break

        selected.append((int(x), int(y)))
        selected_mask[y, x] = True

    # 稳定排序，便于版本管理
    selected.sort(key=lambda p: (p[1], p[0]))

    boxes = []
    box_map = np.zeros_like(walkable, dtype=np.int32)

    junction_set = set(junctions)
    corner_set = set(corners)

    for index, (x, y) in enumerate(selected, start=1):
        if (x, y) in junction_set:
            role = "JUNCTION"
            mandatory_flag = True
        elif (x, y) in corner_set:
            role = "CORNER"
            mandatory_flag = True
        elif (x, y) in exit_anchor_points:
            role = "EXIT_GUIDE"
            mandatory_flag = True
        elif (x, y) in refuge_anchor_points:
            role = "REFUGE_GUIDE"
            mandatory_flag = True
        else:
            role = "CORRIDOR_SUPPORT"
            mandatory_flag = False

        box_id = f"B{index:03d}"
        boxes.append(
            {
                "box_id": box_id,
                "floor": "F01",
                "grid_x": x,
                "grid_y": y,
                "role": role,
                "mandatory": mandatory_flag,
                "sensor_radius_m": 5.0,
                "visibility_radius_m": 7.0,
                "orientation": "AUTO",
                "source": "AUTO_TEST_GENERATOR",
                "requires_manual_review": True,
            }
        )
        box_map[y, x] = index

    return box_map, boxes


def save_binary_png(path: Path, mask: np.ndarray, foreground_white: bool) -> None:
    if foreground_white:
        data = np.where(mask, 255, 0).astype(np.uint8)
    else:
        data = np.where(mask, 0, 255).astype(np.uint8)
    Image.fromarray(data, mode="L").save(path)


def make_preview(
    path: Path,
    wall: np.ndarray,
    boxes: list[dict],
    exits: list[dict],
    refuges: list[dict],
) -> None:
    base = np.full((*wall.shape, 3), 255, dtype=np.uint8)
    base[wall] = np.array([35, 35, 35], dtype=np.uint8)
    image = Image.fromarray(base, mode="RGB")
    draw = ImageDraw.Draw(image)

    for refuge in refuges:
        x, y = refuge["grid_x"], refuge["grid_y"]
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(255, 196, 0), outline=(150, 100, 0))

    for box in boxes:
        x, y = box["grid_x"], box["grid_y"]
        color = (30, 110, 230) if box["mandatory"] else (0, 170, 200)
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)

    for exit_item in exits:
        x, y = exit_item["grid_x"], exit_item["grid_y"]
        draw.rectangle((x - 6, y - 6, x + 6, y + 6), fill=(20, 180, 60), outline=(0, 80, 20))

    image.save(path)


def validate_package(
    wall: np.ndarray,
    walkable: np.ndarray,
    box_map: np.ndarray,
    exit_map: np.ndarray,
    refuge_map: np.ndarray,
) -> dict:
    errors = []
    warnings = []

    if wall.shape != walkable.shape:
        errors.append("MAP_E100: 墙体与可通行数组形状不一致。")

    if np.any(wall & walkable):
        errors.append("MAP_E101: M_wall 与 M_walkable 存在重叠。")

    if np.any((box_map > 0) & ~walkable):
        errors.append("MAP_E102: 存在位于不可通行单元上的小黑盒。")

    if np.any((exit_map > 0) & ~walkable):
        errors.append("MAP_E103: 存在位于不可通行单元上的出口。")

    if np.any((refuge_map > 0) & ~walkable):
        errors.append("MAP_E104: 存在位于不可通行单元上的避难点。")

    labels, component_count = ndi.label(walkable)
    exit_labels = set(labels[exit_map > 0].tolist())
    exit_labels.discard(0)

    unreachable_box_count = 0
    for label_id in np.unique(labels[box_map > 0]):
        if label_id == 0 or label_id not in exit_labels:
            unreachable_box_count += int(np.sum((box_map > 0) & (labels == label_id)))

    if unreachable_box_count:
        errors.append(
            f"MAP_E105: 有 {unreachable_box_count} 个小黑盒所在连通域无法到达任一出口。"
        )

    if component_count > 1:
        warnings.append(
            f"MAP_W001: 可通行区域包含 {component_count} 个连通域；"
            "测试包已尽量保留主连通域，正式地图需人工确认。"
        )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "statistics": {
            "height": int(wall.shape[0]),
            "width": int(wall.shape[1]),
            "wall_cells": int(wall.sum()),
            "walkable_cells": int(walkable.sum()),
            "black_box_count": int(np.count_nonzero(box_map)),
            "exit_count": int(exit_map.max()),
            "refuge_count": int(refuge_map.max()),
            "walkable_component_count": int(component_count),
        },
    }


def build_map_package(
    input_path: Path,
    output_dir: Path,
    size: int = 512,
    resolution: float = 0.10,
    wall_threshold: int = 205,
    wall_dilation: int = 0,
    box_spacing_cells: int = 34,
    max_boxes: int = 80,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    floors_dir = output_dir / "floors"
    floors_dir.mkdir(exist_ok=True)

    original = Image.open(input_path).convert("L")
    gray = np.asarray(original)
    cropped = crop_to_drawing(gray, threshold=wall_threshold)
    resized = resize_gray(cropped, size=size)

    # 深色像素视为墙体
    wall = resized < wall_threshold
    # 删除极小深色噪点，但保留真实墙体和短墙段
    labels, component_count = ndi.label(wall)
    if component_count > 0:
        sizes = np.bincount(labels.ravel())
        keep_ids = np.where(sizes >= 4)[0]
        keep_ids = keep_ids[keep_ids != 0]
        wall = np.isin(labels, keep_ids)

    if wall_dilation > 0:
        wall = ndi.binary_dilation(wall, iterations=wall_dilation)

    # 加入封闭边框，之后仅在指定位置开出口
    border = 3
    wall[:border, :] = True
    wall[-border:, :] = True
    wall[:, :border] = True
    wall[:, -border:] = True

    free = ~wall
    walkable = keep_main_free_component(free)

    # 将主连通域之外的区域视为障碍
    wall = ~walkable

    clearance_cells = ndi.distance_transform_edt(walkable)
    clearance_m = (clearance_cells * resolution).astype(np.float32)

    exit_points = []
    for side in ("TOP", "RIGHT", "BOTTOM", "LEFT"):
        point = carve_exit(
            wall=wall,
            walkable=walkable,
            side=side,
            clearance=clearance_cells,
            gap_width=7,
        )
        exit_points.append(point)

    # 重新计算连通域，确保开口后的主区域一致
    walkable = keep_main_free_component(~wall)
    wall = ~walkable
    clearance_cells = ndi.distance_transform_edt(walkable)
    clearance_m = (clearance_cells * resolution).astype(np.float32)

    exits = []
    exit_map = np.zeros_like(walkable, dtype=np.int16)
    for index, (x, y) in enumerate(exit_points, start=1):
        # 吸附到最近可通行单元
        if not walkable[y, x]:
            yy, xx = np.where(walkable)
            nearest = int(np.argmin((xx - x) ** 2 + (yy - y) ** 2))
            x, y = int(xx[nearest]), int(yy[nearest])

        exit_id = f"E{index:02d}"
        exits.append(
            {
                "exit_id": exit_id,
                "floor": "F01",
                "grid_x": x,
                "grid_y": y,
                "world_x_m": round(x * resolution, 3),
                "world_y_m": round(y * resolution, 3),
                "enabled": True,
                "exit_type": "GROUND_EXIT",
                "source": "AUTO_TEST_GENERATOR",
                "requires_manual_review": True,
            }
        )
        y0, y1 = max(0, y - 2), min(size, y + 3)
        x0, x1 = max(0, x - 2), min(size, x + 3)
        region_walkable = walkable[y0:y1, x0:x1]
        exit_region = exit_map[y0:y1, x0:x1]
        exit_region[region_walkable] = index

    refuge_points = choose_refuges(
        walkable=walkable,
        clearance=clearance_cells,
        exits_xy=[(e["grid_x"], e["grid_y"]) for e in exits],
        count=2,
    )

    refuges = []
    refuge_map = np.zeros_like(walkable, dtype=np.int16)
    for index, (x, y) in enumerate(refuge_points, start=1):
        refuge_id = f"R{index:02d}"
        refuges.append(
            {
                "refuge_id": refuge_id,
                "floor": "F01",
                "grid_x": x,
                "grid_y": y,
                "world_x_m": round(x * resolution, 3),
                "world_y_m": round(y * resolution, 3),
                "capacity": 20,
                "enabled": True,
                "source": "AUTO_TEST_GENERATOR",
                "requires_manual_review": True,
            }
        )
        y0, y1 = max(0, y - 3), min(size, y + 4)
        x0, x1 = max(0, x - 3), min(size, x + 4)
        region_walkable = walkable[y0:y1, x0:x1]
        refuge_region = refuge_map[y0:y1, x0:x1]
        refuge_region[region_walkable] = index

    box_map, boxes = generate_black_boxes(
        walkable=walkable,
        exits_xy=[(e["grid_x"], e["grid_y"]) for e in exits],
        refuges_xy=[(r["grid_x"], r["grid_y"]) for r in refuges],
        spacing_cells=box_spacing_cells,
        max_boxes=max_boxes,
    )

    for box in boxes:
        box["world_x_m"] = round(box["grid_x"] * resolution, 3)
        box["world_y_m"] = round(box["grid_y"] * resolution, 3)

    # MVP：火灾传播域暂时与人员可通行域一致
    fire_domain = walkable.copy()

    # 材质编码：0=自由空间，1=墙体
    material = np.zeros_like(walkable, dtype=np.uint8)
    material[wall] = 1

    # 原图、墙体和通行图
    Image.fromarray(resized.astype(np.uint8), mode="L").save(floors_dir / "F01_base.png")
    save_binary_png(floors_dir / "F01_wall.png", wall, foreground_white=False)
    save_binary_png(floors_dir / "F01_walkable.png", walkable, foreground_white=True)
    np.save(floors_dir / "F01_material.npy", material)

    semantic = {
        "floor_id": "F01",
        "shape": [int(size), int(size)],
        "coordinate_origin": "top_left",
        "x_axis": "east",
        "y_axis": "south",
        "layer_files": {
            "base_image": "F01_base.png",
            "wall_image": "F01_wall.png",
            "walkable_image": "F01_walkable.png",
            "material_array": "F01_material.npy",
        },
        "material_codes": {
            "0": "FREE_SPACE",
            "1": "WALL_NONCOMBUSTIBLE",
        },
    }
    save_json(floors_dir / "F01_semantic.json", semantic)

    # 根目录运行时数组，便于现有 backend/data 风格直接读取
    np.save(output_dir / "M_wall.npy", wall.astype(bool))
    np.save(output_dir / "M_walkable.npy", walkable.astype(bool))
    np.save(output_dir / "M_fire_domain.npy", fire_domain.astype(bool))
    np.save(output_dir / "M_material.npy", material)
    np.save(output_dir / "M_clearance.npy", clearance_m)
    np.save(output_dir / "M_box.npy", box_map)
    np.save(output_dir / "M_exit.npy", exit_map)
    np.save(output_dir / "M_refuge.npy", refuge_map)

    save_json(output_dir / "exits.json", exits)
    save_json(output_dir / "refuges.json", refuges)
    save_json(output_dir / "black_boxes.manual.json", boxes)
    save_json(output_dir / "doors.json", [])
    save_json(output_dir / "stairs.json", [])
    save_json(output_dir / "vents.json", [])
    save_json(
        output_dir / "gateways.json",
        [
            {
                "gateway_id": "GW01",
                "floor": "F01",
                "grid_x": size // 2,
                "grid_y": size // 2,
                "enabled": True,
                "source": "AUTO_TEST_GENERATOR",
            }
        ],
    )

    map_config = {
        "map_id": "test_floorplan_from_map2",
        "map_version": "1.0.0",
        "resolution_m_per_cell": float(resolution),
        "coordinate_origin": "top_left",
        "x_axis": "east",
        "y_axis": "south",
        "floors": ["F01"],
        "default_unit": "meter",
        "grid_shape": [size, size],
        "source_image": input_path.name,
        "generated_for": "fire_escape_system",
        "automatic_result_requires_manual_review": True,
    }
    (output_dir / "map_config.yaml").write_text(
        yaml.safe_dump(map_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    placement_config = {
        "mode": "AUTO_WITH_MANUAL_REVIEW",
        "box_spacing_cells": int(box_spacing_cells),
        "max_boxes": int(max_boxes),
        "mandatory_roles": [
            "JUNCTION",
            "CORNER",
            "EXIT_GUIDE",
            "REFUGE_GUIDE",
        ],
        "n_minus_one_validation": False,
    }
    (output_dir / "placement_config.yaml").write_text(
        yaml.safe_dump(placement_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    hazard_config = {
        "mvp_fire_domain_equals_walkable": True,
        "fire_max": 100.0,
        "local_growth_rate": 0.12,
        "spread_rate": 0.08,
        "smoke_model_enabled": False,
        "temperature_model_enabled": False,
        "requires_calibration": True,
    }
    (output_dir / "hazard_config.yaml").write_text(
        yaml.safe_dump(hazard_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    planner_config = {
        "planner_mode": "MULTI_SOURCE_REVERSE",
        "distance_weight": 1.0,
        "risk_weight": 8.0,
        "turn_penalty": 2.0,
        "uturn_penalty": 6.0,
        "fatal_risk_threshold": 85.0,
        "switch_margin": 0.15,
        "requires_calibration": True,
    }
    (output_dir / "planner_config.yaml").write_text(
        yaml.safe_dump(planner_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    validation_report = validate_package(
        wall=wall,
        walkable=walkable,
        box_map=box_map,
        exit_map=exit_map,
        refuge_map=refuge_map,
    )
    save_json(output_dir / "validation_report.json", validation_report)

    compiled_metadata = {
        "map_id": map_config["map_id"],
        "map_version": map_config["map_version"],
        "resolution_m_per_cell": resolution,
        "shape": [size, size],
        "coordinate_system": {
            "origin": "top_left",
            "x_axis": "east",
            "y_axis": "south",
        },
        "array_keys": [
            "M_wall",
            "M_walkable",
            "M_fire_domain",
            "M_material",
            "M_clearance",
            "M_box",
            "M_exit",
            "M_refuge",
        ],
        "entity_files": {
            "exits": "exits.json",
            "refuges": "refuges.json",
            "black_boxes": "black_boxes.manual.json",
            "doors": "doors.json",
            "stairs": "stairs.json",
            "vents": "vents.json",
            "gateways": "gateways.json",
        },
        "validation_valid": validation_report["valid"],
        "warning": "自动生成测试地图，正式部署前必须人工复核墙体、门、出口和黑盒位置。",
    }
    save_json(output_dir / "compiled_map.json", compiled_metadata)

    np.savez_compressed(
        output_dir / "compiled_map.npz",
        M_wall=wall.astype(bool),
        M_walkable=walkable.astype(bool),
        M_fire_domain=fire_domain.astype(bool),
        M_material=material,
        M_clearance=clearance_m,
        M_box=box_map,
        M_exit=exit_map,
        M_refuge=refuge_map,
    )

    make_preview(
        output_dir / "preview.png",
        wall=wall,
        boxes=boxes,
        exits=exits,
        refuges=refuges,
    )

    print(f"地图包已生成：{output_dir}")
    print(json.dumps(validation_report, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成消防逃生系统测试地图包")
    parser.add_argument("--input", required=True, type=Path, help="输入平面图")
    parser.add_argument("--output", required=True, type=Path, help="输出地图包目录")
    parser.add_argument("--size", type=int, default=512, help="输出网格宽高")
    parser.add_argument(
        "--resolution",
        type=float,
        default=0.10,
        help="每个网格对应的实际米数",
    )
    parser.add_argument(
        "--wall-threshold",
        type=int,
        default=205,
        help="灰度低于该值视为墙体",
    )
    parser.add_argument(
        "--wall-dilation",
        type=int,
        default=0,
        help="额外膨胀墙体的像素数",
    )
    parser.add_argument(
        "--box-spacing-cells",
        type=int,
        default=34,
        help="中心线补点最大间距",
    )
    parser.add_argument(
        "--max-boxes",
        type=int,
        default=80,
        help="自动生成小黑盒数量上限",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_map_package(
        input_path=args.input,
        output_dir=args.output,
        size=args.size,
        resolution=args.resolution,
        wall_threshold=args.wall_threshold,
        wall_dilation=args.wall_dilation,
        box_spacing_cells=args.box_spacing_cells,
        max_boxes=args.max_boxes,
    )
