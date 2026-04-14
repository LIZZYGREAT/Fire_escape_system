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
        
        self.heat_matrix = np.full((width, height), config.W_BASE, dtype=np.float32)
        self.smoke_matrix = np.zeros((width, height), dtype=np.float32) 
        
        self.fuel_matrix = np.full((width, height), 100.0, dtype=np.float32)
        self.MIN_RESIDUAL_HEAT = 75.0 
        
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

        walls = (self.mask_matrix == 0)
        dilated_walls = binary_dilation(walls, structure=self.dilation_struct)
        self.wall_adjacency_mask = dilated_walls & (self.mask_matrix == 1)

        raw_noise = np.random.uniform(0.0, 1.0, size=(width, height))
        smoothed_noise = gaussian_filter(raw_noise, sigma=3.0)
        min_n, max_n = smoothed_noise.min(), smoothed_noise.max()
        self.spatial_variance = 0.5 + 1.0 * (smoothed_noise - min_n) / (max_n - min_n + 1e-5)

    def tick_update(self, active_fire_sources: list, iterations: int = 2) -> list:
        for fx, fy, intensity in active_fire_sources:
            if 0 <= fx < self.width and 0 <= fy < self.height:
                if self.mask_matrix[fx, fy] == 1:
                    self.heat_matrix[fx, fy] = max(self.heat_matrix[fx, fy], intensity)
                    self.smoke_matrix[fx, fy] = 100.0 
                    
        for _ in range(iterations):
            # --- 轨道A：热力场演化 ---
            excess_heat = self.heat_matrix - config.W_BASE
            diffused_heat = convolve2d(excess_heat, self.kernel, mode='same', boundary='fill', fillvalue=0.0)
            wall_multiplier = np.where(self.wall_adjacency_mask, 1.2, 1.0)
            self.heat_matrix += diffused_heat * self.spatial_variance * wall_multiplier * 0.35
            
            is_burning = self.heat_matrix > 25.0
            has_fuel = self.fuel_matrix > 0.0
            
            combustion_mask = is_burning & has_fuel
            burnout_mask = is_burning & ~has_fuel
            
            # 【逻辑修改 2】：大幅加快火灾衰减。燃料消耗 1.5 -> 3.5
            self.fuel_matrix[combustion_mask] -= 3.5
            self.fuel_matrix = np.clip(self.fuel_matrix, 0.0, 100.0)
            
            growth = self.heat_matrix[combustion_mask] * 0.08 
            self.heat_matrix[combustion_mask] += growth
            
            # 【逻辑修改 2】：热量散失翻倍 3.0 -> 7.0
            self.heat_matrix[burnout_mask] -= 7.0
            
            self.heat_matrix[burnout_mask] = np.maximum(self.heat_matrix[burnout_mask], self.MIN_RESIDUAL_HEAT)
            
            self.heat_matrix = np.clip(self.heat_matrix, config.W_BASE, config.W_FIRE_MAX)
            self.heat_matrix = np.where(self.mask_matrix == 1, self.heat_matrix, config.W_BASE)

            # --- 轨道B：烟雾场演化 ---
            diffused_smoke = convolve2d(self.smoke_matrix, self.kernel, mode='same', boundary='fill', fillvalue=0.0)
            self.smoke_matrix += diffused_smoke * self.spatial_variance * 0.85
            self.smoke_matrix = np.clip(self.smoke_matrix, 0.0, 100.0)
            self.smoke_matrix = np.where(self.mask_matrix == 1, self.smoke_matrix, 0.0)

            # 【逻辑修改 1】：绝对物理边界截断 [10, 240]
            # 利用切片直接覆写边缘，性能极高
            self.heat_matrix[:10, :] = config.W_BASE
            self.heat_matrix[241:, :] = config.W_BASE
            self.heat_matrix[:, :10] = config.W_BASE
            self.heat_matrix[:, 241:] = config.W_BASE

            self.smoke_matrix[:10, :] = 0.0
            self.smoke_matrix[241:, :] = 0.0
            self.smoke_matrix[:, :10] = 0.0
            self.smoke_matrix[:, 241:] = 0.0

        # 形态学防贴边约束
        core_fire = self.heat_matrix > 70.0
        dilated_fire = binary_dilation(core_fire, structure=self.dilation_struct)
        fringe = dilated_fire & ~core_fire
        
        output_matrix = np.copy(self.heat_matrix)
        output_matrix[fringe] = np.maximum(output_matrix[fringe], config.W_FIRE_MAX * 0.9)
        
        combined_risk = output_matrix + self.smoke_matrix * 0.5
        self.current_risk_matrix = np.where(self.mask_matrix == 1, combined_risk, config.W_BASE)
        
        diff_matrix = np.abs(self.current_risk_matrix - self.reported_risk_matrix)
        threshold_crossed = (self.reported_risk_matrix < 70.0) & (self.current_risk_matrix >= 70.0)
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