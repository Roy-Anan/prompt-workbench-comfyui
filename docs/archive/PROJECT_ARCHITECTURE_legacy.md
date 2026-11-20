# PROJECT_ARCHITECTURE — 架构与模块边界

> **已废弃归档（2026-11-20）**：本文件为早期 Tauri/桌面宿主阶段的项目控制视图，已被现行架构文档 `docs/ARCHITECTURE.md`（ADR-0002 本机 Web 运行时）取代；仅留作历史追溯，不再回写。

- 文档版本：1.0
- 日期：2026-11-17
- 适用阶段：M0 基线规划
- 配套基线：`docs/ARCHITECTURE.md`、`docs/decisions/ADR-0002-LOCAL-WEB-RUNTIME.md`（本文件为项目控制视图，不替代基线架构文档）

## 1. 总体拓扑

> **方向（ADR-0002，2026-11-18）**：正式运行形态为免安装本机浏览器 Web 应用，FastAPI 同源托管前端静态产物与 API；Tauri/Rust/PyInstaller/NSIS 路线已废弃，历史代码归档于 `legacy/src-tauri/`，不参与 Web 运行时。

模块化单体，单一 Python 进程对内提供 API 与静态页面，一组本地外部服务：

```text
┌──────────────────────── 本机浏览器 (Edge / Chrome) ─────────────────────────┐
│  访问 http://127.0.0.1:17890 · 同源 fetch · Cookie 会话票据 + CSRF 头        │
└───────────────┬──────────────────────────────────────────────────────────────┘
                │ 同源 loopback：FastAPI 同时返回前端静态文件与 /api/*
┌───────────────▼──────────────────────── Python 业务服务 (FastAPI) ────────────┐
│  静态托管 frontend/dist · SPA 回退 · api 路由 · core/domain/workflow/rules/…   │
│  repositories/adapters(ollama·comfyui·gpu)                                    │
└───────┬──────────────────────────────┬─────────────────────────────────┬──────┘
        │ SQLite 数据层                 │ Ollama Adapter                   │ ComfyUI Adapter
        └──────────────────────────────┴─────────────────────────────────┴──────┘
```

## 2. 模块职责与边界

### 2.1 启动脚本与进程生命周期

- 职责：`workbench_launcher.py` 复用项目 `.venv` 启动 FastAPI，做 loopback/端口/实例身份校验，健康检查通过后打开浏览器；`workbench_stopper.py` 按 PID + 身份记录回收本工作台进程树。
- 边界：**不实现模型规则或 Prompt 业务逻辑**；不关闭 ComfyUI / Ollama 用户服务；脚本层保持薄。

### 2.2 React 前端

- 职责：中文桌面 UI、Prompt 结构化区块与双视图、候选/草稿/历史/LoRA/队列/设置页面；展示服务返回的差异、状态与确认提示。
- 边界：只通过本地后端 API 调用业务能力；**不直接读写工作流文件，不直连 ComfyUI，不直接控制 Ollama 进程**；前端状态不作为安全校验依据。

### 2.3 Python 后端（业务逻辑权威实现）

> **特别注意：Python 后端是业务逻辑的权威实现。** 规则判定、白名单校验、安全约束、状态机与数据访问以 Python 为准；前端不得复制或绕过这些规则。

- **api**：HTTP 路由、请求/响应模型、错误码；不放业务规则。
- **core**：配置、日志、应用生命周期、异常类型。
- **domain**：核心实体与值对象。
- **workflow**：UI/API workflow 解析、节点识别、Prompt 白名单、快照与差异验证。
- **rules**：模型身份、规则导入、三级继承、优先级、冲突检测、动态检索。
- **prompt**：创作任务构造、候选校验、翻译、区块、语法与 Token 检查。
- **queue**：先进先出、暂停/恢复、取消、重试、崩溃恢复。
- **drafts**：手动保存、版本历史、回收站、模型快照。
- **history**：生成记录、文件路径状态、筛选、导出。
- **loras**：元数据读取、角色卡、组合、工作流联动。
- **repositories**：SQLite 数据访问与迁移。
- **安全边界由程序强制执行**，不依赖 LLM 自觉遵守（见第 5 节）。

### 2.4 SQLite 数据层

- 职责：保存模型身份、规则版本、工作流识别、LoRA 卡片、草稿、生成任务、队列、设置与诊断事件等结构化数据与必要快照。
- 边界：图片原件、模型权重、原始工作流**不入库**；数据库只存元数据、路径、索引与快照；关键 JSON 快照记录 `schema_version`；迁移先备份/校验。

