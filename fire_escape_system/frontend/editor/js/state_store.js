const ENTITY_PREFIX = {
    exits: 'E',
    refuges: 'R',
    stairs: 'S',
    doors: 'D',
    gateways: 'G',
    blackBoxes: 'B'
};

const cloneValue = (value) => {
    if (typeof structuredClone === 'function') return structuredClone(value);
    return JSON.parse(JSON.stringify(value));
};

const finiteNumber = (value, fallback = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
};

const arrayValue = (value) => Array.isArray(value) ? value : [];

const createId = (prefix = 'item') => {
    if (globalThis.crypto?.randomUUID) return `${prefix}-${crypto.randomUUID()}`;
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
};

export function createDefaultProject(overrides = {}) {
    const project = {
        schemaVersion: '1.0.0',
        revision: 0,
        map: {
            id: 'default',
            name: '未命名地图',
            version: '1.0.0',
            imageDataUrl: '',
            imageUrl: '',
            width: 250,
            height: 250,
            metersPerPixel: 0.05,
            sourceMaskPath: '',
            coordinateOrigin: 'top_left',
            xAxis: 'east',
            yAxis: 'south'
        },
        annotations: {
            strokes: {
                walkable: [],
                walls: []
            },
            wallPixels: [],
            fireDomains: []
        },
        entities: {
            doors: [],
            exits: [],
            refuges: [],
            stairs: [],
            gateways: [],
            blackBoxes: []
        },
        derived: {
            centerline: [],
            candidates: [],
            topology: {},
            validation: { valid: null, issues: [], summary: {} },
            masks: {}
        },
        settings: {
            brushSize: 12,
            coverageRadius: 5,
            visibleRadius: 8,
            maxBoxDistance: 6,
            snapDistance: 16,
            snapToCenterline: true,
            snapToGrid: false,
            gridSize: 10
        },
        simulation: {
            initialFires: [],
            ignitionTick: 10,
            tickIntervalSeconds: 1
        }
    };

    return deepMerge(project, overrides);
}

function deepMerge(base, overrides) {
    if (!overrides || typeof overrides !== 'object') return cloneValue(base);
    const result = cloneValue(base);
    Object.entries(overrides).forEach(([key, value]) => {
        if (value && typeof value === 'object' && !Array.isArray(value)
            && result[key] && typeof result[key] === 'object' && !Array.isArray(result[key])) {
            result[key] = deepMerge(result[key], value);
        } else {
            result[key] = cloneValue(value);
        }
    });
    return result;
}

function normalizePoint(point) {
    if (Array.isArray(point)) {
        return { x: finiteNumber(point[0]), y: finiteNumber(point[1]) };
    }
    return {
        x: finiteNumber(point?.x ?? point?.col ?? point?.column),
        y: finiteNumber(point?.y ?? point?.row)
    };
}

function normalizeStroke(stroke, index, kind) {
    const rawPoints = stroke?.points ?? stroke?.vertices ?? stroke?.path ?? stroke;
    return {
        id: stroke?.id || createId(`${kind}-${index + 1}`),
        points: arrayValue(rawPoints).map(normalizePoint),
        size: Math.max(1, finiteNumber(stroke?.size ?? stroke?.width, kind === 'walls' ? 8 : 16)),
        closed: Boolean(stroke?.closed ?? stroke?.polygon),
        locked: Boolean(stroke?.locked)
    };
}

function normalizeEntity(entity, index, collection) {
    const point = normalizePoint(entity?.position ?? entity?.coordinate ?? entity);
    const prefix = ENTITY_PREFIX[collection] || collection.slice(0, 1).toUpperCase();
    const id = String(entity?.id ?? entity?.box_id ?? entity?.code ?? `${prefix}${String(index + 1).padStart(3, '0')}`);
    const result = {
        ...entity,
        id,
        x: point.x,
        y: point.y,
        label: String(entity?.label ?? entity?.name ?? id),
        locked: Boolean(entity?.locked)
    };

    if (collection === 'doors') {
        result.doorType = entity?.doorType ?? entity?.door_type ?? entity?.type ?? 'normal';
        result.state = entity?.state ?? (entity?.open === true ? 'open' : 'closed');
    }
    if (collection === 'blackBoxes') {
        result.mandatory = Boolean(entity?.mandatory ?? entity?.required);
        result.source = entity?.source ?? 'manual';
    }
    return result;
}

function collectionFrom(raw, ...keys) {
    for (const key of keys) {
        const value = raw?.[key];
        if (Array.isArray(value)) return value;
    }
    return [];
}

function normalizeCandidates(rawCandidates) {
    return arrayValue(rawCandidates).map((candidate, index) => {
        const point = normalizePoint(candidate?.position ?? candidate);
        return {
            ...candidate,
            id: String(candidate?.id ?? candidate?.candidate_id ?? `C${String(index + 1).padStart(3, '0')}`),
            x: point.x,
            y: point.y,
            reason: String(candidate?.reason ?? candidate?.source ?? candidate?.kind ?? '自动候选'),
            mandatory: Boolean(candidate?.mandatory ?? candidate?.required),
            selected: Boolean(candidate?.selected ?? candidate?.mandatory)
        };
    });
}

