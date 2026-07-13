import { createEntity, createId } from './state_store.js';

const ENTITY_TOOLS = {
    exit: 'exits',
    refuge: 'refuges',
    stair: 'stairs',
    door: 'doors',
    gateway: 'gateways',
    blackBox: 'blackBoxes'
};

const ENTITY_STYLES = {
    exits: { color: '#16a34a', fill: '#dcfce7', glyph: '出' },
    refuges: { color: '#0f766e', fill: '#ccfbf1', glyph: '避' },
    stairs: { color: '#7e22ce', fill: '#f3e8ff', glyph: '梯' },
    doors: { color: '#b45309', fill: '#fef3c7', glyph: '门' },
    gateways: { color: '#0369a1', fill: '#e0f2fe', glyph: '网' },
    blackBoxes: { color: '#111827', fill: '#fbbf24', glyph: 'B' }
};

const DEFAULT_LAYERS = {
    base: true,
    grid: true,
    walkable: true,
    walls: true,
    semantic: true,
    centerline: true,
    candidates: true,
    blackBoxes: true,
    coverage: true,
    validation: true
};

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const isPointLike = (value) => Array.isArray(value)
    ? value.length >= 2 && Number.isFinite(Number(value[0])) && Number.isFinite(Number(value[1]))
    : value && Number.isFinite(Number(value.x)) && Number.isFinite(Number(value.y));
const pointOf = (value) => Array.isArray(value)
    ? { x: Number(value[0]), y: Number(value[1]) }
    : { x: Number(value.x), y: Number(value.y) };

function extractCenterlinePaths(raw) {
    if (!raw) return [];
    if (raw.paths) return extractCenterlinePaths(raw.paths);
    if (raw.points) return extractCenterlinePaths(raw.points);
    if (raw.centerline) return extractCenterlinePaths(raw.centerline);
    if (raw.polylines) return extractCenterlinePaths(raw.polylines);
    if (raw.coordinates) return extractCenterlinePaths(raw.coordinates);
    if (!Array.isArray(raw) || raw.length === 0) return [];
    if (isPointLike(raw[0])) return [raw.map(pointOf)];
    return raw.flatMap((path) => extractCenterlinePaths(path));
}

function geometryPoint(issue, project) {
    const geometry = issue?.geometry ?? issue?.position ?? issue?.point;
    if (isPointLike(geometry)) return pointOf(geometry);
    if (Array.isArray(geometry?.points) && geometry.points.length) return pointOf(geometry.points[0]);
    const entityId = issue?.entityId ?? issue?.entity_id;
    if (entityId) {
        for (const list of Object.values(project.entities)) {
            const entity = list.find((item) => item.id === entityId);
            if (entity) return { x: entity.x, y: entity.y };
        }
    }
    return null;
}

export class CanvasMapEditor {
    constructor(canvas, store, callbacks = {}) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.store = store;
        this.callbacks = callbacks;
        this.tool = 'select';
        this.layers = { ...DEFAULT_LAYERS };
        this.view = { scale: 1, offsetX: 0, offsetY: 0 };
        this.viewport = { width: 1, height: 1, dpr: 1 };
        this.pointer = { screen: { x: 0, y: 0 }, map: { x: 0, y: 0 }, inside: false };
        this.interaction = null;
        this.selected = null;
        this.measurement = null;
        this.spacePressed = false;
        this.image = null;
        this.imageSource = '';
        this.imageLoading = false;

