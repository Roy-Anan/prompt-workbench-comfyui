# PROJECT_CHANGELOG — 版本变更记录

> 沿革：本文件由仓库根 `PROJECT_CHANGELOG.md` 迁入 `docs/CHANGELOG.md`（2026-11-20）；下方历史条目保留原样，后续版本记录继续追加在本文件。

本文件记录项目每次交付的变更。每次后续交付都应按下方格式追加一条记录；不得把未完成事项写成已完成。

## 记录格式

每条记录包含以下字段：

- **版本号**：语义化版本（如 `0.1.0`）；基线/准备阶段可用 `0.0.x`。
- **日期**：YYYY-MM-DD。
- **任务编号**：对应的任务书编号（如 TASK-001）。
- **分支**：提交所在分支（如 `dev`）。
- **新增功能**：本次新增的能力或文件。
- **修改文件**：本次改动的文件路径清单。
- **修复问题**：本次修复的问题；无则写“无”。
- **测试情况**：已执行的检查/测试及其结果；未测试如实写“未测试”。
- **未完成事项**：遗留项与下一阶段待办。

## 变更记录

### 0.0.1 — 2026-11-17（TASK-001，分支 dev）

- **新增功能**：
  - 建立项目控制文件：总计划、项目状态、架构与模块边界、变更记录、注入系统规范。
  - 从最新 main 创建并推送 `dev` 开发分支。
  - 归档旧项目素材保留在 `legacy-materials/`（前序提交完成，本次未改动）。
- **修改文件**：
  - `PROJECT_MASTER_PLAN.md`（新建）
  - `PROJECT_STATE.md`（新建）
  - `PROJECT_ARCHITECTURE.md`（新建）
  - `PROJECT_CHANGELOG.md`（新建，本文件）
  - `docs/PROJECT_CONTROL/INJECTION_SYSTEM_SPEC.md`（新建）
- **修复问题**：无。
- **测试情况**：
  - 远程仓库状态核对：main 最新提交 `3084866aa85595133c7a1179b15ddd71e659d8f3`；8 份基线文档与 `legacy-materials/` 在位。
  - dev 分支与 origin/dev 同步；本地工作区干净。
  - 未执行业务功能测试（尚无业务代码）。
- **未完成事项**：
  - M0 工程骨架（Tauri + React + TS + Vite + Python sidecar）尚未搭建。
  - 旧素材逐文件审查尚未完成。
  - 真机端到端验收尚未进行。

### 0.1.0-dev.1 — 2026-11-17（TASK-002，分支 dev）

- **新增功能**：
  - Python FastAPI 本地服务：会话令牌校验、设置读取/保存、存储路径校验、Ollama / ComfyUI 连接检测、管理目录初始化。
  - React + TypeScript + Vite 前端：服务健康、Ollama / ComfyUI 状态、存储位置与服务连接设置界面。
  - Tauri 2 桌面宿主骨架：`get_backend_config` 命令、随机本地端口、UUID v4 令牌、拉起自己的 Python 子进程、退出时结束该子进程（Rust 代码已落地，未在本机编译运行）。
  - 根 `.gitignore`：忽略 `.venv/`、`node_modules/`、`dist/`、Rust `target/`、`gen/`、本地日志与用户数据。
- **修改文件**：
  - 新增 `backend/requirements.txt`、`backend/main.py`、`backend/test_main.py`。
  - 新增 `frontend/package.json`、`frontend/tsconfig.json`、`frontend/tsconfig.app.json`、`frontend/tsconfig.node.json`、`frontend/vite.config.ts`、`frontend/index.html`、`frontend/src/main.tsx`、`frontend/src/App.tsx`、`frontend/src/styles.css`。
  - 新增 `frontend/src-tauri/Cargo.toml`、`frontend/src-tauri/build.rs`、`frontend/src-tauri/tauri.conf.json`、`frontend/src-tauri/capabilities/default.json`、`frontend/src-tauri/src/main.rs`、`frontend/src-tauri/src/lib.rs`。
  - 更新 `PROJECT_STATE.md`、`PROJECT_CHANGELOG.md`（本文件）。
  - 未改动 `legacy-materials/` 与既有基线文档。
