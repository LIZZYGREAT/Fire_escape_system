# backend/core/fire_dynamics.py
import numpy as np
from scipy.signal import convolve2d
from scipy.ndimage import binary_dilation, gaussian_filter
from . import config

class FireDynamicsEngine:
    def __init__(self, width: int, height: int, mask_matrix: np.ndarray):
        """
        双轨非线性演化引擎 (热力致死场 + 烟雾信息场)
        """
        self.width = width
        self.height = height
        self.mask_matrix = mask_matrix
        
        # [双轨核心]：热力池与烟雾池解耦
        self.heat_matrix = np.full((width, height), config.W_BASE, dtype=np.float32)
        self.smoke_matrix = np.zeros((width, height), dtype=np.float32) 
        
        self.current_risk_matrix = np.full((width, height), config.W_BASE, dtype=np.float32)
        self.reported_risk_matrix = np.full((width, height), config.W_BASE, dtype=np.float32)
        
        self.kernel = np.array([
            [0.1, 0.2, 0.1],
            [0.2, 0.0, 0.2],
            [0.1, 0.2, 0.1]
        ], dtype=np.float32)
        
        self.dilation_struct = np.array([
            [0, 1, 0],
            [1, 1, 1],
            [0, 1, 0]
        ], dtype=bool)

        # 【形态学预处理】：提取贴墙网格 (Coanda Effect 附壁效应计算掩码)
        walls = (self.mask_matrix == 0)
        dilated_walls = binary_dilation(walls, structure=self.dilation_struct)
        self.wall_adjacency_mask = dilated_walls & (self.mask_matrix == 1)

        # 【流体力学重构】：生成静态的低频空间异质性场 (模拟风道、可燃物不均匀分布)
        # 1. 生成纯白噪声
        raw_noise = np.random.uniform(0.0, 1.0, size=(width, height))
        # 2. 高斯模糊：将白噪声糊成大块大块的“斑块”（Sigma 越大，流体边缘越平滑且撕裂）
        smoothed_noise = gaussian_filter(raw_noise, sigma=3.0)
        # 3. 归一化并映射到 [0.5, 1.5] 的极端蔓延倍率区间
        min_n, max_n = smoothed_noise.min(), smoothed_noise.max()
        self.spatial_variance = 0.5 + 1.0 * (smoothed_noise - min_n) / (max_n - min_n + 1e-5)

    def tick_update(self, active_fire_sources: list, iterations: int = 2) -> list:
        # 1. 注入火源泵浦
        for fx, fy, intensity in active_fire_sources:
            if 0 <= fx < self.width and 0 <= fy < self.height:
                if self.mask_matrix[fx, fy] == 1:
                    self.heat_matrix[fx, fy] = max(self.heat_matrix[fx, fy], intensity)
                    # 真实火源点会产生极高浓度的信息因子(烟雾)
                    self.smoke_matrix[fx, fy] = 100.0 
                    
        # 2. 双轨反应-扩散运算
        for _ in range(iterations):
            # --- 轨道A：热力场演化 ---
            excess_heat = self.heat_matrix - config.W_BASE
            diffused_heat = convolve2d(excess_heat, self.kernel, mode='same', boundary='fill', fillvalue=0.0)
            
            # 附壁效应：贴墙网格传热系数加速 1.2 倍
            wall_multiplier = np.where(self.wall_adjacency_mask, 1.2, 1.0)
            
            # 【核心剥离】：彻底抛弃动态白噪声，使用静态流体地形场驱动演化
            self.heat_matrix += diffused_heat * self.spatial_variance * wall_multiplier * 0.35
            
            # 指数复利梯度
            combustion_mask = (self.heat_matrix > 25.0)
            growth = self.heat_matrix[combustion_mask] * 0.08 
            self.heat_matrix[combustion_mask] += growth
            
            self.heat_matrix = np.clip(self.heat_matrix, config.W_BASE, config.W_FIRE_MAX)
            self.heat_matrix = np.where(self.mask_matrix == 1, self.heat_matrix, config.W_BASE)

            # --- 轨道B：烟雾信息场演化 ---
            diffused_smoke = convolve2d(self.smoke_matrix, self.kernel, mode='same', boundary='fill', fillvalue=0.0)
            # 烟雾同样受空间地形场影响，呈现出不规则的触须状飘散
            self.smoke_matrix += diffused_smoke * self.spatial_variance * 0.85
            self.smoke_matrix = np.clip(self.smoke_matrix, 0.0, 100.0)
            self.smoke_matrix = np.where(self.mask_matrix == 1, self.smoke_matrix, 0.0)

        # 3. 形态学防贴边约束
        core_fire = self.heat_matrix > 70.0
        dilated_fire = binary_dilation(core_fire, structure=self.dilation_struct)
        fringe = dilated_fire & ~core_fire
        
        output_matrix = np.copy(self.heat_matrix)
        output_matrix[fringe] = np.maximum(output_matrix[fringe], config.W_FIRE_MAX * 0.9)
        
        # 风险融合：Cost = Heat + 0.5 * Smoke
        combined_risk = output_matrix + self.smoke_matrix * 0.5
        self.current_risk_matrix = np.where(self.mask_matrix == 1, combined_risk, config.W_BASE)
        
        # 4. 提取增量 Diff 并应用绝对状态机断言
        diff_matrix = np.abs(self.current_risk_matrix - self.reported_risk_matrix)
        
        # 引入致死阈值穿越断言。一旦达到 70.0，无视增量噪音，强制报警
        threshold_crossed = (self.reported_risk_matrix < 70.0) & (self.current_risk_matrix >= 70.0)
        mutated_mask = (diff_matrix > 20.0) | threshold_crossed
        
        mutated_coords = np.argwhere(mutated_mask)
        
        updates = []
        for x, y in mutated_coords:
            x = int(x)
            y = int(y)
            val = float(self.current_risk_matrix[x, y])
            updates.append((x, y, val))
            # 同步更新汇报备忘录
            self.reported_risk_matrix[x, y] = val
            
        return updates