// frontend/js/main.js
import { networkEngine, GlobalState } from './network.js';
import { SceneRenderer } from './renderer.js';
import { SplineEngine } from './spline_engine.js'; 

const GRID_WIDTH = 250; 
const GRID_HEIGHT = 250;
const CELL_SIZE = 3; 

class EngineConductor {
    constructor() {
        this.renderer = null;
        this.splineEngine = null;
        this.animationFrameId = null;
        this.isPaused = false;
    }

    async bootstrap() {
        this.renderer = new SceneRenderer('canvas-container', GRID_WIDTH, GRID_HEIGHT, CELL_SIZE);
        this.splineEngine = new SplineEngine(CELL_SIZE);
        
        this.registerEventHooks();
        this.bindDashboardControls(); // 绑定UI控制面板
        
        networkEngine.connect();
        this.startRenderLoop();
    }

    bindDashboardControls() {
        const btnToggle = document.getElementById('btn-toggle');
        const btnReset = document.getElementById('btn-reset');

        btnToggle.addEventListener('click', () => {
            this.isPaused = !this.isPaused;
            if (this.isPaused) {
                btnToggle.innerText = 'RESUME 恢复演化';
                btnToggle.classList.add('active-btn');
                networkEngine.sendControl('pause');
            } else {
                btnToggle.innerText = 'PAUSE 暂停演化';
                btnToggle.classList.remove('active-btn');
                networkEngine.sendControl('resume');
            }
        });

        btnReset.addEventListener('click', () => {
            console.log("[Dashboard] 发起物理沙盒重置指令...");
            // 发送重置后，后端会自动处理状态清除并下发全量 sync 帧
            networkEngine.sendControl('reset');
            
            // UI恢复默认
            this.isPaused = false;
            btnToggle.innerText = 'PAUSE 暂停演化';
            btnToggle.classList.remove('active-btn');
        });
    }

    registerEventHooks() {
        document.addEventListener('baselineSynced', () => {
            this.renderer.drawStaticTopology(GlobalState.wallData);
            // 重置时立刻清空之前的火场残留并强制重绘环境
            this.renderer.updateEnvironment();
        });

        document.addEventListener('tickUpdated', () => {
            this.renderer.updateEnvironment();
        });
    }

    startRenderLoop() {
        const loop = (timestamp) => {
            this.renderer.clearMainLayer();

            if (GlobalState.isBaselineSynced) {
                this.splineEngine.renderChains(this.renderer.mainCtx, timestamp);
            }

            this.animationFrameId = requestAnimationFrame(loop);
        };
        requestAnimationFrame(loop);
    }
}

window.addEventListener('DOMContentLoaded', () => {
    const conductor = new EngineConductor();
    conductor.bootstrap();
});