- **修复问题**：无（本次未自行改写业务逻辑；后端在本机环境一次跑通，未做依赖或架构调整）。
- **测试情况**：
  - Python 语法检查：通过（`py_compile` 退出码 0）。
  - Python pytest：通过，5 passed（Python 3.13.18 / pytest 8.4.2）。
  - 后端真实 HTTP 冒烟：无/错令牌 401；正确令牌健康检查 200；设置与离线检测 200；管理目录自动创建。
  - 前端 TypeScript 检查：通过（`tsc -b` 退出码 0）。
  - 前端 Vite 构建：通过（`vite build`，33 模块）。
  - Vite dev 独立启动：127.0.0.1:1420 返回 200。
  - Rust `cargo check`：**未执行成功**，本机未安装 Rust 工具链与 MSVC C++ Build Tools。
  - 桌面窗口、sidecar 自动拉起、端口/令牌握手、退出清理：**未验证**（依赖 Rust 编译与 Tauri 运行时）。
- **未完成事项**：
  - 安装 Rust 工具链 + MSVC C++ Build Tools 后完成 Tauri 桌面闭环真机验收。
  - 固定 Python 3.12 环境并复核兼容性（本机当前为 3.13.18，无 3.12）。
  - WebView2 运行时在 Tauri 壳内的可用性待实测。
  - 业务功能（编辑器、知识库、角色管理、工作流解析、显存调度、ComfyUI 启停、安装包等）按任务书边界本次未实现。

### 0.1.0-dev.2 — 2026-11-17（TASK-002.1，分支 dev）

- **新增功能**：
  - SQLite 基础数据层：`storage.database/workbench.sqlite3`，含 `schema_version`、`app_metadata` 两表；重复初始化保留版本号。
  - 设置存储加固：10 个管理目录与外部模型路径白名单；root 由宿主固定、客户端修改返回 400；未知存储/连接键返回 400；外部模型路径留空允许、填写时必须存在且为目录。
  - 损坏配置恢复：`settings.json` 解析失败时备份为 `settings.corrupt-<时间戳>-<hex>.json` 后重建默认配置。
  - CORS 白名单：仅放行 `tauri://localhost`、`https://tauri.localhost`、`http://tauri.localhost`、`http://localhost:1420`、`http://127.0.0.1:1420`；未授权 Origin 不带 allow-origin。
  - 前端：root 输入框只读、保存前剔除只读 root；新增 `/api/storage/validate` 路径存在/目录/可写检查；合并路径行、连接列表、操作区与响应式样式。
  - Rust 宿主局部加固：数据根统一为 `%LOCALAPPDATA%\ComfyUI-Prompt-Workbench`（与后端默认根一致）；子进程 stdout/stderr 分流到 `logs/sidecar-stdout.log`、`logs/sidecar-stderr.log`；健康检查在 TCP 连通后再校验带令牌的 `/api/health`；启动失败/超时错误附带 stderr 日志路径；令牌仅经环境变量传递、不写入日志。
- **修改文件**：
  - 完整替换 `backend/main.py`、`backend/test_main.py`、`frontend/src/App.tsx`。
  - 局部修改 `frontend/src-tauri/src/lib.rs`；合并 `frontend/src/styles.css` 新样式。
  - 更新 `PROJECT_STATE.md`、`PROJECT_CHANGELOG.md`（本文件）、`PROJECT_ARCHITECTURE.md`。
  - 未改动 `legacy-materials/`、既有 8 份基线文档与 `docs/ROADMAP.md` / `PROJECT_MASTER_PLAN.md`（阶段编号 M0–M10 已核对一致，无冲突）。