export function normalizeProject(input) {
    const envelope = input?.data ?? input?.result ?? input;
    const raw = envelope?.project ?? envelope?.mapPackage ?? envelope?.map_package ?? envelope ?? {};
    const mapRaw = raw.map ?? raw.map_meta ?? raw.metadata ?? {};
    const annotationsRaw = raw.annotations ?? raw.polygons ?? {};
    const strokesRaw = annotationsRaw.strokes ?? {};
    const entitiesRaw = raw.entities ?? raw;
    const derivedRaw = raw.derived ?? raw.compiled ?? {};

    const width = Math.max(1, Math.round(finiteNumber(
        mapRaw.width ?? raw.width ?? raw.grid_width ?? raw.gridWidth,
        250
    )));
    const height = Math.max(1, Math.round(finiteNumber(
        mapRaw.height ?? raw.height ?? raw.grid_height ?? raw.gridHeight,
        250
    )));

    const project = createDefaultProject({
        schemaVersion: raw.schemaVersion ?? raw.schema_version ?? '1.0.0',
        revision: Math.max(0, Math.round(finiteNumber(raw.revision, 0))),
        map: {
            id: String(mapRaw.id ?? raw.map_id ?? raw.id ?? 'default'),
            name: String(mapRaw.name ?? raw.name ?? '未命名地图'),
            version: String(mapRaw.version ?? raw.version ?? '1.0.0'),
            imageDataUrl: String(mapRaw.imageDataUrl ?? mapRaw.image_data_url ?? raw.imageDataUrl ?? raw.image_data_url ?? ''),
            imageUrl: String(mapRaw.imageUrl ?? mapRaw.image_url ?? raw.imageUrl ?? raw.image_url ?? ''),
            width,
            height,
            metersPerPixel: Math.max(0.000001, finiteNumber(
                mapRaw.metersPerPixel ?? mapRaw.meters_per_pixel ?? raw.metersPerPixel ?? raw.meters_per_pixel,
                0.05
            )),
            sourceMaskPath: String(mapRaw.sourceMaskPath ?? mapRaw.source_mask_path ?? ''),
            coordinateOrigin: String(mapRaw.coordinateOrigin ?? mapRaw.coordinate_origin ?? 'top_left'),
            xAxis: String(mapRaw.xAxis ?? mapRaw.x_axis ?? 'east'),
            yAxis: String(mapRaw.yAxis ?? mapRaw.y_axis ?? 'south')
        }
    });

    const polygonWalkable = annotationsRaw.walkable ?? raw.polygons?.walkable ?? [];
    const polygonWalls = annotationsRaw.walls ?? raw.polygons?.walls ?? [];
    const walkable = collectionFrom(strokesRaw, 'walkable').length
        ? strokesRaw.walkable
        : polygonWalkable;
    const walls = collectionFrom(strokesRaw, 'walls').length
        ? strokesRaw.walls
        : polygonWalls;

    project.annotations.strokes.walkable = arrayValue(walkable)
        .map((stroke, index) => normalizeStroke(stroke, index, 'walkable'));
    project.annotations.strokes.walls = arrayValue(walls)
        .map((stroke, index) => normalizeStroke(stroke, index, 'walls'));
    project.annotations.wallPixels = arrayValue(
        annotationsRaw.wallPixels ?? annotationsRaw.wall_pixels ?? raw.wall_data ?? raw.wallData
    ).map((point) => {
        const normalized = normalizePoint(point);
        return [normalized.x, normalized.y];
    });
    project.annotations.fireDomains = arrayValue(
        annotationsRaw.fireDomains ?? annotationsRaw.fire_domains ?? raw.fireDomains ?? raw.fire_domains
    );

    const mappings = {
        doors: ['doors'],
        exits: ['exits', 'exits_data'],
        refuges: ['refuges', 'refuge_points'],
        stairs: ['stairs'],
        gateways: ['gateways'],
        blackBoxes: ['blackBoxes', 'black_boxes', 'boxes']
    };

    Object.entries(mappings).forEach(([collection, keys]) => {
        const list = keys.reduce((found, key) => found.length ? found : collectionFrom(entitiesRaw, key), []);
        project.entities[collection] = list.map((entity, index) => normalizeEntity(entity, index, collection));
    });

    project.derived.centerline = cloneValue(
        derivedRaw.centerline ?? derivedRaw.centerlines ?? derivedRaw.skeleton ?? raw.centerline ?? raw.skeleton ?? []
    );
    project.derived.candidates = normalizeCandidates(
        derivedRaw.candidates ?? raw.candidates ?? raw.candidateBoxes ?? raw.candidate_boxes ?? []
    );
    project.derived.topology = cloneValue(derivedRaw.topology ?? raw.topology ?? {});
    project.derived.masks = cloneValue(derivedRaw.masks ?? raw.masks ?? {});

    const validation = derivedRaw.validation ?? raw.validation ?? {};
    project.derived.validation = {
        valid: validation.valid ?? null,
        issues: arrayValue(validation.issues ?? raw.issues),
        summary: validation.summary ?? {}
    };

    project.settings = deepMerge(project.settings, raw.settings ?? {});
    project.simulation = deepMerge(project.simulation, raw.simulation ?? {});
    return project;
}

