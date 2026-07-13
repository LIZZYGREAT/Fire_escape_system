import {
    StateStore,
    createDefaultProject,
    createEntity,
    normalizeCandidates,
    normalizeProject
} from './state_store.js';
import { editorApi, unwrap } from './api.js';
import { CanvasMapEditor, extractCenterlinePaths } from './canvas_editor.js';
import { createStoredZip } from './zip_utils.js';

const byId = (id) => document.getElementById(id);
const projectStore = new StateStore(createDefaultProject());
let lastSavedRevision = projectStore.project.revision;
let activeOperations = 0;

const dom = {
    mapId: byId('map-id'),
    mapName: byId('map-name'),
    mapVersion: byId('map-version'),
    mapScale: byId('map-scale'),
    saveState: byId('save-state'),
    imageInput: byId('image-input'),
    jsonInput: byId('json-input'),
    emptyHint: byId('empty-hint'),
    busyOverlay: byId('busy-overlay'),
    busyText: byId('busy-text'),
    brushSize: byId('brush-size'),
    brushSizeValue: byId('brush-size-value'),
    coverageRadius: byId('coverage-radius'),
    snapCenterline: byId('snap-centerline'),
    snapGrid: byId('snap-grid'),
    propertyPanel: byId('property-panel'),
    selectionKind: byId('selection-kind'),
    candidateList: byId('candidate-list'),
    candidateCount: byId('candidate-count'),
    issueList: byId('issue-list'),
    issueCount: byId('issue-count'),
    validationSummary: byId('validation-summary'),
    coordinateStatus: byId('coordinate-status'),
    realCoordinateStatus: byId('real-coordinate-status'),
    measurementStatus: byId('measurement-status'),
    mapSizeStatus: byId('map-size-status'),
    zoomStatus: byId('zoom-status'),
    undo: byId('btn-undo'),
    redo: byId('btn-redo'),
    toastRegion: byId('toast-region')
};

const editor = new CanvasMapEditor(byId('map-canvas'), projectStore, {
    onToolChange: activateToolButton,
    onSelectionChange: renderProperties,
    onPointerChange: updatePointerStatus,
    onViewportChange: (view) => { dom.zoomStatus.textContent = `${Math.round(view.scale * 100)}%`; },
    onMeasurement: (meters) => {
        dom.measurementStatus.textContent = meters == null ? '测距: —' : `测距: ${meters.toFixed(2)} m`;
    },
    onNotice: notify
});

function notify(message, type = 'info', timeout = 3200) {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    dom.toastRegion.appendChild(toast);
    setTimeout(() => toast.remove(), timeout);
}

function setBusy(active, text = '处理中…') {
    activeOperations += active ? 1 : -1;
    activeOperations = Math.max(0, activeOperations);
    dom.busyText.textContent = text;
    dom.busyOverlay.hidden = activeOperations === 0;
}

async function runBusy(text, task) {
    setBusy(true, text);
    try {
        return await task();
    } finally {
        setBusy(false);
    }
}

function activateToolButton(tool) {
    document.querySelectorAll('[data-tool]').forEach((button) => {
        button.classList.toggle('active', button.dataset.tool === tool);
    });
}

function updatePointerStatus(point, inside) {
    if (!inside) {
        dom.coordinateStatus.textContent = 'x: —　y: —';
        dom.realCoordinateStatus.textContent = '实际坐标: —';
        return;
    }
    const scale = projectStore.project.map.metersPerPixel;
    dom.coordinateStatus.textContent = `x: ${point.x.toFixed(1)}　y: ${point.y.toFixed(1)} px`;
    dom.realCoordinateStatus.textContent = `实际坐标: ${(point.x * scale).toFixed(2)}, ${(point.y * scale).toFixed(2)} m`;
}

