# backend/core/lbb_manager.py
import math
import numpy as np
from collections import deque
from . import config

class LBBManager:
    def __init__(self, black_boxes: list, mask_matrix: np.ndarray, physical_exits: list):
        """
        小黑盒硬件阵列的边缘感知管理器
        """
        self.black_boxes = black_boxes
        self.mask_matrix = mask_matrix
        self.physical_exits = physical_exits
        self.width, self.height = mask_matrix.shape
        
        # MCU 硬件唤醒阈值 (信息因子/烟雾浓度)
        self.T_WAKE = 15.0
        
        # 冷启动时自动构建全量宏观静态拓扑图 (视野连线机制)
        self.topology_graph = self._build_static_topology()

    def _check_los(self, x0: int, y0: int, x1: int, y1: int) -> bool:
        """
        严谨的 DDA (Digital Differential Analyzer) 视野检测算法。
        严格断言切角遮挡情况，彻底杜绝图论连线“穿透”墙角的 Bug。
        """
        x, y = x0, y0
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        
        while True:
            if self.mask_matrix[x, y] == 0:
                return False
            if x == x1 and y == y1:
                break
                
            e2 = 2 * err
            move_x = False
            move_y = False
            
            if e2 >= -dy:
                err -= dy
                x += sx
                move_x = True
            if e2 <= dx:
                err += dx
                y += sy
                move_y = True
                
            # 切角熔断判定：如果正在走对角线，需校验相邻的两个水平/垂直网格是否为墙。
            # 若任意一边为墙，则属于贴墙切角，视野物理中断。
            if move_x and move_y:
                if self.mask_matrix[x - sx, y] == 0 or self.mask_matrix[x, y - sy] == 0:
                    return False
                    
        return True

    def _build_static_topology(self) -> dict:
        """
        基于象限投影与正方向极性仲裁的静态图构建引擎。
        提取完全畅通的物理连线，并根据夹角偏差与距离，分配唯一的邻接矩阵边缘。
        """
        topology_graph = {}
        target_nodes = self.black_boxes + self.physical_exits
        MAX_SIGHT_RANGE = 80.0  # 物理视野截断极值
        
        for ax, ay in self.black_boxes:
            topology_graph[(ax, ay)] = {0: None, 1: None, 2: None, 3: None}
            
            # 存储四个方向的候选列表: idx -> list of (deviation, distance, (bx, by))
            candidates = {0: [], 1: [], 2: [], 3: []}
            
            for bx, by in target_nodes:
                if ax == bx and ay == by:
                    continue
                    
                distance = math.hypot(bx - ax, by - ay)
                if distance > MAX_SIGHT_RANGE:
                    continue
                    
                # 连通性断言
                if not self._check_los(ax, ay, bx, by):
                    continue
                    
                # 相对坐标转化为物理极坐标
                dx = bx - ax
                dy = by - ay
                deg = math.degrees(math.atan2(dy, dx))
                
                # 45度象限切分与夹角偏差计算
                if -45 <= deg <= 45:
                    dir_idx = 1  # DIR_E
                    deviation = abs(deg)
                elif 45 < deg <= 135:
                    dir_idx = 2  # DIR_S
                    deviation = abs(deg - 90)
                elif -135 <= deg < -45:
                    dir_idx = 0  # DIR_N
                    deviation = abs(deg - (-90))
                else:
                    dir_idx = 3  # DIR_W
                    deviation = abs(abs(deg) - 180)
                    
                candidates[dir_idx].append((deviation, distance, (bx, by)))
                
            # 在同一象限内，执行极性仲裁：优先选取最贴近正方向的节点；若极性一致，则选距离最近的节点。
            for dir_idx in range(4):
                if candidates[dir_idx]:
                    candidates[dir_idx].sort(key=lambda item: (item[0], item[1]))
                    best_target = candidates[dir_idx][0][2]
                    topology_graph[(ax, ay)][dir_idx] = best_target
                    
        return topology_graph

    def scan_and_cluster(self, smoke_matrix: np.ndarray) -> list:
        alert_boxes = []
        for bx, by in self.black_boxes:
            if self.mask_matrix[bx, by] == 1:
                smoke_val = smoke_matrix[bx, by]
                if smoke_val > self.T_WAKE:
                    alert_boxes.append((bx, by, smoke_val))
                    
        if not alert_boxes:
            return []
            
        clusters = self._topological_clustering(alert_boxes)
        centroids = self._calculate_centroids(clusters)
        return centroids

    def _topological_clustering(self, alert_boxes: list) -> list:
        clusters = []
        alert_dict = {(bx, by): val for bx, by, val in alert_boxes}
        unvisited = set(alert_dict.keys())
        
        MAX_DEPTH = 30 
        dx = [0, 1, 0, -1]
        dy = [-1, 0, 1, 0]
        
        while unvisited:
            start_node = unvisited.pop()
            current_cluster = [(start_node[0], start_node[1], alert_dict[start_node])]
            
            queue = deque([(start_node[0], start_node[1], 0)])
            local_visited = {start_node}
            
            while queue:
                cx, cy, depth = queue.popleft()
                
                if depth >= MAX_DEPTH:
                    continue
                    
                for i in range(4):
                    nx = cx + dx[i]
                    ny = cy + dy[i]
                    
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        if self.mask_matrix[nx, ny] == 1 and (nx, ny) not in local_visited:
                            local_visited.add((nx, ny))
                            queue.append((nx, ny, depth + 1))
                            
                            if (nx, ny) in unvisited:
                                unvisited.remove((nx, ny))
                                current_cluster.append((nx, ny, alert_dict[(nx, ny)]))
                                
            clusters.append(current_cluster)
            
        return clusters

    def _calculate_centroids(self, clusters: list) -> list:
        centroids = []
        for cluster in clusters:
            sum_x = 0.0
            sum_y = 0.0
            sum_weight = 0.0
            
            for bx, by, weight in cluster:
                sum_x += bx * weight
                sum_y += by * weight
                sum_weight += weight
                
            if sum_weight > 0:
                cx = int(round(sum_x / sum_weight))
                cy = int(round(sum_y / sum_weight))
                centroids.append((cx, cy))
                
        return centroids