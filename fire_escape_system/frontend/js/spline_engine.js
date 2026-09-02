// frontend/js/spline_engine.js
import { GlobalState } from './network.js';

export class SplineEngine {
    constructor(cellSize) {
        this.cellSize = cellSize;
        // 颜色映射表 - 严格对应原理图要求
        this.statusColors = {
            0: { path: '#27ae60', glow: 'rgba(39, 174, 96, 0.4)', node: '#2ecc71' }, // 绿色 (安全)
            1: { path: '#c0392b', glow: 'rgba(192, 57, 43, 0.4)', node: '#e74c3c' }, // 红色 (火焰)
            2: { path: '#b91c1c', glow: 'rgba(185, 28, 28, 0.45)', node: '#dc2626' }, // 红色 (SOS，停止突围)
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
            this._drawNodeIndicator(ctx, cx, cy, value.status, value.mode);
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
                const isRescueView = GlobalState.guidanceMode === 'rescue';
                const isSos = data.status === 2 || data.mode === 'SOS';
                // 群众视图绝不读取 rescue_next；SOS 节点也不再绘制任何突围路径。
                const nextId = isRescueView
                    // SOS 的救援边先接到安全节点，随后沿该节点的公共安全链继续到出口。
                    ? (data.rescue_next ?? data.rescueNext ?? data.next ?? null)
                    : (isSos ? null : data.next);
                if (!nextId) break;

                const edgeKey = `${GlobalState.guidanceMode}:${currentId}->${nextId}`;

                if (!renderedEdges.has(edgeKey)) {
                    renderedEdges.add(edgeKey);

                    const colors = isRescueView
                        ? (data.status === 1
                            ? { path: '#dc2626', glow: 'rgba(220, 38, 38, .45)' }
                            : { path: '#ea580c', glow: 'rgba(234, 88, 12, .4)' })
                        : (this.statusColors[data.status] || this.statusColors[0]);
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
                    ctx.setLineDash(isRescueView
                        ? [this.cellSize * 2, this.cellSize * 2.4]
                        : [this.cellSize * 4, this.cellSize * 4]);
                    ctx.lineDashOffset = flowOffset;
                    ctx.stroke();

                    // 指向箭头
                    // Physical black boxes only support the four cardinal
                    // commands. Keep the route geometry exact, but render the
                    // device command as N/E/S/W instead of a diagonal arrow.
                    const rescueDirection = Number(data.rescue_dir ?? data.rescueDir ?? -1);
                    const commandDirection = isRescueView && rescueDirection >= 0
                        ? rescueDirection
                        : Number(data.dir ?? -1);
                    this._drawDirectionArrow(
                        ctx,
                        startX,
                        startY,
                        endX,
                        endY,
                        colors.path,
                        commandDirection
                    );

                    ctx.shadowBlur = 0;
                    ctx.setLineDash([]);
                }
                currentId = nextId;
            }
        });
    }

    _drawDirectionArrow(ctx, startX, startY, endX, endY, color, direction = -1) {
        const cardinalAngles = [-Math.PI / 2, 0, Math.PI / 2, Math.PI];
        let angle = cardinalAngles[direction];
        if (!Number.isFinite(angle)) {
            const dx = endX - startX;
            const dy = endY - startY;
            angle = Math.abs(dx) >= Math.abs(dy)
                ? (dx >= 0 ? 0 : Math.PI)
                : (dy >= 0 ? Math.PI / 2 : -Math.PI / 2);
        }
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

    _drawNodeIndicator(ctx, cx, cy, status, mode = null) {
        const isSos = status === 2 || mode === 'SOS';
        const colors = this.statusColors[isSos ? 2 : status] || this.statusColors[0];

        ctx.shadowBlur = 10;
        ctx.shadowColor = colors.glow;
        ctx.fillStyle = colors.node;
        ctx.beginPath();
        ctx.arc(cx, cy, this.cellSize * 1.6, 0, Math.PI * 2);
        ctx.fill();
        
        ctx.shadowBlur = 0;
        if (isSos) {
            ctx.fillStyle = '#fff';
            ctx.font = `800 ${Math.max(8, this.cellSize * 1.45)}px system-ui, sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText('SOS', cx, cy);
        } else {
            // 节点中心的高亮白点（模拟灯光效果）
            ctx.fillStyle = 'rgba(255, 255, 255, 0.8)';
            ctx.beginPath();
            ctx.arc(cx - this.cellSize * 0.4, cy - this.cellSize * 0.4, this.cellSize * 0.5, 0, Math.PI * 2);
            ctx.fill();
        }
    }
}
