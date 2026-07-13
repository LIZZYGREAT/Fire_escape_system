# backend/core/config.py

# --- 全局数学常量定义 ---
INF = float('inf')

# --- 1. 状态空间与方向映射 ---
DIR_N = 0  
DIR_E = 1  
DIR_S = 2  
DIR_W = 3  

NUM_DIRS = 4
SUPER_NODE = (-1, -1, -1)

# --- 2. 核心代价权重系统 ---
W_BASE = 1.0
W_TURN = 2.0

# 【修复核心2】：废除无穷大的死亡掉头惩罚，允许在绝境下付出一定代价后转身逃生
W_REVERSE = 20.0 

# --- 3. 风险与系统状态机阈值 ---
# 【修复核心3】：配合天价火灾，同步提升熔断极值
T_FATAL = 5000.0

# 【修复核心4】：将火灾代价提升百倍，彻底杜绝算法“贪便宜”带人穿火场的弱智行为
W_FIRE_MAX = 10000.0

# Map coordinates and demo fire sources live in versioned map packages under
# ``backend/maps``.  Keeping this module numeric-only prevents the planning
# core from depending on a particular building.