function updateProjectUI(project) {
    const setWhenIdle = (element, value) => {
        if (document.activeElement !== element) element.value = value;
    };
    setWhenIdle(dom.mapId, project.map.id);
    setWhenIdle(dom.mapName, project.map.name);
    setWhenIdle(dom.mapVersion, project.map.version);
    setWhenIdle(dom.mapScale, project.map.metersPerPixel);
    setWhenIdle(dom.coverageRadius, project.settings.coverageRadius);
    dom.brushSize.value = project.settings.brushSize;
    dom.brushSizeValue.textContent = `${project.settings.brushSize} px`;
    dom.snapCenterline.checked = Boolean(project.settings.snapToCenterline);
    dom.snapGrid.checked = Boolean(project.settings.snapToGrid);
    dom.mapSizeStatus.textContent = `${Math.round(project.map.width)} × ${Math.round(project.map.height)} px`;
    dom.emptyHint.hidden = Boolean(project.map.imageDataUrl || project.map.imageUrl || project.annotations.wallPixels.length);
    dom.undo.disabled = !projectStore.canUndo();
    dom.redo.disabled = !projectStore.canRedo();
    const dirty = project.revision !== lastSavedRevision;
    dom.saveState.textContent = dirty ? `未保存 · r${project.revision}` : `已保存 · r${project.revision}`;
    dom.saveState.className = `save-state ${dirty ? 'dirty' : 'saved'}`;
    renderCandidates(project.derived.candidates);
    renderValidation(project.derived.validation);
}

function renderProperties(entity, reference) {
    dom.propertyPanel.replaceChildren();
    if (!entity || !reference) {
        dom.propertyPanel.className = 'property-panel empty-panel';
        dom.propertyPanel.textContent = '在画布上选择标注对象后编辑属性。';
        dom.selectionKind.textContent = '未选择';
        return;
    }
    dom.propertyPanel.className = 'property-panel';
    dom.selectionKind.textContent = `${collectionLabel(reference.collection)} · ${entity.id}`;

    addPropertyField('编号', entity.id, { disabled: true });
    addPropertyField('显示名称', entity.label ?? entity.id, {
        full: true,
        onChange: (value) => editor.updateSelected({ label: value.trim() || entity.id }, 'rename entity')
    });
    addPropertyField('X 坐标', entity.x, {
        type: 'number', step: '0.1',
        onChange: (value) => editor.updateSelected({ x: Number(value) }, 'change entity x')
    });
    addPropertyField('Y 坐标', entity.y, {
        type: 'number', step: '0.1',
        onChange: (value) => editor.updateSelected({ y: Number(value) }, 'change entity y')
    });

    if (reference.collection === 'doors') {
        addPropertySelect('门类型', entity.doorType || 'normal', [
            ['normal', '普通门'], ['fire', '防火门']
        ], (value) => editor.updateSelected({ doorType: value }, 'change door type'));
        addPropertySelect('开闭状态', entity.state || 'closed', [
            ['closed', '关闭'], ['open', '开启']
        ], (value) => editor.updateSelected({ state: value }, 'change door state'));
    }
    if (reference.collection === 'blackBoxes') {
        addPropertySelect('布点属性', entity.mandatory ? 'mandatory' : 'normal', [
            ['normal', '普通点'], ['mandatory', '强制点']
        ], (value) => editor.updateSelected({ mandatory: value === 'mandatory' }, 'change box requirement'));
    }

    const actions = document.createElement('div');
    actions.className = 'property-actions';
    const lock = document.createElement('button');
    lock.className = 'small-action';
    lock.textContent = entity.locked ? '解除锁定' : '锁定对象';
    lock.addEventListener('click', () => editor.toggleSelectedLock());
    const remove = document.createElement('button');
    remove.className = 'small-action danger';
    remove.textContent = '删除对象';
    remove.disabled = entity.locked;
    remove.addEventListener('click', () => editor.deleteSelected());
    actions.append(lock, remove);
    dom.propertyPanel.appendChild(actions);
}

function addPropertyField(label, value, options = {}) {
    const wrapper = document.createElement('label');
    wrapper.className = `property-field ${options.full ? 'full' : ''}`;
    const caption = document.createElement('span');
    caption.textContent = label;
    const input = document.createElement('input');
    input.type = options.type || 'text';
    input.value = value;
    if (options.step) input.step = options.step;
    input.disabled = Boolean(options.disabled);
    if (options.onChange) input.addEventListener('change', () => options.onChange(input.value));
    wrapper.append(caption, input);
    dom.propertyPanel.appendChild(wrapper);
}

