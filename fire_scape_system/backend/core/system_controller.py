# backend/core/system_controller.py
import config
from .dstar_lite import DStarLite
from .fire_dynamics import FireDynamicsEngine

class SystemTickController:
    def __init__(self, dstar_engine: DStarLite, fire_engine: FireDynamicsEngine):
        """
        核心系统调度器，负责粘合环境扩散引擎与 D* Lite 寻路引擎，
        并在每一个时间切片（Tick）内严格按序触发数据流转。
        """
        self.dstar = dstar_engine
        self.fire = fire_engine

    def execute_tick(self, active_fire_sources: list) -> bool:
        """
        执行单个服务器时钟周期的主循环。
        
        :param active_fire_sources: 当前硬件上报的活跃火源 [(x1, y1, intensity1), ...]
        :return: 布尔值，标识本轮 Tick 是否触发了寻路图网络的更新重构
        """
        # 1. 触发物理环境扩散，获取差分突变
        # iterations=3 表示在一个 Tick 周期内，火势最多渗透 3 个网格的距离
        updates = self.fire.tick_update(active_fire_sources, iterations=3)

        # 如果火势处于稳定期，没有任何网格发生达到阈值的突变，则直接休眠，节省算力
        if not updates:
            return False

        # 2. 高维状态空间事件注入 (Event Injection)
        for x, y, new_weight in updates:
            # 2.1 同步更新 D* Lite 底层的客观物理矩阵
            self.dstar.w_base_matrix[x, y] = new_weight

            # 2.2 拓扑映射：寻址并唤醒所有受影响的前驱节点
            for theta in range(config.NUM_DIRS):
                target_state = (x, y, theta)
                
                # 寻找所有能够合法走向 target_state 的前置节点
                predecessors = self.dstar.get_legal_predecessors(target_state)
                
                for pred_state, _ in predecessors:
                    # 强迫这些前置节点重新审视周围的高危环境，触发不一致性入队
                    self.dstar.update_vertex(pred_state)
                
                # 为防止边缘状态锁定，连带强制刷新该目标节点本身
                self.dstar.update_vertex(target_state)

        # 3. 唤醒图论引擎，消化队列中的波纹
        # 引擎将阻塞执行，直到全图所有的 g(s) 与 rhs(s) 恢复数学一致性
        self.dstar.compute_shortest_path()
        
        return True

    def get_blackbox_command(self, box_x: int, box_y: int) -> int:
        """
        在图网络收敛后，为单个小黑盒提取极简的硬件执行指令。
        该方法执行彻底的状态降维与生命阈值熔断。
        """
        min_cost = config.INF
        best_direction = -1
        
        # 遍历北、东、南、西四个物理相邻网格
        dx = [0, 1, 0, -1]
        dy = [-1, 0, 1, 0]
        
        for next_dir in range(config.NUM_DIRS):
            nx = box_x + dx[next_dir]
            ny = box_y + dy[next_dir]
            
            # 剔除物理越界与墙体掩码
            if 0 <= nx < self.dstar.width and 0 <= ny < self.dstar.height:
                if self.dstar.mask_matrix[nx, ny] == 1:
                    
                    # 提取相邻网格在当前梯度场中的真实安全代价
                    # 由于小黑盒不具备朝向记忆，我们统一下游出口方向的最优 g 值
                    g_vals = [self.dstar.g_table[nx, ny, t] for t in range(config.NUM_DIRS)]
                    local_min_g = min(g_vals)
                    
                    # 走向该网格的总代价 = 该网格当前的火灾风险 + 后续通往出口的最小代价
                    step_cost = self.dstar.w_base_matrix[nx, ny] + local_min_g
                    
                    if step_cost < min_cost:
                        min_cost = step_cost
                        best_direction = next_dir
                        
        # 生命安全熔断机制 (Fallback FSM)
        if min_cost >= config.T_FATAL or best_direction == -1:
            return 0xFF  # 返回红闪 SOS 固守指令
            
        # 常态下返回方向指令，加 1 是为了匹配你之前定义的 0x01 ~ 0x04 硬件字节码
        return best_direction + 1