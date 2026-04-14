// frontend/js/spline_engine.js
import { GlobalState } from './network.js';

export class SplineEngine {
    constructor(cellSize) {
        this.cellSize = cellSize;
        // 颜色映射表 - 严格对应原理图要求
        this.statusColors = {
            0: { path: '#27ae60', glow: 'rgba(39, 174, 96, 0.4)', node: '#2ecc71' }, // 绿色 (安全)
            1: { path: '#c0392b', glow: 'rgba(192, 57, 43, 0.4)', node: '#e74c3c' }, // 红色 (火焰)
            2: { path: '#d48d00', glow: 'rgba(212, 141, 0, 0.4)', node: '#f1c40f' }, // 暗黄 (被困)
            3: { path: '#8e44ad', glow: 'rgba(142, 68, 173, 0.4)', node: '#9b59b6' }  // 紫色 (烟雾)
        };
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
        const flowOffset = - (timestamp / 40.0);
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

                    const colors = this.statusColors[data.status] || this.statusColors[0];
                    const [startX, startY] = this._getPixelCoord(currentId);
                    const [endX, endY] = this._getPixelCoord(nextId);

                    ctx.beginPath();
                    ctx.moveTo(startX, startY);
                    ctx.lineTo(endX, endY);

                    // 在白底上，阴影需要稍微深一点或减小范围以避免“脏”感
                    ctx.shadowBlur = 8;
                    ctx.shadowColor = colors.glow;
                    ctx.strokeStyle = colors.path;
                    ctx.lineWidth = this.cellSize * 1.2;

                    // 逃生路线流光
                    ctx.setLineDash([this.cellSize * 4, this.cellSize * 4]);
                    ctx.lineDashOffset = flowOffset;
                    ctx.stroke();

                    // 指向箭头
                    this._drawDirectionArrow(ctx, startX, startY, endX, endY, colors.path);

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
        const colors = this.statusColors[status] || this.statusColors[0];

        ctx.shadowBlur = 10;
        ctx.shadowColor = colors.glow;
        ctx.fillStyle = colors.node;
        ctx.beginPath();
        ctx.arc(cx, cy, this.cellSize * 1.6, 0, Math.PI * 2);
        ctx.fill();
        
        ctx.shadowBlur = 0;
        // 节点中心的高亮白点（模拟灯光效果）
        ctx.fillStyle = 'rgba(255, 255, 255, 0.8)';
        ctx.beginPath();
        ctx.arc(cx - this.cellSize * 0.4, cy - this.cellSize * 0.4, this.cellSize * 0.5, 0, Math.PI * 2);
        ctx.fill();
    }
}