function addPropertySelect(label, value, options, onChange) {
    const wrapper = document.createElement('label');
    wrapper.className = 'property-field';
    const caption = document.createElement('span');
    caption.textContent = label;
    const select = document.createElement('select');
    options.forEach(([optionValue, optionLabel]) => {
        const option = document.createElement('option');
        option.value = optionValue;
        option.textContent = optionLabel;
        select.appendChild(option);
    });
    select.value = value;
    select.addEventListener('change', () => onChange(select.value));
    wrapper.append(caption, select);
    dom.propertyPanel.appendChild(wrapper);
}

function collectionLabel(collection) {
    return ({
        doors: '门', exits: '安全出口', refuges: '避难点', stairs: '楼梯',
        gateways: 'LoRa 网关', blackBoxes: '黑盒'
    })[collection] || collection;
}

function renderCandidates(candidates = []) {
    dom.candidateCount.textContent = String(candidates.length);
    dom.candidateList.replaceChildren();
    if (!candidates.length) {
        dom.candidateList.className = 'result-list empty-panel';
        dom.candidateList.textContent = '生成候选点后可在此勾选并采纳。';
        return;
    }
    dom.candidateList.className = 'result-list';
    candidates.forEach((candidate) => {
        const row = document.createElement('label');
        row.className = 'candidate-row';
        const checkWrap = document.createElement('span');
        checkWrap.className = 'candidate-check';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = Boolean(candidate.selected);
        checkbox.addEventListener('change', () => {
            projectStore.commit((project) => {
                const item = project.derived.candidates.find((entry) => entry.id === candidate.id);
                if (item) item.selected = checkbox.checked;
            }, 'select candidate');
        });
        checkWrap.appendChild(checkbox);
        const main = document.createElement('span');
        main.className = 'candidate-main';
        const title = document.createElement('strong');
        title.textContent = `${candidate.id} · ${candidate.reason || '自动候选'}`;
        const coords = document.createElement('small');
        coords.textContent = `${Number(candidate.x).toFixed(1)}, ${Number(candidate.y).toFixed(1)} px`;
        main.append(title, coords);
        const tag = document.createElement('span');
        tag.className = 'candidate-tag';
        tag.textContent = candidate.mandatory ? '强制' : '建议';
        row.append(checkWrap, main, tag);
        row.addEventListener('dblclick', () => editor.focusPoint(candidate, Math.max(editor.view.scale, 2)));
        dom.candidateList.appendChild(row);
    });
}

function renderValidation(validation = {}) {
    const issues = Array.isArray(validation.issues) ? validation.issues : [];
    dom.issueList.replaceChildren();
    const errorCount = issues.filter((issue) => severityOf(issue) === 'error').length;
    const warningCount = issues.filter((issue) => severityOf(issue) === 'warning').length;
    if (validation.valid === null || validation.valid === undefined) {
        dom.issueCount.textContent = '未校验';
        dom.issueCount.className = 'panel-badge neutral';
        dom.validationSummary.textContent = '尚未执行全图校验。';
        dom.validationSummary.className = 'validation-summary';
    } else {
        dom.issueCount.textContent = `${errorCount} 错误 / ${warningCount} 警告`;
        dom.issueCount.className = `panel-badge ${validation.valid ? '' : 'neutral'}`;
        dom.validationSummary.textContent = validation.valid
            ? '校验通过，未发现阻断性问题。'
            : `校验未通过：${errorCount} 个错误，${warningCount} 个警告。`;
        dom.validationSummary.className = `validation-summary ${validation.valid ? 'valid' : 'invalid'}`;
    }
    issues.forEach((issue, index) => {
        const severity = severityOf(issue);
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'issue-row';
        const bar = document.createElement('span');
        bar.className = `issue-bar ${severity}`;
        const content = document.createElement('span');
        content.className = 'issue-content';
        const title = document.createElement('strong');
        title.textContent = issue.message ?? issue.title ?? issue.code ?? `校验问题 ${index + 1}`;
        const detail = document.createElement('p');
        detail.textContent = issue.suggestion ?? issue.detail ?? `${issue.code ?? severity}`;
        content.append(title, detail);
        row.append(bar, content);
        row.addEventListener('click', () => editor.focusIssue(issue));
        dom.issueList.appendChild(row);
    });
}

function severityOf(issue) {
    const severity = String(issue?.severity ?? issue?.level ?? 'error').toLowerCase();
    if (['warn', 'warning'].includes(severity)) return 'warning';
    if (['info', 'information'].includes(severity)) return 'info';
    return 'error';
}

