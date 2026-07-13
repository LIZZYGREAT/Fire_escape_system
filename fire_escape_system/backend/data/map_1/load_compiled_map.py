#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""消防逃生系统标准地图包加载示例。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml


@dataclass(frozen=True)
class LoadedMap:
    map_id: str
    map_version: str
    resolution: float
    arrays: dict[str, np.ndarray]
    exits: list[dict]
    refuges: list[dict]
    black_boxes: list[dict]


def load_map_package(map_dir: str | Path) -> LoadedMap:
    map_dir = Path(map_dir)
    if not map_dir.is_dir():
        raise FileNotFoundError(f"地图包目录不存在：{map_dir}")

    config_path = map_dir / "map_config.yaml"
    npz_path = map_dir / "compiled_map.npz"

    if not config_path.exists():
        raise FileNotFoundError(f"缺少地图配置：{config_path}")
    if not npz_path.exists():
        raise FileNotFoundError(f"缺少编译数组：{npz_path}")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    with np.load(npz_path, allow_pickle=False) as data:
        arrays = {key: data[key] for key in data.files}

    required = {
        "M_wall",
        "M_walkable",
        "M_fire_domain",
        "M_material",
        "M_clearance",
        "M_box",
        "M_exit",
        "M_refuge",
    }
    missing = required - arrays.keys()
    if missing:
        raise ValueError(f"compiled_map.npz 缺少数组：{sorted(missing)}")

    shapes = {arr.shape for arr in arrays.values()}
    if len(shapes) != 1:
        raise ValueError(f"地图数组形状不一致：{shapes}")

    if np.any(arrays["M_wall"] & arrays["M_walkable"]):
        raise ValueError("M_wall 与 M_walkable 发生重叠。")

    def read_json(name: str) -> list[dict]:
        return json.loads((map_dir / name).read_text(encoding="utf-8"))

    return LoadedMap(
        map_id=config["map_id"],
        map_version=config["map_version"],
        resolution=float(config["resolution_m_per_cell"]),
        arrays=arrays,
        exits=read_json("exits.json"),
        refuges=read_json("refuges.json"),
        black_boxes=read_json("black_boxes.manual.json"),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("map_dir", type=Path)
    args = parser.parse_args()

    loaded = load_map_package(args.map_dir)
    print(f"map_id: {loaded.map_id}")
    print(f"version: {loaded.map_version}")
    print(f"shape: {loaded.arrays['M_walkable'].shape}")
    print(f"resolution: {loaded.resolution} m/cell")
    print(f"boxes: {len(loaded.black_boxes)}")
    print(f"exits: {len(loaded.exits)}")
    print(f"refuges: {len(loaded.refuges)}")
