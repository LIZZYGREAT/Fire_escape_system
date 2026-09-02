import { GlobalState } from './network.js';

const PALETTE = {
    background: '#f7faf9',
    grid: 'rgba(71, 85, 105, 0.075)',
    wall: '#26343d',
    exit: '#10b981',
    exitDark: '#047857',
    smokeRgb: [71, 85, 105],
    smokeLightRgb: [148, 163, 184],
    sosDensity: 'rgba(220, 38, 38, 0.20)',
    sosBorder: 'rgba(185, 28, 28, 0.86)'
};

const clamp01 = (value) => Math.max(0, Math.min(1, value));
const mix = (a, b, t) => Math.round(a + (b - a) * t);

export class SceneRenderer {
    constructor(containerId, gridWidth, gridHeight, cellSize = 3) {
        this.container = document.getElementById(containerId);
        this.cellSize = cellSize;
        this.bgCanvas = this._createCanvas(1, 'bg-layer');
        this.envCanvas = this._createCanvas(2, 'env-layer');
        this.mainCanvas = this._createCanvas(3, 'spline-layer');
        this.bgCtx = this.bgCanvas.getContext('2d', { alpha: false });
        this.envCtx = this.envCanvas.getContext('2d');
        this.mainCtx = this.mainCanvas.getContext('2d');
        this.resizeGrid(gridWidth, gridHeight);
        this.drawStaticTopology([]);
    }

    _createCanvas(zIndex, id) {
        const canvas = document.createElement('canvas');
        canvas.id = id;
        Object.assign(canvas.style, { position: 'absolute', inset: '0', zIndex: String(zIndex) });
        this.container.appendChild(canvas);
        return canvas;
    }

    resizeGrid(width, height) {
        const nextWidth = Math.max(1, Number(width) || 1);
        const nextHeight = Math.max(1, Number(height) || 1);
        if (this.gridWidth === nextWidth && this.gridHeight === nextHeight) return;
        this.gridWidth = nextWidth;
        this.gridHeight = nextHeight;
        this.pixelWidth = this.gridWidth * this.cellSize;
        this.pixelHeight = this.gridHeight * this.cellSize;
        [this.bgCanvas, this.envCanvas, this.mainCanvas].forEach((canvas) => {
            canvas.width = this.pixelWidth;
            canvas.height = this.pixelHeight;
        });
        this.container.style.aspectRatio = `${this.gridWidth} / ${this.gridHeight}`;
    }

    drawStaticTopology(wallCoordinates) {
        const ctx = this.bgCtx;
        ctx.fillStyle = PALETTE.background;
        ctx.fillRect(0, 0, this.pixelWidth, this.pixelHeight);
        ctx.strokeStyle = PALETTE.grid;
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        for (let x = 0; x <= this.pixelWidth; x += this.cellSize * 10) {
            ctx.moveTo(x, 0); ctx.lineTo(x, this.pixelHeight);
        }
        for (let y = 0; y <= this.pixelHeight; y += this.cellSize * 10) {
            ctx.moveTo(0, y); ctx.lineTo(this.pixelWidth, y);
        }
        ctx.stroke();
        ctx.fillStyle = PALETTE.wall;
        for (const [x, y] of wallCoordinates) {
            ctx.fillRect(x * this.cellSize, y * this.cellSize, this.cellSize, this.cellSize);
        }
    }

    _drawDynamicExits(timestamp) {
        const pulse = (Math.sin(timestamp / 320) + 1) / 2;
        const ctx = this.mainCtx;
        ctx.save();
        for (const [x, y] of GlobalState.exitsData || []) {
            const cx = (x + 0.5) * this.cellSize;
            const cy = (y + 0.5) * this.cellSize;
            const radius = this.cellSize * (4.5 + pulse * 2.5);
            const halo = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
            halo.addColorStop(0, `rgba(16, 185, 129, ${0.28 - pulse * 0.08})`);
            halo.addColorStop(1, 'rgba(16, 185, 129, 0)');
            ctx.fillStyle = halo;
            ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.fill();
            const size = Math.max(8, this.cellSize * 3.2);
            ctx.fillStyle = PALETTE.exit;
            ctx.strokeStyle = PALETTE.exitDark;
            ctx.lineWidth = Math.max(1, this.cellSize * 0.55);
            ctx.fillRect(cx - size / 2, cy - size / 2, size, size);
            ctx.strokeRect(cx - size / 2, cy - size / 2, size, size);
            ctx.fillStyle = '#fff';
            ctx.font = `700 ${Math.max(7, size * 0.56)}px sans-serif`;
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText('出', cx, cy + 0.5);
        }
        ctx.restore();
    }

