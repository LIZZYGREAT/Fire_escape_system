# backend/core/dstar_lite.py
import heapq
from . import config

class DStarLite:
    def __init__(self, exits: list):
        """
        D* Lite 宏观拓扑寻路引擎 (纯图论实现)
        不再处理像素级物理矩阵，完全基于小黑盒节点 (Node) 与有向边 (Edge) 进行演算。
        
        :param exits: 物理出口坐标列表，形如 [(x1, y1), (x2, y2)]
        """
        self.exits = exits
        self.SUPER_NODE = (-1, -1)
        
        # 核心状态表 (字典映射，彻底消灭高维空载矩阵)
        self.g_table = {self.SUPER_NODE: config.INF}
        self.rhs_table = {self.SUPER_NODE: 0.0}
        
        # 宏观拓扑数据总线
        # edge_costs[(u_x, u_y)][(v_x, v_y)] = dynamic_cost
        self.edge_costs = {}
        # reverse_edges[(v_x, v_y)] = set((u_x, u_y)) 用于 O(1) 查找前驱节点
        self.reverse_edges = {}
        
        self.U = []
        self.queue_dict = {}
        
        # 将所有真实物理出口连接至虚拟超级源点，代价为 0
        for exit_node in self.exits:
            self._ensure_node(exit_node)
            self.set_edge(exit_node, self.SUPER_NODE, 0.0)
            
        self._initialize_graph()

    def _ensure_node(self, node: tuple):
        if node not in self.g_table:
            self.g_table[node] = config.INF
            self.rhs_table[node] = config.INF
        if node not in self.edge_costs:
            self.edge_costs[node] = {}
        if node not in self.reverse_edges:
            self.reverse_edges[node] = set()

    def set_edge(self, u: tuple, v: tuple, cost: float):
        """
        全量覆盖写入拓扑边代价，并自动建立反向索引
        """
        self._ensure_node(u)
        self._ensure_node(v)
        self.edge_costs[u][v] = cost
        self.reverse_edges[v].add(u)

    def get_edge_cost(self, u: tuple, v: tuple) -> float:
        return self.edge_costs.get(u, {}).get(v, config.INF)

    def get_legal_successors(self, node: tuple) -> list:
        return [(v, cost) for v, cost in self.edge_costs.get(node, {}).items() if cost != config.INF]

    def get_legal_predecessors(self, node: tuple) -> list:
        return [(u, self.get_edge_cost(u, node)) for u in self.reverse_edges.get(node, set()) if self.get_edge_cost(u, node) != config.INF]

    def _initialize_graph(self):
        key = self.calculate_key(self.SUPER_NODE)
        self.insert_to_queue(self.SUPER_NODE, key)

    def calculate_key(self, node: tuple) -> tuple:
        min_val = min(self.g_table[node], self.rhs_table[node])
        return (min_val, min_val)

    def insert_to_queue(self, node: tuple, key: tuple):
        self.queue_dict[node] = key
        heapq.heappush(self.U, (key[0], key[1], node[0], node[1]))

    def remove_from_queue(self, node: tuple):
        if node in self.queue_dict:
            del self.queue_dict[node]

    def top_key(self) -> tuple:
        while self.U:
            k1, k2, nx, ny = self.U[0]
            node = (nx, ny)
            if node not in self.queue_dict or self.queue_dict[node] != (k1, k2):
                heapq.heappop(self.U)
            else:
                return (k1, k2)
        return (config.INF, config.INF)

    def pop_queue(self) -> tuple:
        while self.U:
            k1, k2, nx, ny = heapq.heappop(self.U)
            node = (nx, ny)
            if node in self.queue_dict and self.queue_dict[node] == (k1, k2):
                del self.queue_dict[node]
                return node
        return None

    def update_vertex(self, node: tuple):
        if node != self.SUPER_NODE:
            min_rhs = config.INF
            for v, cost in self.get_legal_successors(node):
                val = cost + self.g_table[v]
                if val < min_rhs:
                    min_rhs = val
            self.rhs_table[node] = min_rhs

        self.remove_from_queue(node)

        if self.g_table[node] != self.rhs_table[node]:
            key = self.calculate_key(node)
            self.insert_to_queue(node, key)

    def update_edge_cost(self, u: tuple, v: tuple, new_cost: float):
        """
        对外接口：接收底层 LBBManager 传来的射线积分变动
        """
        old_cost = self.get_edge_cost(u, v)
        if old_cost == new_cost:
            return
            
        self.set_edge(u, v, new_cost)
        self.update_vertex(u)

    def compute_shortest_path(self):
        while self.U:
            k_old = self.top_key()
            if k_old == (config.INF, config.INF):
                break

            u = self.pop_queue()
            if u is None:
                break

            k_new = self.calculate_key(u)

            if k_old < k_new:
                self.insert_to_queue(u, k_new)
            elif self.g_table[u] > self.rhs_table[u]:
                self.g_table[u] = self.rhs_table[u]
                for p, _ in self.get_legal_predecessors(u):
                    self.update_vertex(p)
            else:
                self.g_table[u] = config.INF
                self.update_vertex(u)
                for p, _ in self.get_legal_predecessors(u):
                    self.update_vertex(p)