function bindProjectField(element, key, parser = (value) => value) {
    element.addEventListener('change', () => {
        const value = parser(element.value);
        if (value === null) {
            element.value = projectStore.project.map[key];
            notify('输入值无效，已恢复原值', 'warning');
            return;
        }
        projectStore.commit((project) => { project.map[key] = value; }, `change map ${key}`);
    });
}

bindProjectField(dom.mapId, 'id', (value) => {
    const parsed = value.trim();
    return /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(parsed) ? parsed : null;
});
bindProjectField(dom.mapName, 'name', (value) => value.trim() || '未命名地图');
bindProjectField(dom.mapVersion, 'version', (value) => value.trim() || '1.0.0');
bindProjectField(dom.mapScale, 'metersPerPixel', (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
});

document.querySelectorAll('[data-tool]').forEach((button) => {
    button.addEventListener('click', () => editor.setTool(button.dataset.tool));
});

dom.brushSize.addEventListener('input', () => {
    dom.brushSizeValue.textContent = `${dom.brushSize.value} px`;
    projectStore.updateTransient((project) => { project.settings.brushSize = Number(dom.brushSize.value); }, 'brush preview');
});
dom.brushSize.addEventListener('change', () => {
    projectStore.commit((project) => { project.settings.brushSize = Number(dom.brushSize.value); }, 'change brush size');
});
dom.coverageRadius.addEventListener('change', () => {
    const radius = Number(dom.coverageRadius.value);
    if (!Number.isFinite(radius) || radius <= 0) return notify('覆盖半径必须大于 0', 'warning');
    projectStore.commit((project) => { project.settings.coverageRadius = radius; }, 'change coverage radius');
});
dom.snapCenterline.addEventListener('change', () => projectStore.commit((project) => {
    project.settings.snapToCenterline = dom.snapCenterline.checked;
}, 'toggle centerline snap'));
dom.snapGrid.addEventListener('change', () => projectStore.commit((project) => {
    project.settings.snapToGrid = dom.snapGrid.checked;
}, 'toggle grid snap'));

document.querySelectorAll('[data-layer]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => editor.setLayer(checkbox.dataset.layer, checkbox.checked));
});
byId('btn-layers-all').addEventListener('click', () => {
    document.querySelectorAll('[data-layer]').forEach((checkbox) => {
        checkbox.checked = true;
        editor.setLayer(checkbox.dataset.layer, true);
    });
});

byId('btn-import-image').addEventListener('click', () => dom.imageInput.click());
byId('btn-open-json').addEventListener('click', () => dom.jsonInput.click());
byId('btn-save-json').addEventListener('click', saveJson);
byId('btn-load-default').addEventListener('click', loadDefaultMap);
byId('btn-save-server').addEventListener('click', saveToServer);
byId('btn-compile').addEventListener('click', compileMap);
byId('btn-candidates').addEventListener('click', generateCandidates);
byId('btn-validate').addEventListener('click', validateMap);
byId('btn-export').addEventListener('click', exportZip);
byId('btn-adopt-selected').addEventListener('click', () => adoptCandidates(false));
byId('btn-adopt-all').addEventListener('click', () => adoptCandidates(true));
dom.undo.addEventListener('click', () => projectStore.undo());
dom.redo.addEventListener('click', () => projectStore.redo());
byId('btn-fit').addEventListener('click', () => editor.fitToMap());

