export class ApiError extends Error {
    constructor(message, status = 0, details = null) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.details = details;
    }
}

const unwrap = (payload) => payload?.data ?? payload?.result ?? payload;

async function parseError(response) {
    const contentType = response.headers.get('content-type') || '';
    try {
        if (contentType.includes('application/json')) {
            const payload = await response.json();
            return payload.detail ?? payload.message ?? JSON.stringify(payload);
        }
        return (await response.text()) || response.statusText;
    } catch {
        return response.statusText || `HTTP ${response.status}`;
    }
}

async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData) && !headers.has('content-type')) {
        headers.set('content-type', 'application/json');
    }
    headers.set('accept', options.accept || 'application/json');

    let response;
    try {
        response = await fetch(path, { ...options, headers });
    } catch (error) {
        throw new ApiError(`无法连接服务器：${error.message}`, 0, error);
    }
    if (!response.ok) {
        throw new ApiError(await parseError(response), response.status);
    }
    return response;
}

async function requestJson(path, options = {}) {
    const response = await request(path, options);
    if (response.status === 204) return null;
    const text = await response.text();
    if (!text) return null;
    try {
        return unwrap(JSON.parse(text));
    } catch {
        throw new ApiError('服务器返回了无法解析的 JSON', response.status, text);
    }
}

const jsonOptions = (method, project) => ({
    method,
    body: JSON.stringify(project)
});

export const editorApi = {
    loadDefault() {
        return requestJson('/api/maps/default');
    },

    load(mapId) {
        return requestJson(`/api/maps/${encodeURIComponent(mapId)}`);
    },

    save(project) {
        const mapId = project?.map?.id || 'default';
        return requestJson(`/api/maps/${encodeURIComponent(mapId)}`, jsonOptions('PUT', project));
    },

    compile(project) {
        return requestJson('/api/maps/compile', jsonOptions('POST', project));
    },

    validate(project) {
        return requestJson('/api/maps/validate', jsonOptions('POST', project));
    },

    candidates(project) {
        return requestJson('/api/placement/candidates', jsonOptions('POST', project));
    },

    async exportZip(project) {
        const mapId = project?.map?.id || 'default';
        const response = await request(`/api/maps/${encodeURIComponent(mapId)}/export`, {
            ...jsonOptions('POST', project),
            accept: 'application/zip, application/octet-stream, application/json'
        });
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            const payload = unwrap(await response.json());
            const downloadUrl = payload?.downloadUrl ?? payload?.download_url ?? payload?.url;
            if (!downloadUrl) throw new ApiError('导出接口未返回 ZIP 或下载地址', response.status, payload);
            const downloadResponse = await request(downloadUrl, { accept: 'application/zip, application/octet-stream' });
            return downloadResponse.blob();
        }
        return response.blob();
    }
};

export { unwrap };
