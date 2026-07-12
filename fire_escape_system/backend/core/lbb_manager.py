# backend/core/lbb_manager.py
import math
import numpy as np
from collections import defaultdict, deque
from . import config

class LBBManager:
    def __init__(self, black_boxes: list, mask_matrix: np.ndarray, physical_exits: list):
        self.black_boxes = black_boxes
        self.mask_matrix = mask_matrix
        self.physical_exits = physical_exits
        self.width, self.height = mask_matrix.shape
        
        self.T_WAKE = 15.0
        # 射线膨胀半径（模拟人体的物理逃生宽度，1 代表 3x3 九宫格）
        self.beam_radius = 1 
        
        # 抽象宏观拓扑图：node_id -> {direction: target_node_id}
        self.topology_graph = {}
        
        # 边数据总线：edge_id(u, v) -> 运行时状态字典
        self.edges_data = {}
        
        # 核心性能枢纽：空间倒排索引。pixel(x, y) -> 途径该像素及其膨胀邻域的所有宏观边 edge_id
        self.pixel_to_edges = defaultdict(set)
        
        # 引擎点火初始化
        self._build_static_topology()
        self._rasterize_and_index_edges()

    def _check_los_and_get_path(self, x0: int, y0: int, x1: int, y1: int) -> tuple[bool, list]:
        """
        严谨的 DDA 算法，不仅检测碰撞，同时返回连续的像素路径轨迹
        """
        path = []
        x, y = x0, y0
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        
        while True:
            if self.mask_matrix[x, y] == 0:
                return False, []
                
            path.append((x, y))
            
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
                
            # 切角熔断判定
            if move_x and move_y:
                if self.mask_matrix[x - sx, y] == 0 or self.mask_matrix[x, y - sy] == 0:
                    return False, []
                    
        return True, path

    def _build_static_topology(self) -> dict:
        """
        基于绝对欧几里得距离的静态图构建引擎，彻底废弃易导致“舍近求远”的偏角判定陷阱。
        """
        target_nodes = self.black_boxes + self.physical_exits
        MAX_SIGHT_RANGE = 80.0 
        
        for ax, ay in self.black_boxes:
            self.topology_graph[(ax, ay)] = {0: None, 1: None, 2: None, 3: None}
            candidates = {0: [], 1: [], 2: [], 3: []}
            
            for bx, by in target_nodes:
                if ax == bx and ay == by:
                    continue
                    
                # 物理绝对距离，作为仲裁唯一真理
                euclidean_dist = math.hypot(bx - ax, by - ay)
                if euclidean_dist > MAX_SIGHT_RANGE:
                    continue
                    
                # 45度象限切分
                dx = bx - ax
                dy = by - ay
                deg = math.degrees(math.atan2(dy, dx))
                
                if -45 <= deg <= 45:
                    dir_idx = 1
                elif 45 < deg <= 135:
                    dir_idx = 2
                elif -135 <= deg < -45:
                    dir_idx = 0
                else:
                    dir_idx = 3
                    
                candidates[dir_idx].append((euclidean_dist, (bx, by)))
                
            for dir_idx in range(4):
                if candidates[dir_idx]:
                    # 极性仲裁：在可见象限内，只认欧几里得距离最近的节点
                    candidates[dir_idx].sort(key=lambda item: item[0])
                    for dist, target_coord in candidates[dir_idx]:
                        is_clear, _ = self._check_los_and_get_path(ax, ay, target_coord[0], target_coord[1])
                        if is_clear:
                            self.topology_graph[(ax, ay)][dir_idx] = target_coord
                            break

    def _rasterize_and_index_edges(self):
        """
        边栅格化与倒排索引构建。将所有宏观边投影至物理底图，并为 DStarLite 铺设初始通行代价。
        """
        for ax, ay in self.black_boxes:
            for dir_idx, target in self.topology_graph[(ax, ay)].items():
                if target:
                    is_clear, path = self._check_los_and_get_path(ax, ay, target[0], target[1])
                    if is_clear:
                        edge_id = ((ax, ay), target)
                        # 初始无火灾时，代价退化为切比雪夫物理距离
                        base_cost = len(path) * config.W_BASE
                        
                        self.edges_data[edge_id] = {
                            'path': path,
                            'base_cost': base_cost,
                            'dynamic_cost': base_cost,
                            'is_lethal': False
                        }
                        
                        # 构建宽射线倒排索引：将轨迹及其膨胀半径内的所有像素都挂靠到该宏观边上
                        for px, py in path:
                            for ox in range(-self.beam_radius, self.beam_radius + 1):
                                for oy in range(-self.beam_radius, self.beam_radius + 1):
                                    nx, ny = px + ox, py + oy
                                    if 0 <= nx < self.width and 0 <= ny < self.height:
                                        self.pixel_to_edges[(nx, ny)].add(edge_id)

    def compute_edge_cost(self, edge_id: tuple, w_base_matrix: np.ndarray) -> tuple[float, bool]:
        """
        宽射线连续积分引擎（The Thick Ray Cost Collapse Formula）
        根据最新的物理场，沿 DDA 路径扫描最大极值并积分。
        """
        edge_info = self.edges_data[edge_id]
        path = edge_info['path']
        
        total_cost = 0.0
        is_lethal = False
        
        for px, py in path:
            step_max_risk = 0.0
            
            # 扫描局部感受野
            for ox in range(-self.beam_radius, self.beam_radius + 1):
                for oy in range(-self.beam_radius, self.beam_radius + 1):
                    nx, ny = px + ox, py + oy
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        if self.mask_matrix[nx, ny] == 0:
                            # 致命熔断 1：膨胀导致撞墙（对于狭窄走廊可通过调整掩码或边缘忍耐度优化）
                            return config.INF, True
                            
                        risk_val = w_base_matrix[nx, ny]
                        if risk_val >= config.T_FATAL:
                            # 致命熔断 2：任意邻域像素击穿致死阈值
                            return config.INF, True
                            
                        if risk_val > step_max_risk:
                            step_max_risk = risk_val
                            
            total_cost += step_max_risk
            
        return total_cost, is_lethal

    # ---------------- 聚类相关逻辑保留不变 ----------------
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
        return self._calculate_centroids(clusters)

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