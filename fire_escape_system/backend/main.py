# backend/main.py
import os  
import asyncio
import json
import logging
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from core import config
from core.dstar_lite import DStarLite
from core.fire_dynamics import FireDynamicsEngine
from core.system_controller import SystemTickController
from core.lbb_manager import LBBManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SystemConductor")

# --- 1. 全局配置与矩阵加载 ---
base_dir = os.path.dirname(os.path.abspath(__file__))
mask_path = os.path.join(base_dir, 'data', 'M_mask.npy')
mask_matrix = np.load(mask_path).T
WIDTH, HEIGHT = mask_matrix.shape
physical_exits = config.PHYSICAL_EXITS 

black_boxes = []
for bx, by in config.INITIAL_BLACK_BOXES:
    if 0 <= bx < WIDTH and 0 <= by < HEIGHT and mask_matrix[bx, by] == 1:
        black_boxes.append((bx, by))

# --- 2. 仿真上下文管理器 (重新规划的依赖链) ---
class SimulationContext:
    def __init__(self):
        self.tick_count = 0
        self.ground_truth_fires = []
        self.pending_risk_updates = []
        self.previous_topology_tree = {}
        
        self.dstar_engine = None
        self.fire_engine = None
        self.lbb_manager = None
        self.system_controller = None

    def initialize_engines(self):
        logger.info("物理引擎容器初始化/重置中...")
        
        # 1. 物理场必须最先装载
        self.fire_engine = FireDynamicsEngine(WIDTH, HEIGHT, mask_matrix)
        
        # 2. 拓扑中间件构建欧几里得测距与倒排索引
        self.lbb_manager = LBBManager(black_boxes, mask_matrix, physical_exits)
        
        # 3. 宏观图论引擎组网
        self.dstar_engine = DStarLite(physical_exits)
        
        # 4. 控制器缝合与点火
        self.system_controller = SystemTickController(self.dstar_engine, self.lbb_manager)
        self.system_controller.initialize_baseline()
        
        logger.info("基线安全逃生网络及倒排索引机制已完全建立！")
        
        self.tick_count = 0
        self.ground_truth_fires = []
        self.pending_risk_updates = []
        self.previous_topology_tree = {}

sim_context = SimulationContext()
sim_context.initialize_engines()

# --- 3. 异步应用流控与网络层 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.is_paused = False
    logic_task = asyncio.create_task(logic_tick_loop(app))
    yield
    logic_task.cancel()

app = FastAPI(title="Smart Fire Escape System", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, 
    allow_methods=["*"], allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        payload = json.dumps(message, separators=(',', ':'))
        for connection in self.active_connections:
            try:
                await connection.send_text(payload)
            except Exception:
                pass

manager = ConnectionManager()

async def logic_tick_loop(app: FastAPI):
    logger.info("云端 Logic Tick 引擎已启动...")
    tick_interval = 1

    while True:
        await asyncio.sleep(tick_interval)
        
        if getattr(app.state, "is_paused", False):
            continue

        sim_context.tick_count += 1

        if sim_context.tick_count == 10:
            for fx, fy, intensity in config.INITIAL_FIRES:
                sim_context.ground_truth_fires.append((int(fx), int(fy), float(intensity)))
                logger.warning(f"系统注入：按配置爆发物理火灾 ({fx},{fy})")

        updates = await asyncio.to_thread(sim_context.fire_engine.tick_update, sim_context.ground_truth_fires, 2)
        if updates:
            sim_context.pending_risk_updates.extend(updates)

        w_base_matrix = sim_context.fire_engine.current_risk_matrix

        if sim_context.pending_risk_updates:
            await asyncio.to_thread(sim_context.system_controller.sync_physical_to_graph, sim_context.pending_risk_updates, w_base_matrix)
            sim_context.pending_risk_updates.clear()
        
        current_tree = await asyncio.to_thread(sim_context.system_controller.extract_topology_tree, black_boxes, w_base_matrix)
        
        tree_payload = {}
        for k, v in current_tree.items():
            if sim_context.previous_topology_tree.get(k) != v:
                tree_payload[k] = v
                sim_context.previous_topology_tree[k] = v

        fire_payload = [[x, y, round(val, 1)] for x, y, val in updates] if updates else []
        
        if fire_payload or tree_payload:
            await manager.broadcast({
                "type": "tick_update",
                "fire_diff": fire_payload,
                "topology_tree": tree_payload
            })

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            msg_type = payload.get("type")
            
            if msg_type == "request_full_sync":
                wall_coords = [[x, y] for x in range(WIDTH) for y in range(HEIGHT) if mask_matrix[x, y] == 0]
                
                w_base_matrix = sim_context.fire_engine.current_risk_matrix
                current_tree = await asyncio.to_thread(sim_context.system_controller.extract_topology_tree, black_boxes, w_base_matrix)
                sim_context.previous_topology_tree.update(current_tree)
                
                await websocket.send_text(json.dumps({
                    "type": "full_sync",
                    "wall_data": wall_coords,  
                    "fire_data": [[x, y, float(w_base_matrix[x, y])] for x in range(WIDTH) for y in range(HEIGHT) if w_base_matrix[x, y] > config.W_BASE],
                    "topology_tree": current_tree,
                    "exits_data": physical_exits
                }, separators=(',', ':')))
            
            elif msg_type == "control":
                cmd = payload.get("command")
                if cmd == "pause":
                    app.state.is_paused = True
                    logger.info(">> 收到控制台指令：仿真物理流逝已被冻结。")
                elif cmd == "resume":
                    app.state.is_paused = False
                    logger.info(">> 收到控制台指令：仿真物理流逝已恢复。")
                elif cmd == "reset":
                    logger.info(">> 收到控制台指令：正在重置底层物理沙盒...")
                    app.state.is_paused = True
                    sim_context.initialize_engines()
                    wall_coords = [[x, y] for x in range(WIDTH) for y in range(HEIGHT) if mask_matrix[x, y] == 0]
                    
                    w_base_matrix = sim_context.fire_engine.current_risk_matrix
                    clean_tree = await asyncio.to_thread(sim_context.system_controller.extract_topology_tree, black_boxes, w_base_matrix)
                    
                    await manager.broadcast({
                        "type": "full_sync",
                        "wall_data": wall_coords,  
                        "fire_data": [],
                        "topology_tree": clean_tree,
                        "exits_data": physical_exits
                    })
                    app.state.is_paused = False
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)