        this._bindEvents();
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(canvas.parentElement);
        this.unsubscribe = store.subscribe((project, meta) => {
            this._ensureImage(project.map);
            this._repairSelection(project);
            this.render();
            callbacks.onStateChange?.(project, meta);
        });
        this.resize();
        this._ensureImage(store.project.map);
    }

    destroy() {
        this.unsubscribe?.();
        this.resizeObserver?.disconnect();
    }

    resize() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        const width = Math.max(1, Math.floor(rect.width));
        const height = Math.max(1, Math.floor(rect.height));
        const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
        if (this.canvas.width !== Math.round(width * dpr) || this.canvas.height !== Math.round(height * dpr)) {
            this.canvas.width = Math.round(width * dpr);
            this.canvas.height = Math.round(height * dpr);
            this.canvas.style.width = `${width}px`;
            this.canvas.style.height = `${height}px`;
        }
        this.viewport = { width, height, dpr };
        this.render();
    }

    fitToMap() {
        const { width, height } = this.store.project.map;
        if (!width || !height) return;
        const padding = 42;
        const scaleX = Math.max(0.01, (this.viewport.width - padding * 2) / width);
        const scaleY = Math.max(0.01, (this.viewport.height - padding * 2) / height);
        this.view.scale = clamp(Math.min(scaleX, scaleY), 0.02, 40);
        this.view.offsetX = (this.viewport.width - width * this.view.scale) / 2;
        this.view.offsetY = (this.viewport.height - height * this.view.scale) / 2;
        this.render();
        this._emitViewport();
    }

    setTool(tool) {
        if (!tool) return;
        const leavingMeasurement = this.tool === 'measure' && tool !== 'measure';
        this.tool = tool;
        this.interaction = null;
        if (leavingMeasurement) {
            this.measurement = null;
            this.callbacks.onMeasurement?.(null);
        }
        this.canvas.dataset.tool = tool;
        this.callbacks.onToolChange?.(tool);
        this.render();
    }

    setLayer(name, visible) {
        if (!(name in this.layers)) return;
        this.layers[name] = Boolean(visible);
        this.render();
    }

    setSelected(reference) {
        this.selected = reference;
        this.callbacks.onSelectionChange?.(this.getSelectedEntity(), reference);
        this.render();
    }

    getSelectedEntity() {
        if (!this.selected?.collection || !this.selected?.id) return null;
        return this.store.project.entities[this.selected.collection]
            ?.find((entity) => entity.id === this.selected.id) ?? null;
    }

    updateSelected(changes, reason = 'update entity') {
        const reference = this.selected;
        if (!reference) return;
        this.store.commit((project) => {
            const entity = project.entities[reference.collection]?.find((item) => item.id === reference.id);
            if (!entity) return;
            Object.assign(entity, changes);
            entity.x = clamp(Number(entity.x) || 0, 0, project.map.width);
            entity.y = clamp(Number(entity.y) || 0, 0, project.map.height);
        }, reason);
    }

    toggleSelectedLock() {
        const entity = this.getSelectedEntity();
        if (!entity) return;
        this.updateSelected({ locked: !entity.locked }, entity.locked ? 'unlock entity' : 'lock entity');
    }

    deleteSelected() {
        const reference = this.selected;
        const entity = this.getSelectedEntity();
        if (!reference || !entity) return false;
        if (entity.locked) {
            this.callbacks.onNotice?.('对象已锁定，请先解锁再删除', 'warning');
            return false;
        }
        this.store.commit((project) => {
            project.entities[reference.collection] = project.entities[reference.collection]
                .filter((item) => item.id !== reference.id);
        }, 'delete entity');
        this.setSelected(null);
        return true;
    }

    focusPoint(point, targetScale = null) {
        if (!isPointLike(point)) return;
        const mapPoint = pointOf(point);
        if (targetScale) this.view.scale = clamp(targetScale, 0.02, 40);
        this.view.offsetX = this.viewport.width / 2 - mapPoint.x * this.view.scale;
        this.view.offsetY = this.viewport.height / 2 - mapPoint.y * this.view.scale;
        this.render();
        this._emitViewport();
    }

    focusIssue(issue) {
        const point = geometryPoint(issue, this.store.project);
        if (point) this.focusPoint(point, Math.max(this.view.scale, 2));
    }

    screenToMap(screenPoint) {
        return {
            x: (screenPoint.x - this.view.offsetX) / this.view.scale,
            y: (screenPoint.y - this.view.offsetY) / this.view.scale
        };
    }

    mapToScreen(mapPoint) {
        return {
            x: mapPoint.x * this.view.scale + this.view.offsetX,
            y: mapPoint.y * this.view.scale + this.view.offsetY
        };
    }

    render() {
        const ctx = this.ctx;
        const { width, height, dpr } = this.viewport;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = '#dce3e9';
        ctx.fillRect(0, 0, width, height);

        ctx.save();
        ctx.translate(this.view.offsetX, this.view.offsetY);
        ctx.scale(this.view.scale, this.view.scale);
        this._drawMap(ctx);
        ctx.restore();

        this._drawScreenOverlay(ctx);
    }

    _drawMap(ctx) {
        const project = this.store.project;
        const map = project.map;
        ctx.save();
        ctx.beginPath();
        ctx.rect(0, 0, map.width, map.height);
        ctx.clip();

        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, map.width, map.height);
        if (this.layers.base && this.image?.complete && this.image.naturalWidth) {
            ctx.globalAlpha = 0.92;
            ctx.drawImage(this.image, 0, 0, map.width, map.height);
            ctx.globalAlpha = 1;
        }
        if (this.layers.grid) this._drawGrid(ctx, map);
        if (this.layers.walkable) this._drawStrokes(ctx, project.annotations.strokes.walkable, '#22c55e', 0.24);
        if (this.layers.walls) {
            this._drawWallPixels(ctx, project.annotations.wallPixels);
            this._drawStrokes(ctx, project.annotations.strokes.walls, '#111827', 0.78);
        }
        if (this.layers.centerline) this._drawCenterline(ctx, project.derived.centerline);
        if (this.layers.coverage && this.layers.blackBoxes) this._drawCoverage(ctx, project);
        if (this.layers.semantic) {
            ['doors', 'exits', 'refuges', 'stairs', 'gateways'].forEach((collection) => {
                this._drawEntities(ctx, collection, project.entities[collection]);
            });
        }
        if (this.layers.candidates) this._drawCandidates(ctx, project.derived.candidates);
        if (this.layers.blackBoxes) this._drawEntities(ctx, 'blackBoxes', project.entities.blackBoxes);
        if (this.layers.validation) this._drawValidation(ctx, project);
        ctx.restore();

        ctx.save();
        ctx.strokeStyle = '#64748b';
        ctx.lineWidth = 1 / this.view.scale;
        ctx.strokeRect(0, 0, map.width, map.height);
        ctx.restore();
    }

    _drawGrid(ctx, map) {
        const configured = Number(this.store.project.settings.gridSize) || 10;
        const targetScreen = 28;
        let step = configured;
        while (step * this.view.scale < targetScreen) step *= 2;
        if (step * this.view.scale > 240) return;
        ctx.save();
        ctx.strokeStyle = 'rgba(100, 116, 139, 0.16)';
        ctx.lineWidth = 1 / this.view.scale;
        ctx.beginPath();
        for (let x = 0; x <= map.width; x += step) {
            ctx.moveTo(x, 0);
            ctx.lineTo(x, map.height);
        }
        for (let y = 0; y <= map.height; y += step) {
            ctx.moveTo(0, y);
            ctx.lineTo(map.width, y);
        }
        ctx.stroke();
        ctx.restore();
    }

    _drawStrokes(ctx, strokes, color, alpha) {
        ctx.save();
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.globalAlpha = alpha;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        strokes.forEach((stroke) => {
            if (!stroke.points?.length) return;
            ctx.lineWidth = Math.max(1, Number(stroke.size) || 1);
            ctx.beginPath();
            ctx.moveTo(stroke.points[0].x, stroke.points[0].y);
            stroke.points.slice(1).forEach((point) => ctx.lineTo(point.x, point.y));
            if (stroke.closed && stroke.points.length > 2) {
                ctx.closePath();
                ctx.fill();
            }
            ctx.stroke();
        });
        ctx.restore();
    }

    _drawWallPixels(ctx, pixels) {
        if (!pixels?.length) return;
        ctx.save();
        ctx.fillStyle = 'rgba(17, 24, 39, 0.82)';
        pixels.forEach((point) => ctx.fillRect(Number(point[0]), Number(point[1]), 1.05, 1.05));
        ctx.restore();
    }

    _drawCenterline(ctx, centerline) {
        const paths = extractCenterlinePaths(centerline);
        if (!paths.length) return;
        ctx.save();
        ctx.strokeStyle = '#0284c7';
        ctx.fillStyle = 'rgba(2, 132, 199, .82)';
        ctx.lineWidth = 1.5 / this.view.scale;
        ctx.setLineDash([7 / this.view.scale, 4 / this.view.scale]);
        if (centerline?.points && Array.isArray(centerline.points)) {
            const dot = Math.max(0.55, 1.5 / this.view.scale);
            centerline.points.forEach((value) => {
                const point = pointOf(value);
                ctx.fillRect(point.x - dot / 2, point.y - dot / 2, dot, dot);
            });
            ctx.restore();
            return;
        }
        paths.forEach((path) => {
            if (path.length < 2) return;
            ctx.beginPath();
            ctx.moveTo(path[0].x, path[0].y);
            path.slice(1).forEach((point) => ctx.lineTo(point.x, point.y));
            ctx.stroke();
        });
        ctx.restore();
    }

    _drawCoverage(ctx, project) {
        const metersPerPixel = Math.max(0.000001, Number(project.map.metersPerPixel) || 0.05);
        const radius = Math.max(0, Number(project.settings.coverageRadius) || 0) / metersPerPixel;
        if (!radius) return;
        ctx.save();
        ctx.fillStyle = 'rgba(59, 130, 246, 0.08)';
        ctx.strokeStyle = 'rgba(37, 99, 235, 0.45)';
        ctx.lineWidth = 1 / this.view.scale;
        project.entities.blackBoxes.forEach((box) => {
            ctx.beginPath();
            ctx.arc(box.x, box.y, radius, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
        });
        ctx.restore();
    }

    _drawEntities(ctx, collection, entities) {
        const style = ENTITY_STYLES[collection];
        if (!style) return;
        const radius = 8 / this.view.scale;
        const fontSize = 10 / this.view.scale;
        entities.forEach((entity) => {
            const selected = this.selected?.collection === collection && this.selected?.id === entity.id;
            ctx.save();
            ctx.translate(entity.x, entity.y);
            ctx.lineWidth = (selected ? 3 : 1.5) / this.view.scale;
            ctx.strokeStyle = selected ? '#06b6d4' : style.color;
            ctx.fillStyle = style.fill;
            ctx.beginPath();
            if (collection === 'blackBoxes') {
                ctx.rect(-radius, -radius, radius * 2, radius * 2);
            } else if (collection === 'gateways') {
                ctx.moveTo(0, -radius);
                ctx.lineTo(radius, 0);
                ctx.lineTo(0, radius);
                ctx.lineTo(-radius, 0);
                ctx.closePath();
            } else {
                ctx.arc(0, 0, radius, 0, Math.PI * 2);
            }
            ctx.fill();
            ctx.stroke();
            ctx.fillStyle = style.color;
            ctx.font = `700 ${fontSize}px system-ui, sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(style.glyph, 0, 0.5 / this.view.scale);
            ctx.font = `600 ${9 / this.view.scale}px system-ui, sans-serif`;
            ctx.textAlign = 'left';
            ctx.textBaseline = 'bottom';
            ctx.fillText(entity.label || entity.id, radius + 3 / this.view.scale, -radius);
            if (entity.locked) {
                ctx.fillStyle = '#dc2626';
                ctx.font = `700 ${8 / this.view.scale}px system-ui, sans-serif`;
                ctx.fillText('锁', radius + 3 / this.view.scale, radius + 2 / this.view.scale);
            }
            ctx.restore();
        });
    }

    _drawCandidates(ctx, candidates) {
        const radius = 6 / this.view.scale;
        candidates.forEach((candidate) => {
            ctx.save();
            ctx.translate(candidate.x, candidate.y);
            ctx.rotate(Math.PI / 4);
            ctx.fillStyle = candidate.selected ? '#f97316' : 'rgba(251, 146, 60, 0.28)';
            ctx.strokeStyle = candidate.mandatory ? '#dc2626' : '#ea580c';
            ctx.lineWidth = (candidate.mandatory ? 2.5 : 1.5) / this.view.scale;
            ctx.fillRect(-radius, -radius, radius * 2, radius * 2);
            ctx.strokeRect(-radius, -radius, radius * 2, radius * 2);
            ctx.restore();
        });
    }

    _drawValidation(ctx, project) {
        const issues = project.derived.validation?.issues ?? [];
        const size = 8 / this.view.scale;
        ctx.save();
        ctx.lineWidth = 2.2 / this.view.scale;
        issues.forEach((issue) => {
            const point = geometryPoint(issue, project);
            if (!point) return;
            const severity = String(issue.severity ?? 'error').toLowerCase();
            ctx.strokeStyle = severity === 'warning' ? '#f59e0b' : severity === 'info' ? '#0284c7' : '#dc2626';
            ctx.beginPath();
            ctx.arc(point.x, point.y, size, 0, Math.PI * 2);
            ctx.moveTo(point.x - size * 0.7, point.y - size * 0.7);
            ctx.lineTo(point.x + size * 0.7, point.y + size * 0.7);
            ctx.moveTo(point.x + size * 0.7, point.y - size * 0.7);
            ctx.lineTo(point.x - size * 0.7, point.y + size * 0.7);
            ctx.stroke();
        });
        ctx.restore();
    }

    _drawScreenOverlay(ctx) {
        const map = this.store.project.map;
        if (this.measurement?.start && this.measurement?.end) {
            const start = this.mapToScreen(this.measurement.start);
            const end = this.mapToScreen(this.measurement.end);
            const meters = distance(this.measurement.start, this.measurement.end) * map.metersPerPixel;
            ctx.save();
            ctx.strokeStyle = '#0f172a';
            ctx.fillStyle = '#0f172a';
            ctx.lineWidth = 2;
            ctx.setLineDash([6, 4]);
            ctx.beginPath();
            ctx.moveTo(start.x, start.y);
            ctx.lineTo(end.x, end.y);
            ctx.stroke();
            const mid = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
            const text = `${meters.toFixed(2)} m`;
            ctx.font = '600 12px system-ui, sans-serif';
            const width = ctx.measureText(text).width + 12;
            ctx.setLineDash([]);
            ctx.fillStyle = 'rgba(255,255,255,.94)';
            ctx.fillRect(mid.x - width / 2, mid.y - 12, width, 22);
            ctx.fillStyle = '#0f172a';
            ctx.textAlign = 'center';
            ctx.fillText(text, mid.x, mid.y + 4);
            ctx.restore();
        }

        if (this.pointer.inside && ['walkable', 'wall'].includes(this.tool) && !this.interaction) {
            const point = this.mapToScreen(this.pointer.map);
            const brushSize = Number(this.store.project.settings.brushSize) || 12;
            ctx.save();
            ctx.strokeStyle = this.tool === 'walkable' ? '#16a34a' : '#111827';
            ctx.fillStyle = this.tool === 'walkable' ? 'rgba(34,197,94,.12)' : 'rgba(17,24,39,.12)';
            ctx.beginPath();
            ctx.arc(point.x, point.y, brushSize * this.view.scale / 2, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
            ctx.restore();
        }
    }

    _bindEvents() {
        this.canvas.addEventListener('contextmenu', (event) => event.preventDefault());
        this.canvas.addEventListener('pointerdown', (event) => this._pointerDown(event));
        this.canvas.addEventListener('pointermove', (event) => this._pointerMove(event));
        this.canvas.addEventListener('pointerup', (event) => this._pointerUp(event));
        this.canvas.addEventListener('pointercancel', (event) => this._pointerUp(event, true));
        this.canvas.addEventListener('pointerleave', () => {
            this.pointer.inside = false;
            if (!this.interaction) this.render();
        });
        this.canvas.addEventListener('wheel', (event) => this._wheel(event), { passive: false });

        window.addEventListener('keydown', (event) => {
            if (event.code === 'Space' && !this._isTyping(event.target)) {
                this.spacePressed = true;
                this.canvas.classList.add('space-pan');
                event.preventDefault();
            }
            if (this._isTyping(event.target)) return;
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
                event.preventDefault();
                if (event.shiftKey) this.store.redo(); else this.store.undo();
            } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
                event.preventDefault();
                this.store.redo();
            } else if (event.key === 'Delete' || event.key === 'Backspace') {
                event.preventDefault();
                this.deleteSelected();
            } else if (event.key === 'Escape') {
                if (this.interaction) this._cancelInteraction();
                this.setSelected(null);
            }
        });
        window.addEventListener('keyup', (event) => {
            if (event.code === 'Space') {
                this.spacePressed = false;
                this.canvas.classList.remove('space-pan');
            }
        });
    }

    _pointerDown(event) {
        this.canvas.focus({ preventScroll: true });
        this.canvas.setPointerCapture(event.pointerId);
        this._updatePointer(event);
        const screen = { ...this.pointer.screen };
        const rawMapPoint = { ...this.pointer.map };
        const mapPoint = this._constrainPoint(rawMapPoint);
        const shouldPan = this.tool === 'pan' || this.spacePressed || event.button === 1 || event.button === 2;
        if (shouldPan) {
            this.interaction = {
                kind: 'pan',
                pointerId: event.pointerId,
                startScreen: screen,
                startOffset: { x: this.view.offsetX, y: this.view.offsetY }
            };
            this.canvas.classList.add('dragging');
            return;
        }

        if (!this._insideMap(rawMapPoint)) return;
        if (this.tool === 'select') {
            const hit = this._hitTestEntity(mapPoint);
            if (hit) {
                this.setSelected({ collection: hit.collection, id: hit.entity.id });
                if (!hit.entity.locked) {
                    this.store.beginTransaction();
                    this.interaction = {
                        kind: 'drag-entity',
                        pointerId: event.pointerId,
                        reference: { collection: hit.collection, id: hit.entity.id },
                        offset: { x: mapPoint.x - hit.entity.x, y: mapPoint.y - hit.entity.y },
                        moved: false
                    };
                    this.canvas.classList.add('dragging');
                }
                return;
            }
            const candidate = this._hitTestCandidate(mapPoint);
            if (candidate) {
                this.store.commit((project) => {
                    const item = project.derived.candidates.find((entry) => entry.id === candidate.id);
                    if (item) item.selected = !item.selected;
                }, 'toggle candidate');
                return;
            }
            this.setSelected(null);
            return;
        }

        if (this.tool === 'walkable' || this.tool === 'wall') {
            const kind = this.tool === 'wall' ? 'walls' : 'walkable';
            const stroke = {
                id: createId(kind),
                points: [mapPoint],
                size: Math.max(1, Number(this.store.project.settings.brushSize) || 12),
                closed: false,
                locked: false
            };
            this.store.beginTransaction();
            this.store.updateTransient((project) => project.annotations.strokes[kind].push(stroke), 'start stroke');
            this.interaction = { kind: 'stroke', pointerId: event.pointerId, strokeId: stroke.id, collection: kind };
            return;
        }

        if (this.tool === 'measure') {
            this.measurement = { start: mapPoint, end: mapPoint };
            this.interaction = { kind: 'measure', pointerId: event.pointerId };
            this.render();
            return;
        }

        const collection = ENTITY_TOOLS[this.tool];
        if (collection) {
            const snapped = collection === 'blackBoxes' ? this._snapPoint(mapPoint) : mapPoint;
            let entity;
            this.store.commit((project) => {
                entity = createEntity(project, collection, snapped);
                project.entities[collection].push(entity);
            }, `add ${collection}`);
            this.setSelected({ collection, id: entity.id });
        }
    }

    _pointerMove(event) {
        this._updatePointer(event);
        if (!this.interaction || this.interaction.pointerId !== event.pointerId) {
            this.render();
            return;
        }
        const interaction = this.interaction;
        if (interaction.kind === 'pan') {
            this.view.offsetX = interaction.startOffset.x + this.pointer.screen.x - interaction.startScreen.x;
            this.view.offsetY = interaction.startOffset.y + this.pointer.screen.y - interaction.startScreen.y;
            this.render();
            this._emitViewport();
            return;
        }

        const mapPoint = this._constrainPoint(this.pointer.map);
        if (interaction.kind === 'stroke') {
            this.store.updateTransient((project) => {
                const stroke = project.annotations.strokes[interaction.collection]
                    .find((item) => item.id === interaction.strokeId);
                if (!stroke) return;
                const last = stroke.points.at(-1);
                const threshold = Math.max(0.5, (Number(stroke.size) || 1) / 6);
                if (!last || distance(last, mapPoint) >= threshold) stroke.points.push(mapPoint);
            }, 'draw stroke');
        } else if (interaction.kind === 'drag-entity') {
            this.store.updateTransient((project) => {
                const entity = project.entities[interaction.reference.collection]
                    ?.find((item) => item.id === interaction.reference.id);
                if (!entity) return;
                let next = this._constrainPoint({
                    x: mapPoint.x - interaction.offset.x,
                    y: mapPoint.y - interaction.offset.y
                });
                if (interaction.reference.collection === 'blackBoxes') next = this._snapPoint(next);
                if (Math.abs(entity.x - next.x) > 0.01 || Math.abs(entity.y - next.y) > 0.01) {
                    interaction.moved = true;
                    entity.x = next.x;
                    entity.y = next.y;
                }
            }, 'drag entity');
        } else if (interaction.kind === 'measure') {
            this.measurement.end = mapPoint;
            this.callbacks.onMeasurement?.(
                distance(this.measurement.start, this.measurement.end) * this.store.project.map.metersPerPixel
            );
            this.render();
        }
    }

    _pointerUp(event, cancelled = false) {
        if (!this.interaction || this.interaction.pointerId !== event.pointerId) return;
        const interaction = this.interaction;
        this.interaction = null;
        this.canvas.classList.remove('dragging');
        if (interaction.kind === 'stroke') {
            this.store.endTransaction({ commit: !cancelled, reason: 'draw stroke' });
        } else if (interaction.kind === 'drag-entity') {
            this.store.endTransaction({ commit: !cancelled && interaction.moved, reason: 'drag entity' });
            this.callbacks.onEntityDragEnd?.(this.getSelectedEntity(), interaction.reference);
        }
        this.render();
    }

    _cancelInteraction() {
        const kind = this.interaction?.kind;
        this.interaction = null;
        this.canvas.classList.remove('dragging');
        if (kind === 'stroke' || kind === 'drag-entity') {
            this.store.endTransaction({ commit: false, reason: 'cancel interaction' });
        }
        this.render();
    }

    _wheel(event) {
        event.preventDefault();
        const screen = this._screenPoint(event);
        const before = this.screenToMap(screen);
        const factor = Math.exp(-event.deltaY * 0.0015);
        this.view.scale = clamp(this.view.scale * factor, 0.02, 40);
        this.view.offsetX = screen.x - before.x * this.view.scale;
        this.view.offsetY = screen.y - before.y * this.view.scale;
        this._updatePointer(event);
        this.render();
        this._emitViewport();
    }

    _updatePointer(event) {
        const screen = this._screenPoint(event);
        const mapPoint = this.screenToMap(screen);
        this.pointer = { screen, map: mapPoint, inside: this._insideMap(mapPoint) };
        this.callbacks.onPointerChange?.(mapPoint, this.pointer.inside);
    }

    _screenPoint(event) {
        const rect = this.canvas.getBoundingClientRect();
        return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }

    _insideMap(point) {
        const { width, height } = this.store.project.map;
        return point.x >= 0 && point.y >= 0 && point.x <= width && point.y <= height;
    }

    _constrainPoint(point) {
        const { width, height } = this.store.project.map;
        return { x: clamp(point.x, 0, width), y: clamp(point.y, 0, height) };
    }

    _snapPoint(point) {
        const settings = this.store.project.settings;
        let snapped = { ...point };
        if (settings.snapToGrid) {
            const grid = Math.max(1, Number(settings.gridSize) || 10);
            snapped = { x: Math.round(snapped.x / grid) * grid, y: Math.round(snapped.y / grid) * grid };
        }
        if (!settings.snapToCenterline) return this._constrainPoint(snapped);
        const paths = extractCenterlinePaths(this.store.project.derived.centerline);
        const sources = paths.length
            ? paths
            : this.store.project.annotations.strokes.walkable.map((stroke) => stroke.points);
        let nearest = null;
        let nearestDistance = Infinity;
        sources.forEach((path) => path.forEach((candidate) => {
            const currentDistance = distance(snapped, candidate);
            if (currentDistance < nearestDistance) {
                nearestDistance = currentDistance;
                nearest = candidate;
            }
        }));
        const threshold = Math.max(1, Number(settings.snapDistance) || 16);
        if (nearest && nearestDistance <= threshold) snapped = { x: nearest.x, y: nearest.y };
        return this._constrainPoint(snapped);
    }

    _hitTestEntity(point) {
        const threshold = 14 / this.view.scale;
        const collections = ['blackBoxes', 'gateways', 'doors', 'stairs', 'refuges', 'exits'];
        for (const collection of collections) {
            if (collection === 'blackBoxes' && !this.layers.blackBoxes) continue;
            if (collection !== 'blackBoxes' && !this.layers.semantic) continue;
            const entities = this.store.project.entities[collection] ?? [];
            for (let index = entities.length - 1; index >= 0; index -= 1) {
                if (distance(point, entities[index]) <= threshold) return { collection, entity: entities[index] };
            }
        }
        return null;
    }

    _hitTestCandidate(point) {
        if (!this.layers.candidates) return null;
        const threshold = 12 / this.view.scale;
        return this.store.project.derived.candidates
            .findLast?.((candidate) => distance(point, candidate) <= threshold)
            ?? [...this.store.project.derived.candidates].reverse().find((candidate) => distance(point, candidate) <= threshold)
            ?? null;
    }

    _repairSelection(project) {
        if (!this.selected) return;
        const exists = project.entities[this.selected.collection]
            ?.some((entity) => entity.id === this.selected.id);
        if (!exists) {
            this.selected = null;
            this.callbacks.onSelectionChange?.(null, null);
        } else {
            this.callbacks.onSelectionChange?.(this.getSelectedEntity(), this.selected);
        }
    }

    _ensureImage(map) {
        const source = map.imageDataUrl || map.imageUrl || '';
        if (source === this.imageSource) return;
        this.imageSource = source;
        this.image = null;
        if (!source) {
            this.render();
            return;
        }
        this.imageLoading = true;
        const image = new Image();
        if (!source.startsWith('data:')) image.crossOrigin = 'anonymous';
        image.onload = () => {
            if (this.imageSource !== source) return;
            this.image = image;
            this.imageLoading = false;
            this.render();
        };
        image.onerror = () => {
            this.imageLoading = false;
            this.callbacks.onNotice?.('底图图片加载失败，标注数据仍可编辑', 'warning');
            this.render();
        };
        image.src = source;
    }

    _emitViewport() {
        this.callbacks.onViewportChange?.(this.view);
    }

    _isTyping(target) {
        return target instanceof HTMLInputElement
            || target instanceof HTMLTextAreaElement
            || target instanceof HTMLSelectElement
            || target?.isContentEditable;
    }
}

export { extractCenterlinePaths, geometryPoint, DEFAULT_LAYERS };
