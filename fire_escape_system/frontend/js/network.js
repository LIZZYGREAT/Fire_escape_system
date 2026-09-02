// frontend/js/network.js

export const GlobalState = {
    fireMap: new Map(),
    heatMap: new Map(),
    smokeMap: new Map(),
    topologyTree: new Map(), 
    wallData: [],
    exitsData: [],
    mapMetadata: {},
    tick: 0,
    isBaselineSynced: false,
    // 默认面向群众，只消费 next；消防搜救模式由用户显式切换后才消费 rescue_next。
    guidanceMode: 'evacuation'
};

class NetworkEngine {
    constructor(url) {
        this.url = url;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 8;
        this.baseReconnectDelay = 1000; 
    }

    connect() {
        console.log(`[Network] 尝试连接到核心服务器: ${this.url}`);
        this.ws = new WebSocket(this.url);

        this.ws.onopen = this.handleOpen.bind(this);
        this.ws.onmessage = this.handleMessage.bind(this);
        this.ws.onclose = this.handleClose.bind(this);
        this.ws.onerror = this.handleError.bind(this);
    }

    _updateNetUI(isOnline) {
        const dot = document.getElementById('status-light');
        const text = document.getElementById('status-text');
        if (!dot || !text) return;
        if (isOnline) {
            dot.classList.add('online');
            text.innerText = 'WS CONNECTED';
            text.style.color = '#ccc';
        } else {
            dot.classList.remove('online');
            text.innerText = 'DISCONNECTED';
            text.style.color = '#888';
        }
    }

    handleOpen() {
        console.log("[Network] WebSocket 连接握手成功！");
        this.reconnectAttempts = 0; 
        this._updateNetUI(true);

        const syncProbe = { type: "request_full_sync" };
        this.ws.send(JSON.stringify(syncProbe));
        fetch('/api/runtime/snapshot', { cache: 'no-store' })
            .then((response) => response.json())
            .then((snapshot) => this.processFullSync(snapshot))
            .catch((error) => console.warn('[Network] HTTP baseline fallback failed', error));
    }

    handleMessage(event) {
        try {
            const data = JSON.parse(event.data);

            if (data.type === "full_sync") {
                this.processFullSync(data);
            } else if (data.type === "tick_update") {
                this.processTickUpdate(data);
            } else if (data.type === "runtime_settings") {
                document.dispatchEvent(new CustomEvent('runtimeSettingsChanged', { detail: data }));
            } else if (data.type === "error") {
                document.dispatchEvent(new CustomEvent('runtimeError', { detail: data }));
            }
        } catch (error) {
            console.error("[Network] 数据帧反序列化失败:", error);
        }
    }

    handleClose(event) {
        console.warn(`[Network] 连接已断开`);
        GlobalState.isBaselineSynced = false; 
        this._updateNetUI(false);
        this.triggerReconnect();
    }

    handleError(error) {
        console.error("[Network] 底层 Socket 发生异常:", error);
    }

    triggerReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) return;
        const delay = this.baseReconnectDelay * Math.pow(2, this.reconnectAttempts);
        this.reconnectAttempts++;
        setTimeout(() => { this.connect(); }, delay);
    }

    sendControl(command, payload = {}) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: "control",
                command: command,
                ...payload
            }));
        }
    }

    processFullSync(data) {
        GlobalState.fireMap.clear();
        GlobalState.heatMap.clear();
        GlobalState.smokeMap.clear();
        GlobalState.topologyTree.clear();
        GlobalState.wallData = data.wall_data || []; 
        GlobalState.exitsData = data.exits_data || [];
        GlobalState.mapMetadata = data.map_metadata || {};
        GlobalState.tick = Number(data.tick) || 0;
        
        if (data.fire_data) {
            data.fire_data.forEach(([x, y, weight]) => {
                GlobalState.fireMap.set(`${x},${y}`, weight);
            });
        }

        if (data.environment_data) {
            data.environment_data.forEach(([x, y, heat, smoke, risk]) => {
                const key = `${x},${y}`;
                GlobalState.heatMap.set(key, heat);
                GlobalState.smokeMap.set(key, smoke);
                GlobalState.fireMap.set(key, risk);
            });
        }

        if (data.topology_tree) {
            Object.entries(data.topology_tree).forEach(([key, value]) => {
                GlobalState.topologyTree.set(key, value);
            });
        }

        GlobalState.isBaselineSynced = true;
        document.dispatchEvent(new CustomEvent('baselineSynced'));
    }

    processTickUpdate(data) {
        if (!GlobalState.isBaselineSynced) return;

        GlobalState.mapMetadata = data.map_metadata || GlobalState.mapMetadata;
        GlobalState.tick = Number(data.tick) || GlobalState.tick;

        if (data.fire_diff) {
            data.fire_diff.forEach(([x, y, weight]) => {
                GlobalState.fireMap.set(`${x},${y}`, weight);
            });
        }

        if (data.environment_diff) {
            data.environment_diff.forEach(([x, y, heat, smoke, risk]) => {
                const key = `${x},${y}`;
                if (heat <= 1.01) GlobalState.heatMap.delete(key);
                else GlobalState.heatMap.set(key, heat);
                if (smoke <= 0.1) GlobalState.smokeMap.delete(key);
                else GlobalState.smokeMap.set(key, smoke);
                if (risk <= 1.01) GlobalState.fireMap.delete(key);
                else GlobalState.fireMap.set(key, risk);
            });
        }

        if (data.topology_tree) {
            Object.entries(data.topology_tree).forEach(([key, value]) => {
                GlobalState.topologyTree.set(key, value);
            });
        }
        
        document.dispatchEvent(new CustomEvent('tickUpdated'));
    }
}

function resolveWebSocketUrl() {
    // 与当前页面同源，部署到任意主机或 HTTPS 反向代理后无需改代码。
    if (window.location.host) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${protocol}//${window.location.host}/ws`;
    }
    // 直接以 file:// 打开页面时保留本地开发回退。
    return 'ws://127.0.0.1:8000/ws';
}

export const networkEngine = new NetworkEngine(resolveWebSocketUrl());
