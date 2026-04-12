// frontend/js/spline_engine.js
import { GlobalState } from './network.js';

export class SplineEngine {
    constructor(cellSize) {
        this.cellSize = cellSize;
    }

    renderChains(ctx, timestamp) {
        if (!GlobalState.topologyTree || GlobalState.topologyTree.size === 0) return;

        ctx.save();
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round'; 
        
        this._drawFlowingPaths(ctx, timestamp);

        GlobalState.topologyTree.forEach((value, key) => {
            const [cx, cy] = this._getPixelCoord(key);
            this._drawNodeIndicator(ctx, cx, cy, value.status);
        });

        ctx.restore();
    }

    _drawFlowingPaths(ctx, timestamp) {
        const flowOffset = - (timestamp / 50.0);
        const renderedEdges = new Set(); 

        GlobalState.topologyTree.forEach((_, startNodeId) => {
            let currentId = startNodeId;
            let visited = new Set();

            while (currentId && GlobalState.topologyTree.has(currentId)) {
                if (visited.has(currentId)) break;
                visited.add(currentId);

                const data = GlobalState.topologyTree.get(currentId);
                if (!data.next) break;

                const nextId = data.next;
                const edgeKey = `${currentId}->${nextId}`;

                if (!renderedEdges.has(edgeKey)) {
                    renderedEdges.add(edgeKey);

                    // --- 【修复关键】：在循环内部实时判定当前边的颜色 ---
                    let pathColor, glowColor;
                    const status = data.status;
                    if (status === 1) { pathColor = '#ff3232'; glowColor = 'rgba(255, 50, 50, 0.6)'; }
                    else if (status === 2) { pathColor = '#ffaa00'; glowColor = 'rgba(255, 170, 0, 0.6)'; }
                    else if (status === 3) { pathColor = '#d000ff'; glowColor = 'rgba(208, 0, 255, 0.6)'; }
                    else { pathColor = '#00e5ff'; glowColor = 'rgba(0, 229, 255, 0.6)'; }

                    const [startX, startY] = this._getPixelCoord(currentId);
                    const [endX, endY] = this._getPixelCoord(nextId);

                    ctx.beginPath();
                    ctx.moveTo(startX, startY);
                    ctx.lineTo(endX, endY);

                    ctx.shadowBlur = 12;
                    ctx.shadowColor = glowColor;
                    ctx.strokeStyle = pathColor;
                    ctx.lineWidth = this.cellSize * 1.0;

                    // 逃生路线流光
                    ctx.setLineDash([this.cellSize * 5, this.cellSize * 3]);
                    ctx.lineDashOffset = flowOffset;
                    ctx.stroke();

                    // 指向箭头
                    this._drawDirectionArrow(ctx, startX, startY, endX, endY, pathColor);

                    ctx.shadowBlur = 0;
                    ctx.setLineDash([]);
                }
                currentId = nextId;
            }
        });
    }

    _drawDirectionArrow(ctx, startX, startY, endX, endY, color) {
        const angle = Math.atan2(endY - startY, endX - startX);
        const midX = startX + (endX - startX) * 0.7;
        const midY = startY + (endY - startY) * 0.7;
        const headLen = this.cellSize * 2.5;

        ctx.save();
        ctx.setLineDash([]); 
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(midX + Math.cos(angle) * headLen, midY + Math.sin(angle) * headLen);
        ctx.lineTo(midX - Math.cos(angle - Math.PI / 6) * headLen, midY - Math.sin(angle - Math.PI / 6) * headLen);
        ctx.lineTo(midX - Math.cos(angle + Math.PI / 6) * headLen, midY - Math.sin(angle + Math.PI / 6) * headLen);
        ctx.fill();
        ctx.restore();
    }

    _getPixelCoord(nodeStr) {
        const [x, y] = nodeStr.split(',').map(Number);
        return [x * this.cellSize + this.cellSize / 2, y * this.cellSize + this.cellSize / 2];
    }

    _drawNodeIndicator(ctx, cx, cy, status) {
        let coreColor, glowColor;
        if (status === 1) { coreColor = '#ff003c'; glowColor = 'rgba(255, 0, 60, 0.8)'; }
        else if (status === 2) { coreColor = '#ffaa00'; glowColor = 'rgba(255, 170, 0, 0.8)'; }
        else if (status === 3) { coreColor = '#d000ff'; glowColor = 'rgba(208, 0, 255, 0.8)'; }
        else { coreColor = '#00e5ff'; glowColor = 'rgba(0, 229, 255, 0.8)'; }

        ctx.shadowBlur = 15;
        ctx.shadowColor = glowColor;
        ctx.fillStyle = coreColor;
        ctx.beginPath();
        ctx.arc(cx, cy, this.cellSize * 1.5, 0, Math.PI * 2);
        ctx.fill();
        
        ctx.shadowBlur = 0;
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(cx - this.cellSize * 0.3, cy - this.cellSize * 0.3, this.cellSize * 0.4, 0, Math.PI * 2);
        ctx.fill();
    }
}