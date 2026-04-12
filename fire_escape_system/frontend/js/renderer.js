// frontend/js/renderer.js
import { GlobalState } from './network.js';

export class SceneRenderer {
    constructor(containerId, gridWidth, gridHeight, cellSize = 15) {
        this.container = document.getElementById(containerId);
        this.gridWidth = gridWidth;
        this.gridHeight = gridHeight;
        this.cellSize = cellSize;
        
        this.pixelWidth = this.gridWidth * this.cellSize;
        this.pixelHeight = this.gridHeight * this.cellSize;

        this.bgCanvas = this._createCanvas(1, 'bg-layer');         
        this.envCanvas = this._createCanvas(2, 'env-layer');       
        this.mainCanvas = this._createCanvas(3, 'spline-layer');   
        
        this.bgCtx = this.bgCanvas.getContext('2d', { alpha: false }); 
        this.envCtx = this.envCanvas.getContext('2d');
        this.mainCtx = this.mainCanvas.getContext('2d');
    }

    _createCanvas(zIndex, id) {
        const canvas = document.createElement('canvas');
        canvas.id = id;
        canvas.width = this.pixelWidth;
        canvas.height = this.pixelHeight;
        canvas.style.position = 'absolute';
        canvas.style.top = '0';
        canvas.style.left = '0';
        canvas.style.zIndex = zIndex.toString();
        this.container.appendChild(canvas);
        return canvas;
    }

    // --- Layer 1: 静态拓扑层 (蓝图风重构) ---
    
    drawStaticTopology(wallCoordinates) {
        // 1. 深色空间底色
        this.bgCtx.fillStyle = '#0b0c10'; 
        this.bgCtx.fillRect(0, 0, this.pixelWidth, this.pixelHeight);
        
        // 2. 绘制细微的科技网格 (Blueprint Grid)
        this.bgCtx.strokeStyle = 'rgba(69, 162, 158, 0.08)';
        this.bgCtx.lineWidth = 0.5;
        this.bgCtx.beginPath();
        for (let x = 0; x <= this.pixelWidth; x += this.cellSize * 5) {
            this.bgCtx.moveTo(x, 0); this.bgCtx.lineTo(x, this.pixelHeight);
        }
        for (let y = 0; y <= this.pixelHeight; y += this.cellSize * 5) {
            this.bgCtx.moveTo(0, y); this.bgCtx.lineTo(this.pixelWidth, y);
        }
        this.bgCtx.stroke();

        // 3. 渲染“玻璃态线框”墙体
        this.bgCtx.fillStyle = 'rgba(31, 40, 51, 0.7)'; // 半透填充
        this.bgCtx.strokeStyle = '#45a29e'; // 科技感描边
        this.bgCtx.lineWidth = 1.0;
        
        for (const [x, y] of wallCoordinates) {
            const px = x * this.cellSize;
            const py = y * this.cellSize;
            this.bgCtx.fillRect(px, py, this.cellSize, this.cellSize);
            // 绘制带有 0.5px 偏移的精准线框
            this.bgCtx.strokeRect(px + 0.5, py + 0.5, this.cellSize - 1, this.cellSize - 1);
        }

        // 4. 渲染极光绿安全出口 (Neon Exit)
        if (GlobalState.exitsData && GlobalState.exitsData.length > 0) {
            this.bgCtx.shadowBlur = 20;
            this.bgCtx.shadowColor = '#66fcf1';
            this.bgCtx.fillStyle = '#66fcf1';
            
            for (const [x, y] of GlobalState.exitsData) {
                this.bgCtx.fillRect(x * this.cellSize, y * this.cellSize, this.cellSize, this.cellSize);
                // 绘制外围呼吸框
                this.bgCtx.strokeStyle = 'rgba(102, 252, 241, 0.5)';
                this.bgCtx.lineWidth = 2;
                this.bgCtx.strokeRect(x * this.cellSize - 2, y * this.cellSize - 2, this.cellSize + 4, this.cellSize + 4);
            }
            this.bgCtx.shadowBlur = 0;
        }
        
        console.log("[Renderer] Layer 1: 静态蓝图图层重构完毕。");
    }

    // --- Layer 2: 环境流体层 ---

    updateEnvironment() {
        this.envCtx.clearRect(0, 0, this.pixelWidth, this.pixelHeight);
        this.envCtx.globalCompositeOperation = 'screen';

        GlobalState.fireMap.forEach((weight, coordStr) => {
            if (weight < 1.5) return;

            const [xStr, yStr] = coordStr.split(',');
            const cx = parseInt(xStr, 10) * this.cellSize + this.cellSize / 2;
            const cy = parseInt(yStr, 10) * this.cellSize + this.cellSize / 2;
            
            const radius = this.cellSize * Math.min(weight / 20, 3.5);
            const gradient = this.envCtx.createRadialGradient(cx, cy, 0, cx, cy, radius);
            
            if (weight < 70.0) { // 烟雾：深紫色氛围
                const alpha = Math.min(weight / 70.0, 0.5);
                gradient.addColorStop(0, `rgba(138, 43, 226, ${alpha})`);
                gradient.addColorStop(1, 'rgba(138, 43, 226, 0)');
            } else { // 核心火场：高对比度橙红
                gradient.addColorStop(0, 'rgba(255, 165, 0, 0.9)');
                gradient.addColorStop(0.4, 'rgba(255, 69, 0, 0.4)');
                gradient.addColorStop(1, 'rgba(255, 69, 0, 0)');
            }

            this.envCtx.fillStyle = gradient;
            this.envCtx.beginPath();
            this.envCtx.arc(cx, cy, radius, 0, Math.PI * 2);
            this.envCtx.fill();
        });

        this.envCtx.globalCompositeOperation = 'source-over'; 
    }

    clearMainLayer() {
        this.mainCtx.clearRect(0, 0, this.pixelWidth, this.pixelHeight);
    }
}