- **修复问题**：
  - 修复旧版 Rust 用 `app_local_data_dir()/data`、Python 用另一目录的数据根不一致问题。
  - 修复旧版就绪判定只靠 TCP 端口打开、未校验带令牌健康端点的问题。
  - 修复损坏 `settings.json` 直接失败、无备份的问题。
- **测试情况**：
  - Python 语法检查：通过（`py_compile` 退出码 0）。
  - Python pytest：通过，13 passed，6.19s（Python 3.13.18 / pytest 8.4.2，1 条 StarletteDeprecationWarning）。
  - 后端真实 HTTP 冒烟：无/错令牌 401；正确令牌 200；CORS 白名单预检 200 带 allow-origin、未授权 Origin 400 无 allow-origin；SQLite 真实创建、两表、schema_version=1、重复初始化版本保留；root 修改 400；未知键 400；外部路径可留空；损坏配置备份并恢复；离线服务不崩溃。
  - 前端 TypeScript 检查：通过（`tsc -b` 退出码 0）。
  - 前端 Vite 构建：通过（`vite build`，33 模块，659ms）。
  - Rust `cargo check`：**未执行成功**，本机未安装 Rust 工具链与 MSVC C++ Build Tools。
  - 桌面窗口、sidecar 自动拉起、端口/令牌握手、退出清理：**未验证**（依赖 Rust 编译与 Tauri 运行时）。
- **未完成事项**：
  - 安装 Rust 工具链 + MSVC C++ Build Tools 后完成 Tauri 桌面闭环真机验收。
  - 固定 Python 3.12 环境并复核兼容性（本机当前为 3.13.18，无 3.12）。
  - WebView2 在 Tauri 壳内的可用性待实测。
  - 业务功能（编辑器、知识库、角色管理、工作流解析、显存调度、ComfyUI 启停、安装包等）按任务书边界本次未实现。

### 0.1.0-dev.3 — 2026-11-17（TASK-002.2，分支 dev）

- **新增功能**：无业务代码；完成只读 Windows 运行环境盘点、运行依赖与安装包方案整理、现状代码审查与证据归档。
- **修改文件**：
  - 新增 `docs/TASK_EVIDENCE/TASK-002.2/`（README、environment-report、runtime-dependency-report、installer-design-report、current-code-review、disk-space-report、known-issues、raw-outputs/）。
  - 更新 `PROJECT_STATE.md`（追加 TASK-002.2 盘点记录）、`PROJECT_CHANGELOG.md`（本文件）。
- **修复问题**：无（本任务不修改业务代码，不做重构）。
- **测试情况**：
  - 只读采集 OS/架构/管理员权限/磁盘/WebView2/Edge/VC++/Python/.venv/依赖清单/产物存在性；原始输出归档 `raw-outputs/`。
  - 未安装 Rust、MSVC、Node.js、Python 3.12；未修改系统 PATH/注册表/环境变量/防火墙/杀毒设置；未启停 ComfyUI/Ollama。
  - 未执行 `cargo check`、`tauri build`、PyInstaller 打包或桌面端到端运行。
- **未完成事项**：
  - 打包方案（PyInstaller sidecar、Tauri externalBin、NSIS、版本对齐、CSP、依赖拆分）由 ChatGPT 后续设计。
  - 打包环境（Rust/MSVC）与干净 Win11 x64 安装验收待后续任务。
  - 本任务不宣布 M0 桌面闭环通过，也不宣布正式 EXE 已完成。

### 0.1.0-dev.3 — 2026-11-17（TASK-002.3，分支 dev）

