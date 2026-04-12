import numpy as np
import math
from scipy.ndimage import distance_transform_edt

# 1. 原始 2048 比例尺坐标
raw_points = [
    (770,400),(321,766),(194,38),(39,408),(617,618),(616,506),(609,411),
    (613,342),(611,262),(613,188),(519,188),(428,613),(423,485),(429,418),
    (427,331),(408,187),(545,618),(449,734),(326,734),(313,703),(314,531),
    (317,406),(314,295),(313,190),(191,715),(194,614),(196,613),(193,518),
    (193,405),(197,291),(197,188),(197,67)
]

# 2. 映射参数
OLD_SCALE = 820
NEW_SCALE = 250

def map_and_align(points, mask_path='M_mask.npy'):
    # 加载掩码矩阵，用于校准
    mask = np.load(mask_path)
    # 计算距离场：值越大代表离墙越远
    edt = distance_transform_edt(mask == 1)
    
    final_coords = []
    for x, y in points:
        # 基础线性缩放
        nx = int(round(x * NEW_SCALE / OLD_SCALE))
        ny = int(round(y * NEW_SCALE / OLD_SCALE))
        
        # 局部校准：在 3x3 范围内寻找“最中央”的点（EDT最大点）
        best_coord = (nx, ny)
        max_dist = -1
        
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                cx, cy = nx + dx, ny + dy
                if 0 <= cx < NEW_SCALE and 0 <= cy < NEW_SCALE:
                    if edt[cx, cy] > max_dist:
                        max_dist = edt[cx, cy]
                        best_coord = (cx, cy)
        
        final_coords.append(best_coord)
    
    return final_coords

# 执行转换并打印格式化的 INITIAL_BLACK_BOXES
aligned_results = map_and_align(raw_points)
print("INITIAL_BLACK_BOXES = [")
for i, (x, y) in enumerate(aligned_results):
    comma = "," if i < len(aligned_results) - 1 else ""
    print(f"    ({x}, {y}){comma}")
print("]")