# 测试地图包导入说明

## 1. 为什么不能只导入一张 PNG

路径规划运行时需要明确区分墙体、可通行区域、出口、避难点和小黑盒。
原始 PNG 只用于地图编译阶段，运行服务应读取：

- `compiled_map.npz`：所有同尺寸 NumPy 数组；
- `compiled_map.json`：数组键和实体文件说明；
- `map_config.yaml`：地图版本、比例尺和坐标系；
- `exits.json`、`refuges.json`、`black_boxes.manual.json`：实体坐标。

## 2. 本地图包包含的核心输入

| 文件 | 作用 |
|---|---|
| `M_wall.npy` | 墙体和不可通行区域 |
| `M_walkable.npy` | 人员可通行区域 |
| `M_fire_domain.npy` | 火灾传播域；MVP 暂与可通行域一致 |
| `M_material.npy` | 材质编码 |
| `M_clearance.npy` | 到最近墙体的实际距离，单位米 |
| `M_box.npy` | 小黑盒编号栅格 |
| `M_exit.npy` | 出口编号栅格 |
| `M_refuge.npy` | 避难点编号栅格 |
| `compiled_map.npz` | 上述数组的压缩集合 |
| `preview.png` | 墙体、黑盒、出口和避难点预览 |

## 3. 放入项目的推荐位置

```text
fire_escape_system/
├─ backend/
│  └─ app/
└─ maps/
   └─ test_floorplan_from_map2/
      ├─ compiled_map.npz
      ├─ compiled_map.json
      ├─ map_config.yaml
      ├─ exits.json
      ├─ refuges.json
      ├─ black_boxes.manual.json
      └─ ...
```

将整个 `test_floorplan_map_package` 目录复制到：

```text
fire_escape_system/maps/test_floorplan_from_map2
```

## 4. 后端加载

复制 `load_compiled_map.py` 到项目中，或者将其中的
`load_map_package()` 合并到：

```text
backend/app/services/map_initializer.py
```

示例：

```python
from pathlib import Path
from load_compiled_map import load_map_package

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAP_DIR = PROJECT_ROOT / "maps" / "test_floorplan_from_map2"

compiled_map = load_map_package(MAP_DIR)

M_wall = compiled_map.arrays["M_wall"]
M_walkable = compiled_map.arrays["M_walkable"]
M_fire_domain = compiled_map.arrays["M_fire_domain"]
M_clearance = compiled_map.arrays["M_clearance"]

boxes = compiled_map.black_boxes
exits = compiled_map.exits
refuges = compiled_map.refuges
```

## 5. 兼容旧的 `backend/data` 读取方式

如果你们当前代码仍然写死：

```python
np.load("backend/data/M_walkable.npy")
```

可以先把地图包根目录中的以下文件复制到 `backend/data/`：

```text
M_wall.npy
M_walkable.npy
M_fire_domain.npy
M_material.npy
M_clearance.npy
M_box.npy
M_exit.npy
M_refuge.npy
```

但正式版本应改为通过 `map_id` 加载整个地图包，避免更换地图时覆盖旧文件。

## 6. 重新生成

安装依赖：

```powershell
pip install -r requirements_map_generator.txt
```

执行：

```powershell
python generate_test_map_package.py `
  --input map2.png `
  --output .\maps\test_floorplan_from_map2 `
  --size 512 `
  --resolution 0.10
```

常用参数：

```text
--wall-threshold 205       深色像素识别阈值
--wall-dilation 0          墙体额外膨胀像素数
--box-spacing-cells 34     自动补点间距
--max-boxes 80             黑盒数量上限
```

## 7. 必须人工复核的内容

自动图像转换只适用于测试，不应直接作为真实消防部署依据。至少检查：

1. 墙体是否完整；
2. 门洞是否被误封；
3. 出口是否真实存在；
4. 避难点是否符合建筑设计；
5. 小黑盒是否位于可安装位置；
6. 烟雾传播域是否应穿过门、通风口或楼梯；
7. 地图比例尺是否真实；
8. 所有黑盒是否最终能够到达出口或避难点。