- **新增功能**：
  - 本机 Web 运行模式：FastAPI 同源托管 `frontend/dist/` 静态产物与 `/api/*`；SPA 深层路由回退 `index.html`；未知 API 返回 JSON 404；`dist/` 缺失返回 503；静态资源与 SPA 壳免 API 认证。
  - Web 会话与安全：`/api/bootstrap` 为同源回环浏览器签发 HttpOnly 会话票据与 CSRF 令牌；Cookie 会话状态变更需 `X-Workbench-CSRF` 头；Origin 白名单校验；引导令牌仅环境变量传递，不入前端构建。
  - 一键启停入口：根目录 `启动工作台.bat` / `停止工作台.bat`；`scripts/workbench_launcher.py`（复用 `.venv`、端口/重复实例检查、健康检查后开浏览器）；`scripts/workbench_stopper.py`（按 PID 终止工作台进程树，不按名杀 Python）。
  - 前端同源 API 客户端 `frontend/src/api.ts`；前端去除 Tauri `invoke` 与写死端口/令牌。
  - 依赖拆分：`backend/requirements-dev.txt` 单独承载 pytest；运行清单不再含测试依赖。
  - 历史 Tauri 工程归档至 `legacy/src-tauri/`，附 `legacy/README.md` 说明其不属于 Web 运行时。
- **修改文件**：
  - `backend/main.py`（静态托管、会话票据/CSRF/Origin、loopback 绑定校验、版本 `0.1.0-dev.3`）。
  - `backend/test_main.py`（新增 bootstrap 票据、CSRF、Origin 拒绝、API 404 JSON、SPA 回退、同源/外部 Origin bootstrap 用例，共 20 项）。
  - `backend/requirements.txt`、`backend/requirements-dev.txt`。
  - `frontend/src/App.tsx`、`frontend/src/api.ts`（新增）、`frontend/package.json`、`frontend/package-lock.json`。
  - `.gitignore`（Tauri 归档路径、`.workbench.pid` 运行时文件）。
  - 控制文档：`PROJECT_MASTER_PLAN.md`、`PROJECT_STATE.md`、`PROJECT_ARCHITECTURE.md`、`PROJECT_CHANGELOG.md`（本文件）。
  - 证据目录：`docs/TASK_EVIDENCE/TASK-002.3/`。
  - 未改动 `legacy-materials/`、既有 8 份基线文档与用户数据。
- **修复问题**：
  - 启动器健康检查把 401 误判为未就绪：改为只要收到 HTTP 响应即视为服务已起。
  - 启动器脱离运行后清理 PID 文件导致停止脚本找不到后端：改为分离进程并持久保留后端 PID。
  - 停止器依赖已移除的 `wmic`：改用 CIM 查询子进程；仅终止记录 PID 及其子进程。
  - 修复 `urllib.request.Request` 与 FastAPI `Request` 命名冲突导致的探测异常。
- **测试情况**：
  - Python pytest：通过，20 passed（Python 3.13.18 / pytest 8.4.2，1 条 StarletteDeprecationWarning）。
  - TypeScript 检查：通过（`tsc -b` 退出码 0）。
  - Vite 生产构建：通过（32 模块，产物 `dist/`）。
  - 真实浏览器验收（Edge，127.0.0.1:8000）：页面已连接（Python 3.13.18），设置/存储/连接数据渲染正常，刷新后 SPA 回退与会话保持正常。
  - 生命周期：重复启动识别已有实例不冲突；停止后端口释放、PID 清除；再次启动成功；监听仅 `127.0.0.1:8000`。
  - Ollama / ComfyUI 离线：返回 `online:false`，服务 200 不崩溃。
- **未完成事项**：
  - 业务功能（编辑器、知识库、规则引擎、工作流解析、队列、显存调度、出图闭环）按边界本次未实现。
  - Python 3.12 兼容性未单独验证（本机 3.13.18）。
  - Ollama / ComfyUI 在线状态下的真实调用验收待后续阶段。
  - 本任务不宣布 EXE/安装包完成，不恢复 Tauri 桌面路线。

### 0.1.0-dev.4 — 2026-11-17（TASK-002.3.1，分支 dev）

