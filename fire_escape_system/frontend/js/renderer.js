// frontend/js/renderer.js
import { GlobalState } from './network.js';

const PALETTE = {
    bg: '#FFFFFF',
    grid: 'rgba(0, 0, 0, 0.05)',
    wall: '#000000',
    exitNeon: '#00FF00', 
    exitCore: '#FFFFFF',
    
    // 烟雾颜色
    smokeDense: 'rgba(155, 89, 182, 0.40)',    
    smokeFringe: 'rgba(155, 89, 182, 0)',      
    
    // SOS 区域：群众必须停止盲目突围并等待消防搜救
    sosDensity: 'rgba(220, 38, 38, 0.28)',
    sosBorder: 'rgba(185, 28, 28, 0.92)'
};

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

    drawStaticTopology(wallCoordinates) {
        this.bgCtx.fillStyle = PALETTE.bg;
        this.bgCtx.fillRect(0, 0, this.pixelWidth, this.pixelHeight);
        
        this.bgCtx.strokeStyle = PALETTE.grid;
        this.bgCtx.lineWidth = 0.5;
        this.bgCtx.beginPath();
        for (let x = 0; x <= this.pixelWidth; x += this.cellSize * 5) {
            this.bgCtx.moveTo(x, 0); this.bgCtx.lineTo(x, this.pixelHeight);
        }
        for (let y = 0; y <= this.pixelHeight; y += this.cellSize * 5) {
            this.bgCtx.moveTo(0, y); this.bgCtx.lineTo(this.pixelWidth, y);
        }
        this.bgCtx.stroke();

        this.bgCtx.fillStyle = PALETTE.wall;
        for (const [x, y] of wallCoordinates) {
            this.bgCtx.fillRect(x * this.cellSize, y * this.cellSize, this.cellSize, this.cellSize);
        }
    }

    _drawDynamicExits(timestamp) {
        if (!GlobalState.exitsData) return;

        const pulse = (Math.sin(timestamp / 200) + 1) / 2;
        
        this.mainCtx.save();
        for (const [x, y] of GlobalState.exitsData) {
            const cx = x * this.cellSize + this.cellSize / 2;
            const cy = y * this.cellSize + this.cellSize / 2;
            
            const rippleSize = this.cellSize * (6 + pulse * 6);
            const rippleGrad = this.mainCtx.createRadialGradient(cx, cy, 0, cx, cy, rippleSize);
            rippleGrad.addColorStop(0, `rgba(0, 255, 0, ${0.3 * (1 - pulse)})`);
            rippleGrad.addColorStop(1, 'rgba(0, 255, 0, 0)');
            
            this.mainCtx.fillStyle = rippleGrad;
            this.mainCtx.beginPath();
            this.mainCtx.arc(cx, cy, rippleSize, 0, Math.PI * 2);
            this.mainCtx.fill();

            const bodySize = this.cellSize * 5; 
            
            this.mainCtx.shadowBlur = 15;
            this.mainCtx.shadowColor = PALETTE.exitNeon;
            this.mainCtx.strokeStyle = PALETTE.exitNeon;
            this.mainCtx.lineWidth = 3;
            this.mainCtx.strokeRect(cx - bodySize/2, cy - bodySize/2, bodySize, bodySize);

            this.mainCtx.fillStyle = 'rgba(0, 255, 0, 0.8)';
            this.mainCtx.fillRect(cx - bodySize/2, cy - bodySize/2, bodySize, bodySize);

            this.mainCtx.shadowBlur = 0;
            this.mainCtx.fillStyle = PALETTE.exitCore;
            const coreW = bodySize * 0.5;
            const coreH = bodySize * 0.7;
            this.mainCtx.fillRect(cx - coreW/2, cy - coreH/2, coreW, coreH);
        }
        this.mainCtx.restore();
    }

    // 独立的方法 1：动态火场颜色插值逻辑
    _getDynamicFireColor(weight) {
        const minW = 70.0;
        const maxW = 150.0;
        const ratio = Math.max(0, Math.min(1, (weight - minW) / (maxW - minW)));

        // 从 明黄色 (255, 215, 0) 过渡到 橙红色 (255, 69, 0)
        const r = 255;
        const g = Math.round(215 - ratio * (215 - 69)); 
        const b = 0;
        const alpha = 0.8 + ratio * 0.15; 
        
        return {
            core: `rgba(${r}, ${g}, ${b}, ${alpha})`,
            fringe: `rgba(${r}, ${g}, ${b}, 0)`
        };
    }

    // 独立的方法 2：基于火场距离与地图边界的三重约束半径计算
    _getDynamicAlertRadius(gx, gy) {
        let minDistSq = Infinity;
        
        // 1. 计算到最近火场的距离
        GlobalState.fireMap.forEach((weight, coordStr) => {
            if (weight >= 70.0) {
                const [fx, fy] = coordStr.split(',').map(Number);
                const dx = fx - gx;
                const dy = fy - gy;
                const distSq = dx * dx + dy * dy;
                if (distSq < minDistSq) {
                    minDistSq = distSq;
                }
            }
        });

        // 距离作为直径，故半径为距离的一半
        const distFire = (minDistSq === Infinity) ? 30.0 : Math.sqrt(minDistSq);
        const radiusFromFire = distFire;

        // 2. 计算到地图绝对边界 [10, 240] 的正交距离
        const distBoundaryX = Math.min(gx - 10, 240 - gx);
        const distBoundaryY = Math.min(gy - 10, 240 - gy);
        const radiusFromBoundary = Math.max(0, Math.min(distBoundaryX, distBoundaryY));

        // 3. 最大半径上限值熔断
        const MAX_RADIUS = 25.0;

        // 核心约束：三者取最小值
        const finalGridRadius = Math.min(radiusFromFire, radiusFromBoundary, MAX_RADIUS);

        // 严格防止 Canvas 渲染崩溃的保底断言
        return Math.max(1.0, finalGridRadius); 
    }

    updateEnvironment() {
        this.envCtx.clearRect(0, 0, this.pixelWidth, this.pixelHeight);
        this.envCtx.globalCompositeOperation = 'source-over';

        const smokeNodes = [];
        const fireNodes = [];

        GlobalState.fireMap.forEach((weight, coordStr) => {
            if (weight < 1.5) return;
            const [x, y] = coordStr.split(',').map(Number);
            const cx = x * this.cellSize + this.cellSize / 2;
            const cy = y * this.cellSize + this.cellSize / 2;
            
            if (weight >= 70.0) {
                fireNodes.push({ cx, cy, weight });
            } else {
                smokeNodes.push({ cx, cy, weight });
            }
        });

        // 1. 绘制底层紫烟
        smokeNodes.forEach(node => {
            const radius = this.cellSize * Math.min(node.weight / 10, 5.0);
            const gradient = this.envCtx.createRadialGradient(node.cx, node.cy, 0, node.cx, node.cy, radius);
            gradient.addColorStop(0, PALETTE.smokeDense);
            gradient.addColorStop(1, PALETTE.smokeFringe);
            
            this.envCtx.fillStyle = gradient;
            this.envCtx.beginPath();
            this.envCtx.arc(node.cx, node.cy, radius, 0, Math.PI * 2);
            this.envCtx.fill();
        });

        // 2. 绘制顶层动态火场 (调用 _getDynamicFireColor 映射色彩)
        fireNodes.forEach(node => {
            const radius = this.cellSize * 2.2; 
            const fireColors = this._getDynamicFireColor(node.weight);

            const gradient = this.envCtx.createRadialGradient(node.cx, node.cy, 0, node.cx, node.cy, radius);
            gradient.addColorStop(0, fireColors.core);
            gradient.addColorStop(1, fireColors.fringe);
            
            this.envCtx.fillStyle = gradient;
            this.envCtx.beginPath();
            this.envCtx.arc(node.cx, node.cy, radius, 0, Math.PI * 2);
            this.envCtx.fill();
        });

        // 3. 墙体防遮挡镂空逻辑 (Alpha Clipping)
        if (GlobalState.wallData && GlobalState.wallData.length > 0) {
            this.envCtx.globalCompositeOperation = 'destination-out';
            this.envCtx.fillStyle = '#FFFFFF'; 
            
            for (const [x, y] of GlobalState.wallData) {
                this.envCtx.fillRect(x * this.cellSize, y * this.cellSize, this.cellSize, this.cellSize);
            }
            this.envCtx.globalCompositeOperation = 'source-over'; 
        }
    }

    _drawPeopleDensity(timestamp) {
        const densitySources = [];
        GlobalState.topologyTree.forEach((data, key) => {
            if (data.status === 2 || data.mode === 'SOS') densitySources.push(key);
        });
        
        if (densitySources.length === 0) return;

        this.mainCtx.save();
        const dashOffset = -(timestamp / 50.0);

        densitySources.forEach(coordStr => {
            const [x, y] = coordStr.split(',').map(Number);
            const cx = x * this.cellSize + this.cellSize / 2;
            const cy = y * this.cellSize + this.cellSize / 2;
            
            // 调用多重约束函数获取安全的网格半径
            const gridRadius = this._getDynamicAlertRadius(x, y);
            const radius = this.cellSize * gridRadius;
            
            const gradient = this.mainCtx.createRadialGradient(cx, cy, 0, cx, cy, radius);
            gradient.addColorStop(0, PALETTE.sosDensity);
            gradient.addColorStop(1, 'rgba(220, 38, 38, 0)');
            
            this.mainCtx.fillStyle = gradient;
            this.mainCtx.beginPath();
            this.mainCtx.arc(cx, cy, radius, 0, Math.PI * 2);
            this.mainCtx.fill();

            this.mainCtx.strokeStyle = PALETTE.sosBorder;
            this.mainCtx.lineWidth = 1;
            this.mainCtx.setLineDash([this.cellSize * 2, this.cellSize * 1.5]);
            this.mainCtx.lineDashOffset = dashOffset;
            this.mainCtx.beginPath();
            this.mainCtx.arc(cx, cy, radius * 0.85, 0, Math.PI * 2);
            this.mainCtx.stroke();
        });
        
        this.mainCtx.restore();
    }

    clearMainLayer(timestamp = 0) {
        this.mainCtx.clearRect(0, 0, this.pixelWidth, this.pixelHeight);
        
        this._drawPeopleDensity(timestamp);
        this._drawDynamicExits(timestamp);
    }
}
