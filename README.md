# 智能消防逃生与搜救指示系统

本仓库包含两条相互隔离但共享地图模型的工作流：

- **运行监控**：在本地中央计算节点上模拟危险场、动态更新黑盒方向，并通过 WebSocket 向监控页发送完整快照和增量状态。
- **地图部署**：导入建筑平面图，人工标注通行区、墙体和消防语义点，编辑黑盒布点，经过后端编译与校验后导出标准地图包。

系统按“地图编译期”和“在线运行期”拆分。规划与危险场代码只读取已校验的地图模型，不再直接依赖某张 PNG 或一组写死坐标。当前 `demo_building` 由原有 250 × 250 地图迁移而来，用作兼容与回归基线。

## 快速开始

需要 Python 3.9 或更高版本。

```powershell
cd fire_escape_system/backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

启动后访问：

- 运行监控：<http://127.0.0.1:8000/>
- 地图布点编辑器：<http://127.0.0.1:8000/editor/>
- OpenAPI 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

前端为原生 ES Modules 与 Canvas，无需 Node.js 构建，也不依赖公网 CDN。请通过 FastAPI 地址访问，不要直接双击 HTML 文件。

## 地图编辑闭环

1. 导入 PNG/JPG，或载入仓库中的演示地图。
2. 设置地图编号、版本和米/像素比例尺。
3. 使用画笔标注可通行区与墙体。
4. 添加出口、避难点、楼梯、门、网关和人工黑盒。
5. 请求后端提取中心线并生成候选布点；人工拖动、锁定、增加或删除节点。
6. 执行位置、覆盖、视线、静态路径链和 N-1 校验。
7. 保存为草稿；校验通过后编译并导出 ZIP 标准地图包。

编辑器不会直接覆盖当前运行地图。任何结构或布点调整都应创建新版本，并在验证后单独发布/激活。

## 核心接口

| 接口 | 用途 |
|---|---|
| `GET /api/maps/default` | 载入迁移后的演示地图草稿 |
| `GET/PUT /api/maps/{map_id}` | 读取或保存地图草稿 |
| `POST /api/maps/compile` | 编译语义掩码、中心线、拓扑和报告 |
| `POST /api/maps/validate` | 执行地图与布点静态校验 |
| `POST /api/placement/candidates` | 生成半自动黑盒候选点 |
| `POST /api/maps/{map_id}/export` | 导出标准地图包 |
| `WS /ws` | 运行监控完整快照、增量更新与控制命令 |

实际请求/响应 schema 以启动后的 OpenAPI 文档为准。

## 测试

```powershell
cd fire_escape_system/backend
python -m pytest -q
```

前端脚本无需打包，可用 Node.js 做静态语法检查：

```powershell
Get-ChildItem ..\frontend -Recurse -Filter *.js | ForEach-Object { node --check $_.FullName }
```

重点安全不变量包括：

- 火焰和烟雾不得斜穿封闭墙角；
- 出口和黑盒必须位于合法通行区域；
- 绿色逃生链必须无环并最终到达出口或避难点；
- 无可生存路线时，群众指令必须为 `SOS` 且不带方向；
- 高风险搜救路径只能通过独立的消防字段/视图展示；
- 相同地图草稿与参数应产生可重复的编译结果和版本信息。

## 目录概览

```text
fire_escape_system/
├─ backend/
│  ├─ app/                 # 领域模型、地图服务、API 与运行时编排
│  ├─ core/                # 现有危险场和增量图规划内核
│  ├─ maps/demo_building/  # 原项目迁移后的版本化地图配置
│  ├─ data/                # 原始演示掩码和图像资产
│  └─ tests/               # 地图、危险传播和接口回归测试
└─ frontend/
   ├─ index.html           # 运行监控
   ├─ editor/              # 地图与人工布点编辑器
   ├─ css/
   └─ js/
```

更完整的需求基线和工程边界见 `docs/智能消防逃生与搜救指示系统.md` 与 `docs/地图布点编辑器.md`。