- **新增功能**：
  - 项目内数据目录：默认根改为仓库内 `userdata/`（`WORKBENCH_DATA_ROOT` 可覆盖）；新增 `backend/datadir.py` 统一路径服务、管理子目录规划、迁移备份与 SQLite 完整性校验。
  - 启动器 loopback 前置校验；健康检查按应用标识核验实例身份；端口占用区分本工作台与其他服务。
  - 停止器 CIM 多重身份核验（可执行路径 + 命令行 + 启动时间）、递归进程树回收、身份不匹配 fail-closed。
  - 静态路径真实路径包含判断（先解码 URL 编码），拒绝目录穿越与相邻前缀碰撞。
  - `/api/health` 作为未认证身份探测，响应体携带 `app` / `app_id` / `version`。
- **修改文件**：
  - 新增 `backend/datadir.py`、`backend/test_scripts.py`。
  - 修改 `backend/main.py`（接入 datadir、loopback 校验、路径解析、健康标识、中间件放行健康探测、版本 `0.1.0-dev.4`）。
  - 修改 `backend/test_main.py`（目录/迁移/路径安全/loopback/健康身份用例，共 36 项）。
  - 修改 `scripts/workbench_launcher.py`、`scripts/workbench_stopper.py`。
  - 修改 `.gitignore`（新增 `/userdata/`、`*.tmp`）。
  - 控制文档：`PROJECT_STATE.md`、`PROJECT_ARCHITECTURE.md`、`PROJECT_MASTER_PLAN.md`、`PROJECT_CHANGELOG.md`（本文件）。
  - 证据目录：`docs/TASK_EVIDENCE/TASK-002.3.1/`。
  - 未改动 `legacy-materials/`、既有 8 份基线文档与用户数据。
- **修复问题**：
  - 健康检查被认证中间件拦截返回 401，启动器无法识别本工作台实例。
  - 停止器仅凭 PID 存在复用误杀风险。
  - 静态托管字符串前缀匹配存在相邻目录碰撞与穿越风险。
  - 数据根落在用户漫游目录、与源码隔离口径不统一。
- **测试情况**：
  - Python pytest：通过，36 passed（Python 3.13.18 / pytest 8.4.2，1 条 StarletteDeprecationWarning）。
  - TypeScript 检查：通过（`tsc -b` 退出码 0）。
  - Vite 生产构建：通过（32 模块，产物 `dist/`）。
  - 真机迁移：旧库完整性 ok，迁入 `userdata/` 并备份；旧目录保留；新库完整性 ok。
  - 真机浏览器：Edge 连接成功，存储根显示项目内 `userdata`；仅监听 `127.0.0.1:8000`。
- **未完成事项**：
  - Python 3.12 兼容性未单独验证（本机 3.13.18）。
  - Ollama / ComfyUI 在线真实调用与出图闭环待后续阶段验收。
  - 业务功能按边界本次未实现；不恢复 Tauri/Rust/EXE 路线。

### 0.1.0-dev.5 — 2026-11-17（TASK-002.3.2，分支 dev）

- **新增功能**：
  - 结构化运行记录：启动器保存 PID、ISO8601 启动时间、可执行路径、工作目录、命令行、随机实例标识；停止器逐项比对身份，记录损坏/缺失或任一不匹配 fail-closed，不杀进程。
  - 实例标识经本机 /api/health 回传比对；正常停止后清理运行记录与 PID 文件。
  - 旧设置单独迁移通道：保留连接地址、Checkpoint/LoRA 路径及其他偏好；新设置已存在时安全合并、冲突保留已有用户值；迁移前备份；未知字段保留在 _legacy_extra；管理路径按新 userdata 重建。
- **修改文件**：
  - scripts/workbench_launcher.py、scripts/workbench_stopper.py。
  - ackend/datadir.py（旧设置读取/备份/映射/安全合并）。
  - ackend/main.py（实例标识注入与健康检查回传、_legacy_extra 保留）。
  - ackend/test_scripts.py、ackend/test_main.py（身份不匹配/时间/路径/命令行/记录损坏/实例标识/设置迁移/合并/备份用例）。
  - .gitignore（忽略 .workbench-runtime.json）。
  - 控制文档：PROJECT_STATE.md、PROJECT_CHANGELOG.md（本文件）。
  - 证据目录：docs/TASK_EVIDENCE/TASK-002.3.2/。
  - 未改动 legacy-materials/、既有 8 份基线文档与用户数据；未重迁数据库，未改 main。