dom.imageInput.addEventListener('change', async () => {
    const [file] = dom.imageInput.files;
    dom.imageInput.value = '';
    if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type)) return notify('仅支持 PNG 或 JPG 图片', 'error');
    if (file.size > 30 * 1024 * 1024) return notify('底图不能超过 30 MB', 'error');
    if (hasAnnotations(projectStore.project) && !confirm('导入新底图将清空当前标注，是否继续？')) return;
    try {
        await runBusy('正在读取建筑底图…', async () => {
            const dataUrl = await readAsDataUrl(file);
            const dimensions = await readImageDimensions(dataUrl);
            if (dimensions.width > 4096 || dimensions.height > 4096) {
                throw new Error(`图片尺寸 ${dimensions.width}×${dimensions.height} 超过 4096 px 上限，请先缩小底图`);
            }
            const baseName = file.name.replace(/\.[^.]+$/, '') || '未命名地图';
            const project = createDefaultProject({
                map: {
                    id: slugify(baseName) || `map-${Date.now().toString(36)}`,
                    name: baseName,
                    version: '1.0.0',
                    imageDataUrl: dataUrl,
                    imageUrl: '',
                    width: dimensions.width,
                    height: dimensions.height,
                    metersPerPixel: projectStore.project.map.metersPerPixel
                }
            });
            projectStore.replaceProject(project, { reason: 'import image' });
            lastSavedRevision = -1;
            requestAnimationFrame(() => editor.fitToMap());
        });
        notify('底图导入成功，请设置比例尺后开始标注', 'success');
    } catch (error) {
        notify(`底图导入失败：${error.message}`, 'error');
    }
});

dom.jsonInput.addEventListener('change', async () => {
    const [file] = dom.jsonInput.files;
    dom.jsonInput.value = '';
    if (!file) return;
    try {
        const project = normalizeProject(JSON.parse(await file.text()));
        projectStore.replaceProject(project, { reason: 'open json' });
        lastSavedRevision = project.revision;
        requestAnimationFrame(() => editor.fitToMap());
        notify('JSON 工程已加载', 'success');
    } catch (error) {
        notify(`JSON 加载失败：${error.message}`, 'error');
    }
});

async function loadDefaultMap() {
    try {
        await runBusy('正在加载现有地图…', async () => {
            const requestedMapId = dom.mapId.value.trim();
            const useDefault = !requestedMapId || ['default', 'untitled_map'].includes(requestedMapId);
            const response = useDefault
                ? await editorApi.loadDefault()
                : await editorApi.load(requestedMapId);
            const project = normalizeProject(response);
            projectStore.replaceProject(project, { reason: 'load existing map' });
            lastSavedRevision = project.revision;
            requestAnimationFrame(() => editor.fitToMap());
        });
        notify('现有地图已加载', 'success');
    } catch (error) {
        notify(`加载失败：${error.message}`, 'error');
    }
}

function saveJson() {
    const project = projectStore.getSnapshot();
    downloadBlob(new Blob([JSON.stringify(project, null, 2)], { type: 'application/json' }), `${safeName(project.map.id)}.editor.json`);
    lastSavedRevision = project.revision;
    updateProjectUI(projectStore.project);
    notify('工程 JSON 已保存', 'success');
}

async function saveToServer() {
    try {
        await runBusy('正在保存地图工程…', async () => {
            const response = await editorApi.save(projectStore.getSnapshot());
            const revision = Number(response?.revision ?? response?.project?.revision);
            if (Number.isFinite(revision) && revision > projectStore.project.revision) {
                projectStore.updateTransient((project) => { project.revision = revision; }, 'server revision');
            }
            lastSavedRevision = projectStore.project.revision;
            updateProjectUI(projectStore.project);
        });
        notify('地图已保存到服务端', 'success');
    } catch (error) {
        notify(`服务端保存失败：${error.message}`, 'error');
    }
}

async function compileMap() {
    let usedFallback = false;
    try {
        await runBusy('正在编译掩码与中心线…', async () => {
            try {
                const response = await editorApi.compile(projectStore.getSnapshot());
                applyCompilation(response);
            } catch (error) {
                usedFallback = true;
                const fallback = localCompile(projectStore.project);
                applyCompilation(fallback);
                notify(`后端编译暂不可用，已生成本地预览：${error.message}`, 'warning', 5200);
            }
        });
        if (!usedFallback) notify('地图编译完成，中心线已更新', 'success');
    } catch (error) {
        notify(`编译失败：${error.message}`, 'error');
    }
}

function applyCompilation(response) {
    const payload = unwrap(response) ?? {};
    const source = payload.compiled ?? payload.compilation ?? payload.project?.derived ?? payload;
    const centerline = source.centerline ?? source.centerlines ?? source.skeleton ?? source.skeleton_points ?? [];
    const candidates = source.candidates ?? payload.candidates;
    const topology = source.topology ?? payload.topology ?? {};
    const masks = source.masks ?? payload.masks ?? {};
    const validation = normalizeValidation(source.validation ?? payload.validation ?? (payload.issues ? payload : null));
    projectStore.commit((project) => {
        if (centerline) project.derived.centerline = centerline;
        if (candidates) project.derived.candidates = normalizeCandidates(candidates);
        project.derived.topology = topology;
        project.derived.masks = masks;
        if (validation) project.derived.validation = validation;
    }, 'compile map');
}

