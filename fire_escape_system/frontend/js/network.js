// frontend/js/network.js

export const GlobalState = {
    fireMap: new Map(),
    topologyTree: new Map(), 
    wallData: [],
    exitsData: [],
    isBaselineSynced: false
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
    }

    handleMessage(event) {
        try {
            const data = JSON.parse(event.data);

            if (data.type === "full_sync") {
                this.processFullSync(data);
            } else if (data.type === "tick_update") {
                this.processTickUpdate(data);
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

    sendControl(command) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: "control",
                command: command
            }));
        }
    }

    processFullSync(data) {
        GlobalState.fireMap.clear();
        GlobalState.topologyTree.clear();
        GlobalState.wallData = data.wall_data || []; 
        GlobalState.exitsData = data.exits_data || [];
        
        if (data.fire_data) {
            data.fire_data.forEach(([x, y, weight]) => {
                GlobalState.fireMap.set(`${x},${y}`, weight);
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

        if (data.fire_diff) {
            data.fire_diff.forEach(([x, y, weight]) => {
                GlobalState.fireMap.set(`${x},${y}`, weight);
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

export const networkEngine = new NetworkEngine("ws://127.0.0.1:8000/ws");