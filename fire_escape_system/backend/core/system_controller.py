# backend/core/system_controller.py
import numpy as np
from . import config
from .dstar_lite import DStarLite

class SystemTickController:
    def __init__(self, dstar_engine: DStarLite):
        self.dstar = dstar_engine
        # 初始化基线内存指针，用于存储无火状态下的绝对物理最短路径
        self.baseline_g_table = None

    def sync_dstar(self, risk_updates: list):
        if not risk_updates:
            return

        for x, y, new_weight in risk_updates:
            self.dstar.w_base_matrix[x, y] = new_weight

            for theta in range(config.NUM_DIRS):
                target_state = (x, y, theta)
                
                predecessors = self.dstar.get_legal_predecessors(target_state)
                for pred_state, _ in predecessors:
                    self.dstar.update_vertex(pred_state)
                
                self.dstar.update_vertex(target_state)

        self.dstar.compute_shortest_path()

    def _calculate_path_cost(self, x0: int, y0: int, x1: int, y1: int, beam_radius: int = 1) -> tuple[float, bool]:
        """
        基于动态火场的路径代价评估引擎（服务于安全逃生）。
        考虑火场高温、致死阈值，并带有射线宽度的膨胀检测。
        """
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        x, y = x0, y0
        
        total_path_cost = 0.0
        is_lethal = False
        
        while True:
            step_max_risk = 0.0
            for ox in range(-beam_radius, beam_radius + 1):
                for oy in range(-beam_radius, beam_radius + 1):
                    nx, ny = x + ox, y + oy
                    if 0 <= nx < self.dstar.width and 0 <= ny < self.dstar.height:
                        if self.dstar.mask_matrix[nx, ny] == 0:
                            return config.INF, True
                            
                        risk_val = self.dstar.w_base_matrix[nx, ny]
                        
                        if risk_val >= 70.0 and not (nx == x0 and ny == y0):
                            is_lethal = True
                            
                        if risk_val > step_max_risk:
                            step_max_risk = risk_val
                            
            total_path_cost += step_max_risk
                                
            if x == x1 and y == y1:
                break
                
            e2 = 2 * err
            if e2 >= -dy:
                err -= dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy
                
        return total_path_cost, is_lethal

    def _calculate_baseline_physical_cost(self, x0: int, y0: int, x1: int, y1: int) -> float:
        """
        基于纯粹物理拓扑的代数计算引擎（服务于逆向搜救）。
        完全无视火灾带来的风险权重，仅计算走廊物理距离。
        """
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        x, y = x0, y0
        
        total_physical_cost = 0.0
        
        while True:
            # 仅校验绝对墙体，不读取动态火灾底图 w_base_matrix
            if self.dstar.mask_matrix[x, y] == 0:
                return config.INF
                
            total_physical_cost += config.W_BASE
            
            if x == x1 and y == y1:
                break
                
            e2 = 2 * err
            if e2 >= -dy:
                err -= dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy
                
        return total_physical_cost

    def extract_topology_tree(self, black_boxes: list, topology_graph: dict) -> dict:
        # 惰性加载：捕获系统初始化的基线物理状态，作为救援人员导航进入火场的绝对真理
        if self.baseline_g_table is None:
            self.baseline_g_table = np.copy(self.dstar.g_table)

        topology_tree = {}
        physical_exits_set = set(self.dstar.physical_exits)
        
        node_candidates = {}
        node_status = {}
        node_current_g = {}

        # --- 第一阶段：全局视距射线探测与双轨候选排名 ---
        for box_x, box_y in black_boxes:
            if self.dstar.mask_matrix[box_x, box_y] == 0:
                continue

            node_id = (box_x, box_y)
            current_g_dynamic = min([self.dstar.g_table[box_x, box_y, t] for t in range(config.NUM_DIRS)])
            node_current_g[node_id] = current_g_dynamic
            
            # 【状态边界断言】：区分致死高温与烟雾笼罩
            # 因为 w_base_matrix 已经融合了 Heat + Smoke * 0.5，所以超过 15 即可判定为有风险逼近
            is_node_on_fire = self.dstar.w_base_matrix[box_x, box_y] >= 70.0
            is_node_smoky = self.dstar.w_base_matrix[box_x, box_y] >= 15.0 and not is_node_on_fire
            
            safe_candidates = []
            fallback_candidates = []
            
            for direction in range(config.NUM_DIRS):
                target_coord = topology_graph.get(node_id, {}).get(direction)
                
                if target_coord:
                    tx, ty = target_coord
                    
                    if target_coord in physical_exits_set:
                        target_g_dynamic = 0.0
                        target_g_baseline = 0.0
                    else:
                        target_g_dynamic = min([self.dstar.g_table[tx, ty, t] for t in range(config.NUM_DIRS)])
                        # 读取无火状态下的全局最短物理代价
                        target_g_baseline = min([self.baseline_g_table[tx, ty, t] for t in range(config.NUM_DIRS)])
                        
                    # 动态火灾代价（逃生用）与纯物理距离代价（搜救用）彻底解耦
                    path_cost_dynamic, is_lethal = self._calculate_path_cost(box_x, box_y, tx, ty, beam_radius=1)
                    path_cost_baseline = self._calculate_baseline_physical_cost(box_x, box_y, tx, ty)
                    
                    # 降级队列：服务于搜救。仅依靠基线物理数据，提供一条最纯粹的直连物理出口的路线
                    if path_cost_baseline != config.INF:
                        projected_cost_baseline = target_g_baseline + path_cost_baseline
                        fallback_candidates.append({
                            "target": target_coord,
                            "dir": direction,
                            "cost": projected_cost_baseline
                        })
                        
                        # 安全队列：服务于逃生。经过严苛的动态火场断言，过滤死路与致死风险
                        is_target_on_fire = self.dstar.w_base_matrix[tx, ty] >= 70.0
                        if not is_target_on_fire and not is_lethal and target_g_dynamic < config.T_FATAL and path_cost_dynamic != config.INF:
                            projected_cost_dynamic = target_g_dynamic + path_cost_dynamic
                            safe_candidates.append({
                                "target": target_coord,
                                "dir": direction,
                                "cost": projected_cost_dynamic
                            })
            
            # 各自维护独立的优先次序
            safe_candidates.sort(key=lambda x: x["cost"])
            fallback_candidates.sort(key=lambda x: x["cost"])
            
            # --- 状态机严格着色断言与队列派发 ---
            if is_node_on_fire:
                status = 1  # 红色：自身已被火灾吞噬，强制降级为搜救信标
                node_candidates[node_id] = fallback_candidates
            elif current_g_dynamic >= config.T_FATAL or not safe_candidates:
                status = 2  # 黄色：处于死胡同或被大火围困，强制降级为搜救信标
                node_candidates[node_id] = fallback_candidates
            elif is_node_smoky:
                status = 3  # 紫/粉色：烟雾笼罩警戒态，但仍有生机，必须使用安全逃生队列
                node_candidates[node_id] = safe_candidates
            else:
                status = 0  # 蓝色：正常逃生节点，严格遵守动态火场避险逻辑
                node_candidates[node_id] = safe_candidates
                
            node_status[node_id] = status

        # --- 第二阶段：全局死锁解除 (互指仲裁) ---
        current_choices = {}
        for node_id, cands in node_candidates.items():
            if cands:
                current_choices[node_id] = 0 
            else:
                current_choices[node_id] = -1

        stable = False
        iteration = 0
        while not stable and iteration < 10: 
            stable = True
            iteration += 1
            
            for node_a in list(node_candidates.keys()):
                idx_a = current_choices[node_a]
                if idx_a == -1:
                    continue
                    
                target_a = node_candidates[node_a][idx_a]["target"]
                
                if target_a in current_choices:
                    idx_b = current_choices[target_a]
                    if idx_b != -1:
                        target_b = node_candidates[target_a][idx_b]["target"]
                        
                        if target_b == node_a:
                            g_a = node_current_g[node_a]
                            g_b = node_current_g[target_a]
                            
                            # 互指死锁惩罚机制：代价高（离出口远）的必须让步
                            a_should_yield = False
                            if g_a > g_b:
                                a_should_yield = True
                            elif g_a == g_b:
                                a_should_yield = node_a > target_a 

                            if a_should_yield:
                                next_idx = idx_a + 1
                                if next_idx < len(node_candidates[node_a]):
                                    current_choices[node_a] = next_idx
                                    stable = False
                                else:
                                    current_choices[node_a] = -1
                                    stable = False
                            else:
                                next_idx = idx_b + 1
                                if next_idx < len(node_candidates[target_a]):
                                    current_choices[target_a] = next_idx
                                    stable = False
                                else:
                                    current_choices[target_a] = -1
                                    stable = False

        # --- 第三阶段：生成最终无环拓扑树 ---
        for node_id, status in node_status.items():
            idx = current_choices.get(node_id, -1)
            best_dir = -1
            next_node_id = None
            
            if idx != -1:
                best_choice = node_candidates[node_id][idx]
                best_dir = best_choice["dir"]
                next_node_id = f"{best_choice['target'][0]},{best_choice['target'][1]}"

            topology_tree[f"{node_id[0]},{node_id[1]}"] = {
                "status": status,
                "next": next_node_id,
                "dir": best_dir
            }

        return topology_tree