async function generateCandidates() {
    let usedFallback = false;
    try {
        await runBusy('正在分析布点候选位置…', async () => {
            let candidates;
            let skeletonPoints = null;
            try {
                const response = unwrap(await editorApi.candidates(projectStore.getSnapshot())) ?? {};
                candidates = response.candidates ?? response.candidate_boxes ?? response;
                skeletonPoints = response.skeleton_points ?? response.skeleton ?? response.centerline ?? null;
            } catch (error) {
                usedFallback = true;
                candidates = createLocalCandidates(projectStore.project);
                notify(`候选点服务暂不可用，已使用本地规则预览：${error.message}`, 'warning', 5200);
            }
            projectStore.commit((project) => {
                project.derived.candidates = normalizeCandidates(candidates);
                if (skeletonPoints) project.derived.centerline = { points: skeletonPoints };
            }, 'generate candidates');
        });
        if (!usedFallback) notify('候选点已生成，可勾选后采纳', 'success');
    } catch (error) {
        notify(`候选点生成失败：${error.message}`, 'error');
    }
}

async function validateMap() {
    let usedFallback = false;
    try {
        await runBusy('正在执行全图校验…', async () => {
            let validation;
            try {
                validation = normalizeValidation(await editorApi.validate(projectStore.getSnapshot()));
            } catch (error) {
                usedFallback = true;
                validation = localValidate(projectStore.project);
                notify(`校验服务暂不可用，已执行本地基础校验：${error.message}`, 'warning', 5200);
            }
            projectStore.commit((project) => { project.derived.validation = validation; }, 'validate map');
        });
        if (!usedFallback) {
            const valid = projectStore.project.derived.validation.valid;
            notify(valid ? '地图校验通过' : '校验完成，请处理问题面板中的错误', valid ? 'success' : 'warning');
        }
    } catch (error) {
        notify(`校验失败：${error.message}`, 'error');
    }
}

function normalizeValidation(value) {
    if (!value) return null;
    const payload = unwrap(value) ?? {};
    const source = payload.validation ?? payload;
    const issues = Array.isArray(source.issues) ? source.issues : [];
    const valid = source.valid ?? !issues.some((issue) => severityOf(issue) === 'error');
    return { valid: Boolean(valid), issues, summary: source.summary ?? {} };
}

function adoptCandidates(all) {
    const candidates = projectStore.project.derived.candidates.filter((candidate) => all || candidate.selected);
    if (!candidates.length) return notify(all ? '当前没有候选点' : '请先勾选候选点', 'warning');
    let added = 0;
    projectStore.commit((project) => {
        candidates.forEach((candidate) => {
            const duplicate = project.entities.blackBoxes.some((box) => Math.hypot(box.x - candidate.x, box.y - candidate.y) < 2);
            if (duplicate) return;
            const box = createEntity(project, 'blackBoxes', candidate, {
                mandatory: Boolean(candidate.mandatory),
                source: 'automatic'
            });
            project.entities.blackBoxes.push(box);
            added += 1;
        });
    }, 'adopt candidates');
    notify(`已采纳 ${added} 个候选点${added < candidates.length ? '，重复位置已跳过' : ''}`, 'success');
}

async function exportZip() {
    const project = projectStore.getSnapshot();
    let blob;
    let fallback = false;
    try {
        await runBusy('正在生成标准地图包…', async () => {
            try {
                blob = await editorApi.exportZip(project);
                if (!blob?.size) throw new Error('服务端返回空文件');
            } catch (error) {
                fallback = true;
                blob = createLocalExport(project);
                notify(`服务端导出暂不可用，已生成本地兼容 ZIP：${error.message}`, 'warning', 5200);
            }
        });
        downloadBlob(blob, `${safeName(project.map.id)}-${safeName(project.map.version)}.zip`);
        if (!fallback) notify('标准地图包已导出', 'success');
    } catch (error) {
        notify(`导出失败：${error.message}`, 'error');
    }
}