- **修复问题**：
  - 停止器仅凭 PID 存在，存在 PID 被复用后误杀其他程序的风险。
  - 旧版 AppData settings.json 未被新版加载，旧配置可能丢失。
- **测试情况**：
  - Python pytest：通过，48 passed（Python 3.13.18 / pytest 8.4.2，1 条 StarletteDeprecationWarning）。
  - 本次改动不涉及前端源码，未重复执行 tsc/vite 构建。
  - 真机：启动就绪（PID 7908），健康检查实例标识与记录一致，身份核验通过；正常停止后进程树回收、8000 端口释放、记录清理。
  - 隔离复现：旧设置迁移、备份、字段保留与冲突合并不覆盖均通过；旧库保留未重迁。
- **未完成事项**：
  - Python 3.12 兼容性未单独验证（本机 3.13.18）。
  - Ollama / ComfyUI 在线真实调用与出图闭环待后续阶段验收。
  - 业务功能按边界本次未实现；不恢复 Tauri/Rust/EXE 路线。

### 0.1.0-dev.5 — 2026-11-17（TASK-003.1，分支 dev，**未提交 / 未验收**）

> 状态：代码已按交付落盘，但后端新增 API workflow 测试 6 项失败；按任务书要求未提交、未推送 dev，真机闭环验收未执行。本条目为工作区留痕，对应提交 SHA 待复测通过后补记。

- **新增功能（代码已落盘，未验收）**：
  - 工作流导入/解析/Prompt 白名单写入：`backend/workflow_service.py`（格式识别、节点解析、指纹、白名单、安全写入）、`backend/workflow_api.py`（`/api/workflows` 路由）、`backend/test_workflow_service.py`、`frontend/src/WorkflowWorkbench.tsx`。
- **修改文件（工作区未提交）**：
  - `backend/main.py`（接入 workflow_router、版本号 `0.1.0-dev.5`）。
  - `frontend/src/App.tsx`（工作流页面导航与切换）。
  - `frontend/src/styles.css`（工作流页面样式）。
  - 证据目录：`docs/TASK_EVIDENCE/TASK-003.1/`（README、Doubao-Receipt、known-issues、pytest-output、frontend-build）。
- **修复问题**：无（本次未改核心实现；落盘时一处 JSX 片段闭合偏差已按源码原文修正）。
- **测试情况**：
  - Python pytest：**51 passed，6 failed**（57 collected，Python 3.13.18 / pytest 8.4.2）；既有 `test_main.py` 32、`test_scripts.py` 16 全过；新增 `test_workflow_service.py` 3 passed / 6 failed。
  - Python 编译检查：通过（`py_compile` exit 0）。
  - 前端构建：通过（`tsc -b && vite build` exit 0，33 模块）。
  - 真机浏览器验收：未执行（后端解析链路未通过，闭环无法走通）。
- **未完成事项 / 阻塞**：
  - P1（阻塞）：后端解析器从 `workflow["nodes"]` 取 API 节点，与测试样本的扁平 API prompt 结构口径不一致，6 个 API 用例在格式识别阶段被拒；待 ChatGPT 侧确认口径（解析器适配扁平结构 / 样本改为 nodes 包裹）后复测。
  - 白名单写入、非白名单拒绝、指纹校验、重启持久化、UI workflow 浏览器只读验收均未走到，不能视为已验证。
  - Python 3.12 兼容性未验证；Ollama / ComfyUI 在线真实调用与出图闭环不在本次边界。
- **归档规则新增**：证据目录统一 `docs/TASK_EVIDENCE/TASK-xxx/`，含 `Doubao-Receipt.md` 原始回执与佐证材料，随代码提交 dev；规则已写入 `docs/MAINTENANCE_AND_AGENT_HANDOFF.md` 第 5.1 节。