### 2.5 Ollama Adapter

- 职责：模型发现、选择、生成调用、停止、卸载与运行状态验证；隔离本地 LLM 运行时细节，未来可扩展其他本地运行时。
- 边界：第一版只实现 Ollama adapter，不下载模型；不可用时不自动切云端；不擅自杀掉 Ollama 服务进程。

### 2.6 ComfyUI Adapter

- 职责：连接配置与连通性检测；HTTP 提交 API workflow 接收 prompt_id；WebSocket 监听执行事件；queue/history/status 校准；断线重连、状态恢复、明确中断、手动重试。
- 边界：第一版只支持一个 ComfyUI 实例，地址端口可配置；输出目录从配置或 ComfyUI 返回数据确定，不写死某台电脑路径；工作台退出不终止已提交的 ComfyUI 任务。

### 2.7 知识库与 Prompt 注入系统

- 职责：组织角色文件、知识文件、行为规则文件；支持动态组合、版本记录、调用追溯；提供默认文件保护与自定义文件管理。
- 边界：普通角色/知识文件不能覆盖程序级安全约束；规范详见 `docs/PROJECT_CONTROL/INJECTION_SYSTEM_SPEC.md`。

### 2.8 存储管理模块

- 职责：数据目录配置、备份恢复、数据库迁移、文件路径与元数据管理、诊断数据归集。
- 边界：不复制原图与模型权重进工作台目录；只扫描用户登记的模型目录；备份覆盖约定数据域，恢复前校验完整性。

### 2.9 显存管理模块

- 职责：采集显存与进程状态；出图前驱动 LLM 卸载并做多信号验证；显存释放状态不足或未知时阻止提交。
- 边界：能力不足或无法确认时报告 unknown 并安全失败；阈值需真机测量，未测试前不硬编码保证值。

## 3. 进程与通信约束

- Python 服务只绑定 `127.0.0.1`，不监听 `0.0.0.0`；使用随机可用端口与每次启动随机会话凭据；限制 CORS/Origin，拒绝无会话凭据请求。
- 对输入做 schema 校验；所有外部文件与研究资料视为不可信数据。
- 外部服务（ComfyUI、Ollama、NVIDIA 驱动/显存）由用户环境提供；无法确认时安全失败。

## 4. 安全管线（顺序固定）

1. 读取原始 workflow，不修改源文件。
2. 解析/转换为内部只读模型；提交前使用 API workflow。
3. 建立内容指纹与 Prompt 节点白名单。
4. 规则引擎按当前工作流实际模型组合选择规则。
5. 候选输出进入 schema/术语/语法/正负向/规则检查。
6. 用户审核候选并明确确认写入。
7. 基于原始 API workflow 深拷贝，只修改白名单路径。
8. 对比原始快照与新快照；任何非白名单差异即拒绝。
9. 再次读取/校验工作副本；确认 Prompt 值准确、其他字段不变。
10. 入队前执行 LLM unload + 状态验证 + GPU 显存验证。
11. 全部通过后才调用 ComfyUI 提交接口。
12. WebSocket 事件与 HTTP 状态共同确认任务状态；未知状态不推断成功/失败。

## 5. 程序级强制安全约束（不依赖 LLM）

- 工作流节点白名单：只有确认过的 Prompt 白名单字段可写。
- 非白名单字段保护：模型选择、LoRA、采样参数、尺寸、节点结构等一律禁止修改。
- 显存安全检查：卸载与释放未验证通过前禁止提交出图。
- 未确认回滚时禁止提交：写入失败或回滚状态不确定时停止流程。

## 6. 模块依赖方向

- 前端 → api（后端）→ 领域服务 → repositories/adapters。
- 上层可调用下层，下层不反向依赖前端。
- 规则、安全与状态判定集中在 Python 后端，不在前端或 Prompt 文件中重新实现。

## 7. M0 当前落地范围

### 7.1 TASK-002（2026-11-17，0.1.0-dev.1）

本节只记录本次代码已落地与实测状态，不代表第 2 节规划模块已全部实现。

- **已落地并在后端实测**：FastAPI 会话令牌校验、`/api/health`、`/api/settings` 读写、`/api/storage/validate`、`/api/services/status`（Ollama / ComfyUI 仅连通性检测）、管理目录初始化；后端绑定 `127.0.0.1`。
- **已落地并在前端静态构建通过**：React 设置/状态界面、Vite + TypeScript 工程；前端经同源相对路径访问后端。
- **历史遗留（已废弃）**：早期 Tauri 2 宿主代码已归档至 `legacy/src-tauri/`，不参与 Web 运行时；对应 Rust 工具链缺失记录仅作历史说明。

