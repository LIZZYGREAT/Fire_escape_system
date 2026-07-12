# backend/core/system_controller.py
import numpy as np
from . import config
from .dstar_lite import DStarLite
from .lbb_manager import LBBManager

class SystemTickController:
    def __init__(self, dstar_engine: DStarLite, lbb_manager: LBBManager):
        self.dstar = dstar_engine
        self.lbb_manager = lbb_manager
        # 存储基线物理代价值，服务于红/黄色极限环境下的搜救降级网络
        self.baseline_g_table = {}

    def initialize_baseline(self):
        """
        初始化系统：一次性装载所有无火物理连线，生成绝对基线梯度场
        """
        for edge_id, edge_data in self.lbb_manager.edges_data.items():
            u, v = edge_id
            self.dstar.set_edge(u, v, edge_data['base_cost'])
        
        self.dstar.compute_shortest_path()
        self.baseline_g_table = self.dstar.g_table.copy()

    def sync_physical_to_graph(self, risk_updates: list, w_base_matrix: np.ndarray):
        """
        通过空间倒排索引，将底层的海量像素突变，精准过滤并坍缩为宏观图论边缘的代价更新
        """
        if not risk_updates:
            return

        dirty_edges = set()
        
        # O(1) 拦截：找出哪些突变像素真正影响了核心通道
        for x, y, _ in risk_updates:
            if (x, y) in self.lbb_manager.pixel_to_edges:
                dirty_edges.update(self.lbb_manager.pixel_to_edges[(x, y)])

        # 仅对受到波及的宏观边进行宽射线重积分
        for edge_id in dirty_edges:
            u, v = edge_id
            new_cost, is_lethal = self.lbb_manager.compute_edge_cost(edge_id, w_base_matrix)
            
            self.lbb_manager.edges_data[edge_id]['dynamic_cost'] = new_cost
            self.lbb_manager.edges_data[edge_id]['is_lethal'] = is_lethal
            
            self.dstar.update_edge_cost(u, v, new_cost)

        self.dstar.compute_shortest_path()

    def extract_topology_tree(self, black_boxes: list, w_base_matrix: np.ndarray) -> dict:
        """
        基于已收敛的宏观梯度图，仲裁生成无环拓扑树
        """
        topology_tree = {}
        node_candidates = {}
        node_status = {}
        node_current_g = {}

        physical_exits_set = set(self.lbb_manager.physical_exits)

        # --- 第一阶段：宏观节点状态与候选评估 ---
        for box_x, box_y in black_boxes:
            if self.lbb_manager.mask_matrix[box_x, box_y] == 0:
                continue

            node_id = (box_x, box_y)
            current_g_dynamic = self.dstar.g_table.get(node_id, config.INF)
            node_current_g[node_id] = current_g_dynamic
            
            node_risk = w_base_matrix[box_x, box_y]
            is_node_on_fire = node_risk >= 70.0
            is_node_smoky = node_risk >= 15.0 and not is_node_on_fire

            safe_candidates = []
            fallback_candidates = []

            for direction, target_coord in self.lbb_manager.topology_graph.get(node_id, {}).items():
                if target_coord:
                    edge_id = (node_id, target_coord)
                    edge_data = self.lbb_manager.edges_data.get(edge_id)
                    if not edge_data: 
                        continue

                    tx, ty = target_coord
                    
                    target_g_baseline = self.baseline_g_table.get(target_coord, config.INF) if target_coord not in physical_exits_set else 0.0
                    path_cost_baseline = edge_data['base_cost']

                    # 搜救降级通道 (完全无视火势的物理联通性)
                    if path_cost_baseline != config.INF:
                        projected_cost_baseline = target_g_baseline + path_cost_baseline
                        fallback_candidates.append({
                            "target": target_coord,
                            "dir": direction,
                            "cost": projected_cost_baseline
                        })

                    # 逃生安全通道 (依赖动态积分断言)
                    target_g_dynamic = self.dstar.g_table.get(target_coord, config.INF) if target_coord not in physical_exits_set else 0.0
                    path_cost_dynamic = edge_data['dynamic_cost']
                    is_lethal = edge_data['is_lethal']
                    is_target_on_fire = w_base_matrix[tx, ty] >= 70.0

                    if not is_target_on_fire and not is_lethal and target_g_dynamic < config.T_FATAL and path_cost_dynamic != config.INF:
                        projected_cost_dynamic = target_g_dynamic + path_cost_dynamic
                        safe_candidates.append({
                            "target": target_coord,
                            "dir": direction,
                            "cost": projected_cost_dynamic
                        })

            safe_candidates.sort(key=lambda x: x["cost"])
            fallback_candidates.sort(key=lambda x: x["cost"])

            # 严格着色断言
            if is_node_on_fire:
                status = 1  
                node_candidates[node_id] = fallback_candidates
            elif current_g_dynamic >= config.T_FATAL or not safe_candidates:
                status = 2  
                node_candidates[node_id] = fallback_candidates
            elif is_node_smoky:
                status = 3  
                node_candidates[node_id] = safe_candidates
            else:
                status = 0  
                node_candidates[node_id] = safe_candidates

            node_status[node_id] = status

        # --- 第二阶段：互指死锁逻辑仲裁 ---
        current_choices = {}
        for node_id, cands in node_candidates.items():
            current_choices[node_id] = 0 if cands else -1

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
                            
                            a_should_yield = g_a > g_b or (g_a == g_b and node_a > target_a)

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

        # --- 第三阶段：成树封装 ---
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