function createLocalExport(project) {
    const mapMeta = {
        id: project.map.id,
        name: project.map.name,
        version: project.map.version,
        width: project.map.width,
        height: project.map.height,
        meters_per_pixel: project.map.metersPerPixel,
        coordinate_system: 'image-pixel-x-y'
    };
    const files = {
        'project.json': JSON.stringify(project, null, 2),
        'map_meta.json': JSON.stringify(mapMeta, null, 2),
        'boxes.json': JSON.stringify(project.entities.blackBoxes, null, 2),
        'exits.json': JSON.stringify(project.entities.exits, null, 2),
        'refuges.json': JSON.stringify(project.entities.refuges, null, 2),
        'doors.json': JSON.stringify(project.entities.doors, null, 2),
        'stairs.json': JSON.stringify(project.entities.stairs, null, 2),
        'gateways.json': JSON.stringify(project.entities.gateways, null, 2),
        'topology.json': JSON.stringify(project.derived.topology, null, 2),
        'annotations.json': JSON.stringify(project.annotations, null, 2)
    };
    const image = decodeDataUrl(project.map.imageDataUrl);
    if (image) files[`base_map.${image.extension}`] = image.bytes;
    return createStoredZip(files);
}

function localCompile(project) {
    const paths = project.annotations.strokes.walkable
        .filter((stroke) => stroke.points.length > 1)
        .map((stroke) => stroke.points.map((point) => ({ x: point.x, y: point.y })));
    return {
        centerline: paths,
        candidates: createLocalCandidates(project),
        topology: { nodes: [], edges: [], source: 'local-preview' },
        masks: {}
    };
}

function createLocalCandidates(project) {
    const candidates = [];
    const seen = [];
    const add = (point, reason, mandatory = false) => {
        if (!Number.isFinite(Number(point.x)) || !Number.isFinite(Number(point.y))) return;
        if (seen.some((other) => Math.hypot(other.x - point.x, other.y - point.y) < 4)) return;
        const candidate = {
            id: `C${String(candidates.length + 1).padStart(3, '0')}`,
            x: Number(point.x), y: Number(point.y), reason, mandatory, selected: mandatory
        };
        candidates.push(candidate);
        seen.push(candidate);
    };
    project.entities.exits.forEach((point) => add(point, '安全出口', true));
    project.entities.stairs.forEach((point) => add(point, '楼梯口', true));
    project.entities.refuges.forEach((point) => add(point, '避难点入口', true));
    project.entities.doors.forEach((point) => add(point, point.doorType === 'fire' ? '防火门' : '门', point.doorType === 'fire'));

    const step = Math.max(8, project.settings.maxBoxDistance / project.map.metersPerPixel);
    const derivedPaths = project.derived.centerline?.points ? [] : extractCenterlinePaths(project.derived.centerline);
    const paths = derivedPaths.length
        ? derivedPaths
        : project.annotations.strokes.walkable.map((stroke) => stroke.points);
    paths.forEach((path) => {
        if (!path.length) return;
        add(path[0], '走廊端点');
        let travelled = 0;
        let nextSample = step;
        for (let index = 1; index < path.length; index += 1) {
            const previous = path[index - 1];
            const current = path[index];
            const segment = Math.hypot(current.x - previous.x, current.y - previous.y);
            while (travelled + segment >= nextSample && segment > 0) {
                const ratio = (nextSample - travelled) / segment;
                add({ x: previous.x + (current.x - previous.x) * ratio, y: previous.y + (current.y - previous.y) * ratio }, '长走廊补点');
                nextSample += step;
            }
            travelled += segment;
            if (index < path.length - 1 && isCorner(previous, current, path[index + 1])) add(current, '走廊转弯', true);
        }
        if (path.length > 1) add(path.at(-1), '走廊端点');
    });
    return candidates;
}

function isCorner(a, b, c) {
    const ab = { x: a.x - b.x, y: a.y - b.y };
    const cb = { x: c.x - b.x, y: c.y - b.y };
    const denominator = Math.hypot(ab.x, ab.y) * Math.hypot(cb.x, cb.y);
    if (!denominator) return false;
    const cosine = (ab.x * cb.x + ab.y * cb.y) / denominator;
    return cosine > -0.92;
}