### 7.2 TASK-002.1（2026-11-17，0.1.0-dev.2）

- **SQLite 基础层已落地并在后端实测**：数据库文件位于 `storage.database/workbench.sqlite3`；初始表 `schema_version(version PK, applied_at)`、`app_metadata(key PK, value, updated_at)`，`schema_version` 当前为 1；重复初始化保留版本号。业务实体表（规则、草稿、历史、LoRA 等）属后续阶段。
- **根目录与配置规则**：`storage.root` 由后端固定（数据根后迁至项目内 `userdata/`，见 7.4），客户端修改 root 返回 400；管理子目录、外部模型路径（checkpoint/lora，可留空）、连接地址（ollama_url/comfyui_url）按白名单校验；损坏 `settings.json` 先备份为 `settings.corrupt-<时间戳>-<hex>.json` 再重建默认配置。
- **日志职责**：服务 stdout/stderr 记录到数据根日志；会话令牌仅经环境变量传递，不写入日志；启动失败/超时错误附带日志路径。
- **健康检查**：宿主在 TCP 连通后再以 Bearer 令牌请求 `/api/health`，返回 200 且体含 `ok:true` 才判定就绪，不仅凭端口打开判定。
- **尚未落地**：第 2.3 节列出的 domain/workflow/rules/prompt/queue/drafts/history/loras 等业务模块、业务实体表、显存管理、知识库与注入编辑 UI、工作流解析与出图闭环，均属后续阶段。

### 7.3 TASK-002.3（2026-11-17，0.1.0-dev.3，本机 Web 架构）

- **运行形态**：FastAPI 托管 `frontend/dist/` 静态产物并提供 `/api/*`；浏览器经同源相对路径访问，默认 `http://127.0.0.1:17890`（原 8000 与本机酒馆冲突，已改），只绑定 loopback；`WORKBENCH_HOST` / `WORKBENCH_PORT` 可配置，非 loopback 绑定在启动时被拒绝。
- **会话与安全**：`/api/bootstrap` 为同源回环浏览器签发 HttpOnly 会话票据与 CSRF 令牌；Cookie 会话的状态变更需 `X-Workbench-CSRF` 头；Origin 白名单校验；引导令牌仅环境变量传递，不入前端产物；保留原 API 认证，未关闭安全检查。
- **进程生命周期**：根目录 `启动工作台.bat` / `停止工作台.bat` 为普通用户入口；启动器复用项目 `.venv`，记录 PID 到 `.workbench.pid`（已忽略提交）；停止器按 PID 终止工作台进程树，不按进程名批量结束 Python。
- **依赖边界**：正式运行只需要项目 `.venv` 中的 Python 依赖；Node/Vite 仅在构建前端产物时使用，正式运行不启动 Node 开发服务器。Tauri 运行依赖（`@tauri-apps/api`、`@tauri-apps/cli`）已从 `package.json` 移除，Rust 工程归档至 `legacy/src-tauri/`。
- **数据与外部服务**：数据根后迁至仓库内 `userdata/`（见 7.4）；SQLite 与设置文件保留；Ollama `127.0.0.1:11434`、ComfyUI `127.0.0.1:8188` 仅做连通性检测，不自启/自关/重启。

### 7.4 TASK-002.3.1（2026-11-17，0.1.0-dev.4，安全加固与项目内数据目录）

- **数据目录**：默认根改为仓库内 `userdata/`（`WORKBENCH_DATA_ROOT` 可覆盖）；`backend/datadir.py` 统一路径、子目录、迁移备份与完整性校验；`userdata/` 被 Git 忽略。旧根 `%LOCALAPPDATA%\ComfyUI-Prompt-Workbench` 仅作迁移参照并保留，不再作为默认根。
- **安全边界**：启动器前置 loopback 校验；停止器按可执行路径 + 命令行 + 启动时间核验身份、fail-closed、递归回收进程树；静态路径用解码后真实路径包含判断；`/api/health` 为未认证身份探测，携带应用标识供启动器核验。
- **外部服务**：Ollama / ComfyUI 仍仅做连通性检测，不自启、不关闭、不重启；模型权重、原图、原始工作流留在 ComfyUI 外部目录，工作台只记录路径与索引。