    updateEnvironment() {
        const ctx = this.envCtx;
        ctx.clearRect(0, 0, this.pixelWidth, this.pixelHeight);

        ctx.save();
        ctx.globalCompositeOperation = 'source-over';
        GlobalState.smokeMap.forEach((smoke, key) => {
            if (smoke < 0.5) return;
            const [x, y] = key.split(',').map(Number);
            const t = clamp01(smoke / 100);
            const rgb = PALETTE.smokeLightRgb.map((value, index) => mix(value, PALETTE.smokeRgb[index], t));
            const cx = (x + 0.5) * this.cellSize;
            const cy = (y + 0.5) * this.cellSize;
            const radius = this.cellSize * (2.5 + t * 5.5);
            const cloud = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
            cloud.addColorStop(0, `rgba(${rgb.join(',')}, ${0.14 + t * 0.48})`);
            cloud.addColorStop(0.55, `rgba(${rgb.join(',')}, ${0.06 + t * 0.26})`);
            cloud.addColorStop(1, `rgba(${rgb.join(',')}, 0)`);
            ctx.fillStyle = cloud;
            ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.fill();
        });

        GlobalState.heatMap.forEach((heat, key) => {
            const excess = Math.max(0, heat - 1);
            if (excess < 0.8) return;
            const [x, y] = key.split(',').map(Number);
            const ignition = clamp01(excess / 70);
            const red = 245;
            const green = mix(196, 45, ignition);
            const alpha = 0.12 + ignition * 0.76;
            ctx.fillStyle = `rgba(${red}, ${green}, 20, ${alpha})`;
            ctx.fillRect(x * this.cellSize, y * this.cellSize, this.cellSize, this.cellSize);
        });

        const fireCores = [...GlobalState.heatMap.entries()]
            .filter(([, heat]) => heat >= 50)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 180);
        for (const [key, heat] of fireCores) {
            const [x, y] = key.split(',').map(Number);
            const cx = (x + 0.5) * this.cellSize;
            const cy = (y + 0.5) * this.cellSize;
            const t = clamp01((heat - 50) / 100);
            const radius = this.cellSize * (1.8 + t * 1.5);
            const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
            glow.addColorStop(0, 'rgba(255, 250, 205, 0.98)');
            glow.addColorStop(0.28, `rgba(251, 146, 60, ${0.9 - t * 0.1})`);
            glow.addColorStop(0.72, `rgba(220, 38, 38, ${0.64 + t * 0.2})`);
            glow.addColorStop(1, 'rgba(127, 29, 29, 0)');
            ctx.fillStyle = glow;
            ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.fill();
        }
        ctx.restore();

        if (GlobalState.wallData?.length) {
            ctx.save();
            ctx.globalCompositeOperation = 'destination-out';
            ctx.fillStyle = '#fff';
            for (const [x, y] of GlobalState.wallData) {
                ctx.fillRect(x * this.cellSize, y * this.cellSize, this.cellSize, this.cellSize);
            }
            ctx.restore();
        }
    }

    _getAlertRadius(gx, gy) {
        let nearest = 25;
        GlobalState.heatMap.forEach((heat, key) => {
            if (heat < 50) return;
            const [fx, fy] = key.split(',').map(Number);
            nearest = Math.min(nearest, Math.hypot(fx - gx, fy - gy));
        });
        const boundary = Math.max(1, Math.min(gx, gy, this.gridWidth - gx, this.gridHeight - gy));
        return Math.max(2, Math.min(nearest, boundary, 25));
    }

    _drawPeopleDensity(timestamp) {
        const sources = [...GlobalState.topologyTree.entries()]
            .filter(([, data]) => data.status === 2 || data.mode === 'SOS')
            .map(([key]) => key);
        if (!sources.length) return;
        const ctx = this.mainCtx;
        ctx.save();
        sources.forEach((key) => {
            const [x, y] = key.split(',').map(Number);
            const cx = (x + 0.5) * this.cellSize;
            const cy = (y + 0.5) * this.cellSize;
            const radius = this.cellSize * this._getAlertRadius(x, y);
            const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
            gradient.addColorStop(0, PALETTE.sosDensity);
            gradient.addColorStop(1, 'rgba(220, 38, 38, 0)');
            ctx.fillStyle = gradient;
            ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.fill();
            ctx.strokeStyle = PALETTE.sosBorder;
            ctx.lineWidth = 1;
            ctx.setLineDash([this.cellSize * 2, this.cellSize * 1.5]);
            ctx.lineDashOffset = -timestamp / 70;
            ctx.beginPath(); ctx.arc(cx, cy, radius * 0.84, 0, Math.PI * 2); ctx.stroke();
        });
        ctx.restore();
    }

    clearMainLayer(timestamp = 0) {
        this.mainCtx.clearRect(0, 0, this.pixelWidth, this.pixelHeight);
        this._drawPeopleDensity(timestamp);
        this._drawDynamicExits(timestamp);
    }
}