export function nextEntityId(project, collection) {
    const prefix = ENTITY_PREFIX[collection] || collection.slice(0, 1).toUpperCase();
    const entities = project.entities?.[collection] ?? [];
    const used = new Set(entities.flatMap((entity) => [String(entity.id), String(entity.label ?? '')]));
    const aliases = {
        exits: '(?:E|EXIT[-_]?)',
        refuges: '(?:R|REFUGE[-_]?)',
        stairs: '(?:S|STAIR[-_]?)',
        doors: '(?:D|DOOR[-_]?)',
        gateways: '(?:G|GATEWAY[-_]?)',
        blackBoxes: '(?:B|BOX[-_]?)'
    };
    const pattern = new RegExp(`^${aliases[collection] || prefix}(\\d+)$`, 'i');
    let sequence = entities.reduce((maximum, entity) => {
        const values = [entity.id, entity.label];
        return values.reduce((current, value) => {
            const match = pattern.exec(String(value ?? ''));
            return match ? Math.max(current, Number(match[1])) : current;
        }, maximum);
    }, 0) + 1;
    let candidate;
    do {
        candidate = `${prefix}${String(sequence).padStart(3, '0')}`;
        sequence += 1;
    } while (used.has(candidate));
    return candidate;
}

export function createEntity(project, collection, point, extra = {}) {
    const id = nextEntityId(project, collection);
    const entity = {
        id,
        x: finiteNumber(point.x),
        y: finiteNumber(point.y),
        label: id,
        locked: false,
        ...extra
    };
    if (collection === 'doors') Object.assign(entity, { doorType: 'normal', state: 'closed' }, extra);
    if (collection === 'blackBoxes') Object.assign(entity, { mandatory: false, source: 'manual' }, extra);
    return entity;
}

export class StateStore {
    constructor(initialProject = createDefaultProject(), historyLimit = 60) {
        this.project = normalizeProject(initialProject);
        this.historyLimit = historyLimit;
        this.undoStack = [];
        this.redoStack = [];
        this.listeners = new Set();
        this.transactionSnapshot = null;
    }

    subscribe(listener) {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    }

    notify(meta = {}) {
        this.listeners.forEach((listener) => listener(this.project, meta));
    }

    getSnapshot() {
        return cloneValue(this.project);
    }

    replaceProject(project, { recordHistory = false, reason = 'replace' } = {}) {
        if (recordHistory) this._pushUndo(this.getSnapshot());
        this.project = normalizeProject(project);
        if (!recordHistory) {
            this.undoStack = [];
            this.redoStack = [];
        }
        this.notify({ reason });
    }

    commit(mutator, reason = 'edit') {
        const before = this.getSnapshot();
        mutator(this.project);
        this.project.revision = Math.max(0, Number(this.project.revision) || 0) + 1;
        this._pushUndo(before);
        this.redoStack = [];
        this.notify({ reason });
    }

    beginTransaction() {
        if (!this.transactionSnapshot) this.transactionSnapshot = this.getSnapshot();
    }

    updateTransient(mutator, reason = 'transient') {
        mutator(this.project);
        this.notify({ reason, transient: true });
    }

    endTransaction({ commit = true, reason = 'transaction' } = {}) {
        if (!this.transactionSnapshot) return;
        const before = this.transactionSnapshot;
        this.transactionSnapshot = null;
        if (commit) {
            this.project.revision = Math.max(0, Number(this.project.revision) || 0) + 1;
            this._pushUndo(before);
            this.redoStack = [];
        } else {
            this.project = before;
        }
        this.notify({ reason, cancelled: !commit });
    }

    undo() {
        if (!this.undoStack.length) return false;
        this.redoStack.push(this.getSnapshot());
        this.project = this.undoStack.pop();
        this.notify({ reason: 'undo' });
        return true;
    }

    redo() {
        if (!this.redoStack.length) return false;
        this._pushUndo(this.getSnapshot());
        this.project = this.redoStack.pop();
        this.notify({ reason: 'redo' });
        return true;
    }

    canUndo() { return this.undoStack.length > 0; }
    canRedo() { return this.redoStack.length > 0; }

    _pushUndo(snapshot) {
        this.undoStack.push(snapshot);
        if (this.undoStack.length > this.historyLimit) this.undoStack.shift();
    }
}

export { cloneValue, createId, normalizeCandidates };
