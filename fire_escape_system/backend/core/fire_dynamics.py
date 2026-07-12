# backend/core/fire_dynamics.py
import numpy as np
from scipy.signal import convolve2d
from scipy.ndimage import binary_dilation, gaussian_filter
from . import config

class FireDynamicsEngine:
    def __init__(self, width: int, height: int, mask_matrix: np.ndarray):
        self.width = width
        self.height = height
        self.mask_matrix = mask_matrix
        
        # --- 核心物理场矩阵 ---
        self.heat_matrix = np.full((width, height), config.W_BASE, dtype=np.float32)
        self.smoke_matrix = np.zeros((width, height), dtype=np.float32) 
        self.fuel_matrix = np.full((width, height), 100.0, dtype=np.float32)
        
        # --- 与总线通信的风险状态矩阵 ---
        self.current_risk_matrix = np.full((width, height), config.W_BASE, dtype=np.float32)
        self.reported_risk_matrix = np.full((width, height), config.W_BASE, dtype=np.float32)
        
        # --- 热力学与流体力学常数设定 ---
        self.IGNITION_TEMP = 50.0        # 点火阈值：必须达到此温度才开始剧烈燃烧消耗燃料
        self.COOLING_FACTOR = 0.90       # 牛顿冷却系数：燃料耗尽后的散热速率
        self.SMOKE_DISSIPATION = 0.90    # 烟雾沉降系数：全局烟雾消散速率
        self.WIND_INFLUENCE = 0.6        # 风力拉扯系数 (alpha)
        
        # 全局风向矢量 (默认暂定微弱西北风，可随时暴露给前端供动态修改)
        self.wind_vector = (0.5, 0.5)    
        
        # 基础各向同性卷积核 (Diffusion Kernel)
        self.base_kernel = np.array([
            [0.1, 0.2, 0.1],
            [0.2, 0.0, 0.2],
            [0.1, 0.2, 0.1]
        ], dtype=np.float32)
        
        # 生成动态风场卷积核
        self.dynamic_kernel = self._generate_wind_kernel()
        
        # 形态学防贴边约束结构
        self.dilation_struct = np.array([
            [0, 1, 0],
            [1, 1, 1],
            [0, 1, 0]
        ], dtype=bool)

        walls = (self.mask_matrix == 0)
        dilated_walls = binary_dilation(walls, structure=self.dilation_struct)
        self.wall_adjacency_mask = dilated_walls & (self.mask_matrix == 1)

        # 空间异质性噪声底图
        raw_noise = np.random.uniform(0.0, 1.0, size=(width, height))
        smoothed_noise = gaussian_filter(raw_noise, sigma=3.0)
        min_n, max_n = smoothed_noise.min(), smoothed_noise.max()
        self.spatial_variance = 0.5 + 1.0 * (smoothed_noise - min_n) / (max_n - min_n + 1e-5)

    def _generate_wind_kernel(self) -> np.ndarray:
        """
        利用点乘计算风场干预下的非对称对流扩散矩阵，并严谨归一化
        """
        wx, wy = self.wind_vector
        if wx == 0 and wy == 0:
            return self.base_kernel
            
        wind_kernel = np.zeros_like(self.base_kernel)
        
        # 遍历 3x3 矩阵，计算点乘偏移
        for i in range(3):
            for j in range(3):
                if i == 1 and j == 1:
                    continue
                # 将矩阵索引 (i, j) 转化为物理几何向量 (dx, dy)
                dx = i - 1
                dy = j - 1
                
                # 计算向量点乘
                dot_product = wx * dx + wy * dy
                
                # 套用偏置公式: K_new = K_base * (1 + alpha * (W·D))
                base_val = self.base_kernel[i, j]
                wind_kernel[i, j] = base_val * (1.0 + self.WIND_INFLUENCE * dot_product)
                
        # 防止因极端风力导致的负权重
        wind_kernel = np.clip(wind_kernel, 0.0, None)
        
        # 绝对归一化：保证能量守恒，不凭空造热
        total_weight = np.sum(wind_kernel)
        if total_weight > 0:
            wind_kernel = wind_kernel / total_weight
            # 还原至基础扩散系数的量级 (base_kernel 之和为 1.2)
            wind_kernel *= 1.2
            
        return wind_kernel

    def update_wind(self, new_wind_vector: tuple):
        """对外暴露的风场修改接口"""
        self.wind_vector = new_wind_vector
        self.dynamic_kernel = self._generate_wind_kernel()

    def tick_update(self, active_fire_sources: list, iterations: int = 2) -> list:
        # 1. 注入绝对物理火源点
        for fx, fy, intensity in active_fire_sources:
            if 0 <= fx < self.width and 0 <= fy < self.height:
                if self.mask_matrix[fx, fy] == 1:
                    self.heat_matrix[fx, fy] = max(self.heat_matrix[fx, fy], intensity)
                    self.smoke_matrix[fx, fy] = 100.0 
                    
        for _ in range(iterations):
            # --- 轨道A：热力场与燃烧状态机 ---
            excess_heat = self.heat_matrix - config.W_BASE
            
            # 引入动态风向矩阵进行对流扩散
            diffused_heat = convolve2d(excess_heat, self.dynamic_kernel, mode='same', boundary='fill', fillvalue=0.0)
            
            wall_multiplier = np.where(self.wall_adjacency_mask, 1.2, 1.0)
            self.heat_matrix += diffused_heat * self.spatial_variance * wall_multiplier * 0.35
            
            # 【状态机严格着色】
            # 状态 A: 仅吸热，未达点火点 (Heat < 50)，静默过渡
            # 状态 B: 剧烈燃烧 (Heat >= 50 且有燃料)
            # 状态 C: 余烬冷却 (燃料 <= 0)
            
            is_combusting = (self.heat_matrix >= self.IGNITION_TEMP) & (self.fuel_matrix > 0)
            is_burnout = self.fuel_matrix <= 0
            
            # B. 燃烧放热反馈
            self.fuel_matrix[is_combusting] -= 3.5
            self.fuel_matrix = np.clip(self.fuel_matrix, 0.0, 100.0)
            
            growth = self.heat_matrix[is_combusting] * 0.08 
            self.heat_matrix[is_combusting] += growth
            
            # 伴随燃烧产生浓烟
            self.smoke_matrix[is_combusting] += 15.0
            
            # C. 耗尽后牛顿指数冷却 (平滑降回 W_BASE，解开 75.0 的死锁)
            self.heat_matrix[is_burnout] = config.W_BASE + (self.heat_matrix[is_burnout] - config.W_BASE) * self.COOLING_FACTOR
            
            self.heat_matrix = np.clip(self.heat_matrix, config.W_BASE, config.W_FIRE_MAX)
            self.heat_matrix = np.where(self.mask_matrix == 1, self.heat_matrix, config.W_BASE)

            # --- 轨道B：烟雾场演化与沉降 ---
            diffused_smoke = convolve2d(self.smoke_matrix, self.dynamic_kernel, mode='same', boundary='fill', fillvalue=0.0)
            self.smoke_matrix += diffused_smoke * self.spatial_variance * 0.85
            
            # 【烟雾消散机制】：每一帧按系数沉降，无火灾后会慢慢飘散澄清
            self.smoke_matrix *= self.SMOKE_DISSIPATION
            
            self.smoke_matrix = np.clip(self.smoke_matrix, 0.0, 100.0)
            self.smoke_matrix = np.where(self.mask_matrix == 1, self.smoke_matrix, 0.0)

            # --- 绝对物理边界截断 ---
            self.heat_matrix[:10, :] = config.W_BASE
            self.heat_matrix[241:, :] = config.W_BASE
            self.heat_matrix[:, :10] = config.W_BASE
            self.heat_matrix[:, 241:] = config.W_BASE

            self.smoke_matrix[:10, :] = 0.0
            self.smoke_matrix[241:, :] = 0.0
            self.smoke_matrix[:, :10] = 0.0
            self.smoke_matrix[:, 241:] = 0.0

        # --- 形态学处理与突变过滤 ---
        core_fire = self.heat_matrix > 70.0
        dilated_fire = binary_dilation(core_fire, structure=self.dilation_struct)
        fringe = dilated_fire & ~core_fire
        
        output_matrix = np.copy(self.heat_matrix)
        output_matrix[fringe] = np.maximum(output_matrix[fringe], config.W_FIRE_MAX * 0.9)
        
        # 综合风险评估 = 高温致命性 + 烟雾致盲度
        combined_risk = output_matrix + self.smoke_matrix * 0.5
        self.current_risk_matrix = np.where(self.mask_matrix == 1, combined_risk, config.W_BASE)
        
        diff_matrix = np.abs(self.current_risk_matrix - self.reported_risk_matrix)
        threshold_crossed = (self.reported_risk_matrix < 70.0) & (self.current_risk_matrix >= 70.0)
        
        # 平滑过滤：避免每跳个 1~2 像素值也往 D* 里传，造成通道无效堵塞
        mutated_mask = (diff_matrix > 15.0) | threshold_crossed
        
        mutated_coords = np.argwhere(mutated_mask)
        
        updates = []
        for x, y in mutated_coords:
            x = int(x)
            y = int(y)
            val = float(self.current_risk_matrix[x, y])
            updates.append((x, y, val))
            self.reported_risk_matrix[x, y] = val
            
        return updates