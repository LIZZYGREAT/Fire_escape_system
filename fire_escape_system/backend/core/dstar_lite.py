# backend/core/dstar_lite.py
import heapq
import numpy as np
from . import config

class DStarLite:
    def __init__(self, width: int, height: int, mask_matrix: np.ndarray, physical_exits: list):
        """
        D* Lite 寻路引擎初始化 (基于 NumPy 连续内存的高维状态空间)
        
        :param width: 建筑网格宽度
        :param height: 建筑网格高度
        :param mask_matrix: 0/1 掩码矩阵，0为墙体不可通行，1为合法走廊 (形状: width x height)
        :param physical_exits: 物理出口坐标列表，形如 [(x1, y1), (x2, y2)]
        """
        self.width = width
        self.height = height
        self.mask_matrix = mask_matrix
        self.physical_exits = physical_exits
        
        # --- 1. NumPy 连续内存状态矩阵初始化 ---
        # 维度为 (width, height, 4)，O(1) 的寻址效率，彻底告别 Python 嵌套列表的内存碎片
        self.g_table = np.full((width, height, config.NUM_DIRS), config.INF, dtype=np.float32)
        self.rhs_table = np.full((width, height, config.NUM_DIRS), config.INF, dtype=np.float32)
        
        # 物理风险底图 (由环境传感器与高斯卷积生成)，起初全量初始化为基础代价 W_BASE
        self.w_base_matrix = np.full((width, height), config.W_BASE, dtype=np.float32)
        
        # 虚拟超级源点的独立状态存储变量
        self.super_g = config.INF
        self.super_rhs = 0.0  # 超级源点作为反向搜索的绝对终点，前瞻代价始终为 0
        
        # --- 2. 优先队列初始化与惰性删除架构 ---
        # 队列元素结构: (key1, key2, x, y, theta)
        self.U = []
        # queue_dict 承担 O(1) 的存在性检查与版本校验，键为 (x, y, theta)，值为最新 (key1, key2)
        self.queue_dict = {}
        
        # --- 3. 引擎启动映射 ---
        self._initialize_graph()

    def _initialize_graph(self):
        """
        初始化拓扑，将超级源点压入队列，触发反向全局搜索引擎的波纹。
        """
        key = self.calculate_key(config.SUPER_NODE)
        self.insert_to_queue(config.SUPER_NODE, key)

    def calculate_key(self, state: tuple) -> tuple:
        """
        计算状态节点的优先级 Key = [k1, k2]
        基于超级源点的反向多目标搜索机制下，启发式函数 h(s) 恒等于 0，引擎在形态上退化为动态 Dijkstra。
        """
        if state == config.SUPER_NODE:
            min_val = min(self.super_g, self.super_rhs)
            return (min_val, min_val)
            
        x, y, theta = state
        g_val = self.g_table[x, y, theta]
        rhs_val = self.rhs_table[x, y, theta]
        
        min_val = min(g_val, rhs_val)
        return (min_val, min_val)

    def insert_to_queue(self, state: tuple, key: tuple):
        """
        将状态节点插入或覆盖更新至优先队列
        """
        self.queue_dict[state] = key
        # heapq 依赖元组首元素进行小顶堆比较，我们将 key 值置于首位
        heap_element = (key[0], key[1], state[0], state[1], state[2])
        heapq.heappush(self.U, heap_element)

    def remove_from_queue(self, state: tuple):
        """
        逻辑出队操作：仅在字典中抹除记录，配合 top_key 实现堆内僵尸节点的惰性删除。
        """
        if state in self.queue_dict:
            del self.queue_dict[state]

    def top_key(self) -> tuple:
        """
        获取堆顶最小 Key，并清理失效节点，避免 O(N) 级别的手动 remove 遍历。
        """
        while self.U:
            top_element = self.U[0]
            k1, k2, x, y, theta = top_element
            state = (x, y, theta)
            
            # 版本校验：若字典无此状态或 Key 版本不匹配，直接弹出抛弃
            if state not in self.queue_dict or self.queue_dict[state] != (k1, k2):
                heapq.heappop(self.U)
            else:
                return (k1, k2)
        
        return (config.INF, config.INF)

    def pop_queue(self) -> tuple:
        """
        弹出并返回当前优先级最高的有效状态节点
        """
        while self.U:
            top_element = heapq.heappop(self.U)
            k1, k2, x, y, theta = top_element
            state = (x, y, theta)
            
            if state in self.queue_dict and self.queue_dict[state] == (k1, k2):
                del self.queue_dict[state]
                return state
        return None

    def get_turn_penalty(self, dir_in: int, dir_out: int) -> float:
        """
        计算从进向 dir_in 转到出向 dir_out 时的惩罚函数
        """
        if dir_in == dir_out:
            return 0.0
            
        diff = abs(dir_in - dir_out)
        if diff == 2:
            return config.W_REVERSE
        else:
            return config.W_TURN

    def get_legal_successors(self, state: tuple) -> list:
        """
        获取高维拓扑中的合法次级状态节点 (Successors)。
        注：因 D* Lite 执行的是反向搜索，此处的 Successors 在物理意义上对应受困者向出口逃生的下一步。
        """
        if state == config.SUPER_NODE:
            return []
            
        x, y, theta = state
        
        # 若位于安全出口的坐标上，其唯一的后继节点是被权重 0 边连接的虚拟超级源点
        if (x, y) in self.physical_exits:
            return [(config.SUPER_NODE, 0.0)]
            
        successors = []
        
        # 方向映射体系：0(N), 1(E), 2(S), 3(W)
        dx = [0, 1, 0, -1]
        dy = [-1, 0, 1, 0]
        
        for next_dir in range(config.NUM_DIRS):
            nx = x + dx[next_dir]
            ny = y + dy[next_dir]
            
            # 物理越界防范与刚性墙体碰撞检测
            if 0 <= nx < self.width and 0 <= ny < self.height:
                if self.mask_matrix[nx, ny] == 1:
                    next_state = (nx, ny, next_dir)
                    
                    # C(s1, s2) = W_base + Penalty(turn)
                    base_cost = self.w_base_matrix[nx, ny]
                    turn_penalty = self.get_turn_penalty(theta, next_dir)
                    transition_cost = base_cost + turn_penalty
                    
                    successors.append((next_state, transition_cost))
                    
        return successors

    def get_legal_predecessors(self, state: tuple) -> list:
        """
        获取高维拓扑中的合法前驱节点 (Predecessors)。
        物理意义：寻找所有能够合法地“走入”当前状态 state 的上一步状态。
        """
        predecessors = []

        if state == config.SUPER_NODE:
            # 谁能合法地走向超级源点？
            # 只有位于物理出口网格上的状态，且没有任何方向限制
            for ex, ey in self.physical_exits:
                # 遍历到达出口网格时的所有可能朝向 p_theta
                for p_theta in range(config.NUM_DIRS):
                    if self.mask_matrix[ex, ey] == 1:
                        # 从出口迈向虚拟超级源点，不存在物理代价
                        predecessors.append(((ex, ey, p_theta), 0.0))
            return predecessors

        x, y, theta = state

        # 根据当前受困者走入网格的朝向 theta，反向推演其上一步的物理坐标 (px, py)
        dx = [0, 1, 0, -1]
        dy = [-1, 0, 1, 0]

        px = x - dx[theta]
        py = y - dy[theta]

        # 校验反推的物理坐标是否在建筑范围内，且不能是墙体
        if 0 <= px < self.width and 0 <= py < self.height:
            if self.mask_matrix[px, py] == 1:
                # 在上一个物理网格 (px, py) 时，受困者的朝向 p_theta 可能是四个方向之一
                for p_theta in range(config.NUM_DIRS):
                    
                    # 转弯代价：从 p_theta 转为 theta
                    turn_penalty = self.get_turn_penalty(p_theta, theta)
                    
                    # 严格剔除掉原地 180 度折返的非法拓扑边
                    if turn_penalty != config.INF:
                        # 转移代价 = 当前网格 (x, y) 的客观物理代价 + 转向疲劳代价
                        base_cost = self.w_base_matrix[x, y]
                        transition_cost = base_cost + turn_penalty
                        
                        predecessors.append(((px, py, p_theta), transition_cost))

        return predecessors


    def update_vertex(self, state: tuple):
        """
        引擎的最核心原子操作：
        基于当前所有合法的后继节点，重算当前状态的前瞻代价 rhs，并执行队列一致性仲裁。
        """
        if state != config.SUPER_NODE:
            x, y, theta = state
            min_rhs = config.INF
            
            # 公式实现：rhs(u) = min_{s' in Successors(u)} (c(u, s') + g(s'))
            for next_state, transition_cost in self.get_legal_successors(state):
                if next_state == config.SUPER_NODE:
                    next_g = self.super_g
                else:
                    nx, ny, ntheta = next_state
                    next_g = self.g_table[nx, ny, ntheta]
                    
                cost = transition_cost + next_g
                if cost < min_rhs:
                    min_rhs = cost
            
            self.rhs_table[x, y, theta] = min_rhs

        # 重算优先级前清除旧记录
        self.remove_from_queue(state)
        
        # 判定收敛条件
        if state == config.SUPER_NODE:
            g_val = self.super_g
            rhs_val = self.super_rhs
        else:
            x, y, theta = state
            g_val = self.g_table[x, y, theta]
            rhs_val = self.rhs_table[x, y, theta]
            
        # 不一致（g != rhs）则强制入队等待缝补
        if g_val != rhs_val:
            key = self.calculate_key(state)
            self.insert_to_queue(state, key)



    def compute_shortest_path(self):
        """
        主缝合循环：不断处理优先队列 U 中的不一致节点，直到图网络状态重新收敛。
        由于我们需要为所有小黑盒提供全图维度的安全梯度，因此本引擎不设单点提取判定，强制跑空队列。
        """
        while self.U:
            # 取出堆顶元素的理论优先级（可能已过期，故调用 top_key 校验）
            k_old = self.top_key()
            if k_old == (config.INF, config.INF):
                break  # 队列内仅剩失效的游离节点，安全梯度场已完全收敛

            u = self.pop_queue()
            if u is None:
                break

            # 重新计算弹出的节点 u 的最新优先级
            k_new = self.calculate_key(u)

            # 机制：延迟更新 (Lazy Update)
            # 若节点在排队期间由于周围火势蔓延导致情况恶化，以新代价重新入队
            if k_old < k_new:
                self.insert_to_queue(u, k_new)
            else:
                # 提取确切代价 g 与前瞻代价 rhs
                if u == config.SUPER_NODE:
                    g_val = self.super_g
                    rhs_val = self.super_rhs
                else:
                    ux, uy, utheta = u
                    g_val = self.g_table[ux, uy, utheta]
                    rhs_val = self.rhs_table[ux, uy, utheta]

                # 核心状态机分支
                if g_val > rhs_val:
                    # 状态 A：过一致 (Overconsistent)
                    if u == config.SUPER_NODE:
                        self.super_g = rhs_val
                    else:
                        self.g_table[ux, uy, utheta] = rhs_val

                    # 自身变安全后，通知所有“依赖自己”的前驱节点尝试重算借道
                    for p, _ in self.get_legal_predecessors(u):
                        self.update_vertex(p)
                        
                else:
                    # 状态 B：欠一致 (Underconsistent)
                    if u == config.SUPER_NODE:
                        self.super_g = config.INF
                    else:
                        self.g_table[ux, uy, utheta] = config.INF

                    # 毁灭旧有的错误路径记忆后，必须立刻重新审视周围，寻找次优解
                    self.update_vertex(u)
                    
                    # 通知所有前驱节点：“我这条路被火堵死了，你们必须重算”
                    for p, _ in self.get_legal_predecessors(u):
                        self.update_vertex(p)