function localValidate(project) {
    const issues = [];
    const push = (code, severity, message, geometry = null, suggestion = '') => {
        issues.push({ code, severity, message, geometry, suggestion });
    };
    if (!project.map.imageDataUrl && !project.map.imageUrl && !project.annotations.wallPixels.length) {
        push('MAP_BASE_MISSING', 'warning', '尚未导入建筑底图', null, '导入 PNG/JPG 或加载现有地图。');
    }
    if (!(project.map.metersPerPixel > 0)) push('MAP_SCALE_INVALID', 'error', '地图比例尺无效', null, '设置大于 0 的米/像素值。');
    if (!project.annotations.strokes.walkable.length) push('WALKABLE_MISSING', 'error', '尚未标注可通行区域', null, '使用通行区笔刷标注走廊和房间。');
    if (!project.entities.exits.length) push('EXIT_MISSING', 'error', '地图没有安全出口', null, '至少添加一个安全出口。');
    if (!project.entities.blackBoxes.length) push('BOX_MISSING', 'warning', '地图没有部署黑盒', null, '人工添加或生成并采纳候选点。');

    Object.entries(project.entities).forEach(([collection, entities]) => entities.forEach((entity) => {
        if (entity.x < 0 || entity.y < 0 || entity.x > project.map.width || entity.y > project.map.height) {
            push('ENTITY_OUT_OF_BOUNDS', 'error', `${entity.id} 超出地图边界`, entity, '移动到有效地图范围内。');
        }
    }));
    project.entities.blackBoxes.forEach((box, index) => {
        if (pointTouchesWall(box, project.annotations.strokes.walls)) {
            push('BOX_ON_WALL', 'error', `${box.id} 落在墙体标注上`, box, '将黑盒移动到可安装区域。');
        }
        const duplicate = project.entities.blackBoxes.slice(0, index)
            .find((other) => Math.hypot(box.x - other.x, box.y - other.y) < 2);
        if (duplicate) push('BOX_OVERLAP', 'error', `${box.id} 与 ${duplicate.id} 重叠`, box, '删除重复点或调整位置。');
    });
    return { valid: !issues.some((issue) => severityOf(issue) === 'error'), issues, summary: { source: 'local-basic' } };
}

function pointTouchesWall(point, strokes) {
    return strokes.some((stroke) => {
        if (!stroke.points.length) return false;
        if (stroke.points.length === 1) return Math.hypot(point.x - stroke.points[0].x, point.y - stroke.points[0].y) <= stroke.size / 2;
        for (let index = 1; index < stroke.points.length; index += 1) {
            if (pointSegmentDistance(point, stroke.points[index - 1], stroke.points[index]) <= stroke.size / 2) return true;
        }
        return false;
    });
}

function pointSegmentDistance(point, a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSquared = dx * dx + dy * dy;
    if (!lengthSquared) return Math.hypot(point.x - a.x, point.y - a.y);
    const t = Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared));
    return Math.hypot(point.x - (a.x + t * dx), point.y - (a.y + t * dy));
}

function hasAnnotations(project) {
    return project.annotations.strokes.walkable.length
        || project.annotations.strokes.walls.length
        || Object.values(project.entities).some((items) => items.length);
}

function readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error || new Error('文件读取失败'));
        reader.readAsDataURL(file);
    });
}

function readImageDimensions(source) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
        image.onerror = () => reject(new Error('图片格式损坏或浏览器无法解码'));
        image.src = source;
    });
}

function decodeDataUrl(dataUrl) {
    const match = /^data:(image\/(png|jpeg));base64,(.+)$/i.exec(dataUrl || '');
    if (!match) return null;
    const binary = atob(match[3]);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return { extension: match[2].toLowerCase() === 'jpeg' ? 'jpg' : 'png', bytes };
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function slugify(value) {
    return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 64);
}
function safeName(value) { return String(value || 'map').replace(/[<>:"/\\|?*\x00-\x1F]/g, '_').slice(0, 80); }

projectStore.subscribe((project) => updateProjectUI(project));
updateProjectUI(projectStore.project);
editor.setTool('select');
requestAnimationFrame(() => editor.fitToMap());

window.addEventListener('beforeunload', (event) => {
    if (projectStore.project.revision === lastSavedRevision) return;
    event.preventDefault();
    event.returnValue = '';
});
