# 技术架构与模块边界

> 当前运行形态以 ADR-0002《本机浏览器 Web 运行时（免安装）》为准；ADR-0001 中的 Tauri/PyInstaller/NSIS 路线已废弃。下文若残留旧桌面宿主表述，以本节与 ADR-0002 为准。

## 1. 当前技术路线

  -----------------------------------------------------------------------------------------------
  层                      技术                    职责
  ----------------------- ----------------------- -----------------------------------------------
  运行入口                本机浏览器（Edge/Chrome） 访问 `http://127.0.0.1:17890`，同源使用前端页面与 API

  前端                    React + TypeScript +    中文 UI、Prompt 编辑器、候选/草稿/历史/LoRA
                          Vite（产物由 FastAPI 托管） 页面

  本地业务服务            Python + FastAPI        领域逻辑、工作流解析/验证、LLM/ComfyUI
                                                  适配、队列、数据访问、静态托管

  数据库                  SQLite                  本地结构化数据

  LLM                     Ollama 适配器（第一版） 发现模型、调用、停止、卸载、运行状态验证

  ComfyUI                 HTTP + WebSocket 适配器 工作流任务提交、事件、历史、队列状态与中断

  交付方式                免安装启动脚本          双击 `启动工作台.bat` 起服务并开浏览器；
                                                  `停止工作台.bat` 按身份回收进程树；不产出 EXE
  -----------------------------------------------------------------------------------------------

**技术状态：** 本机 `.venv` 实测 Python 3.13.18，测试在该版本通过；Python 3.12 兼容性尚未单独验证，不把未测试的版本号写成永久约束。

## 2. 选择理由

-   单用户本地工具，浏览器同源访问已足够，不需要独立桌面窗口外壳。
-   免安装方案绕开 Rust/MSVC 工具链与打包链，迭代链路短、维护成本低（见 ADR-0002）。
-   React/TypeScript 适合复杂 Prompt
    编辑器、结构化区块、差异对比和多页面状态管理。
-   Python 适合处理 JSON、HTTP/WebSocket、SQLite、本地模型服务和 ComfyUI
    集成。
-   将核心业务放在一个 Python 模块化单体中，避免把同一业务逻辑分散在
    TypeScript 和多个微服务。
-   后端是业务规则与安全校验的唯一权威层；前端只展示与发起命令。

## 3. 进程与通信拓扑

### 启动入口（启动脚本 + FastAPI 进程）

-   双击 `启动工作台.bat`，由 `scripts/workbench_launcher.py` 复用项目 `.venv` 启动 FastAPI 进程。
-   启动前做 loopback 校验、端口占用与实例身份校验；健康检查通过后打开默认浏览器。
-   `停止工作台.bat` 经 `workbench_stopper.py` 按 PID + 可执行路径/命令行/工作目录/实例标识/启动时间核验身份后回收本工作台进程树；不得关闭 ComfyUI 或 Ollama 用户服务。

### 前端（React）

-   只通过本地后端 API 调用业务能力。
-   不直接读取/写入工作流文件，不直接连接 ComfyUI，不直接控制 Ollama
    进程。
-   对重要操作显示确认、差异和状态；不能把前端状态当作安全校验依据。

### 本地业务服务（Python）

-   只绑定 127.0.0.1，不监听 0.0.0.0。
-   使用随机可用端口和每次启动随机会话凭据；限制
    CORS/Origin；拒绝无会话凭据请求。
-   对输入做 schema 校验；对所有外部文件和研究资料视为不可信数据。
-   统一管理 SQLite、工作流解析、规则合并、LLM 任务、ComfyUI
    任务队列和生成历史。

### 外部服务

-   ComfyUI：用户自行启动；工作台连接用户配置的地址。
-   Ollama：用户自行安装/运行；工作台通过本地 API
    调用，不擅自杀掉服务进程。
-   NVIDIA 驱动/显存状态：按能力读取，无法确认时安全失败。

## 4. 目标目录结构

    /
      README.md
      package.json
      pnpm-lock.yaml
      vite.config.ts
      tsconfig.json
      src/
        app/
        components/
        features/
          workflows/
          prompt-studio/
          drafts/
          models/
          loras/
          queue/
          history/
          settings/
        services/
        styles/
      backend/
        pyproject.toml
        app/
          api/
          core/
          domain/
          services/
          adapters/
            comfyui/
            ollama/
            gpu/
          repositories/
          workflow/
          rules/
          prompt/
          queue/
          history/
        tests/
      docs/
      scripts/
      .github/workflows/

以上是目标结构，不代表这些目录或代码已经创建。

## 5. Python 后端模块边界

-   api：HTTP 路由、请求/响应模型、错误码；不放业务规则。
-   core：配置、日志、应用生命周期、异常类型。
-   domain：核心实体和值对象。
-   workflow：UI/API workflow 解析、节点识别、Prompt
    白名单、快照与差异验证。
-   rules：模型身份、规则导入、三级继承、优先级、冲突检测和动态检索。
-   prompt：创作任务构造、候选校验、翻译、区块、语法和 Token 检查。
-   adapters/ollama：模型发现、生成、停止、卸载和状态验证。
-   adapters/comfyui：连接、HTTP API、历史/队列状态只读探测（R162 起任务提交、WebSocket 进度订阅与状态校准已退役）。
-   adapters/gpu：显存与进程状态采集；能力不足必须报告 unknown。
-   queue：R162 起本地排队层与调度已退役，`task_queue.py` 仅留表结构定义，运行时无 import；下列先进先出/暂停恢复/取消/重试/崩溃恢复为历史规划留档。
-   loras：元数据读取、角色卡、组合与工作流联动。
-   drafts：手动保存、版本历史、回收站和模型快照。
-   history：生成记录、文件路径状态、筛选和导出。
-   assets：本地图片素材库，分组/标签/检索、复制导入与路径引用（均支持批量）、受保护图片代理，以及在系统文件管理器中打开素材目录。
-   repositories：SQLite 数据访问和迁移。

### 5.1 模块拆分节奏

当前 M0/M1 阶段后端为平铺文件，允许在单文件内集中实现；进入后续阶段时按下列节奏拆分，避免在大文件中持续堆叠：

-   M2 收尾：将工作流解析、白名单/快照/校验从 `workflow_service.py` 拆到 `workflow/`（识别、白名单、快照差异）。
-   M3：新增 `rules/` 与 `repositories/`，模型身份与规则落 SQLite。
-   M4：新增 `prompt/` 与 `adapters/ollama/`。
-   M5/M7：新增 `drafts/`、`history/`。
-   M6：新增 `queue/` 与 `adapters/comfyui/`、`adapters/gpu/`。
-   拆分时保持业务规则只在 Python 后端一份；API 路由保持薄，数据访问集中到 repository。

### 5.2 存储边界

| 数据 | 存放位置 | 说明 |
| --- | --- | --- |
| 工作流原始文件、工作副本、版本快照、识别报告 | `userdata/workflows/<id>/` JSON 文件 | 原始文件不入库、不被覆盖；版本快照为写入前副本 |
| 模型身份、规则版本/来源、LoRA 卡、草稿与版本、队列、生成历史、设置、LLM 参数 | SQLite | 只存元数据、路径、索引与必要快照 |
| 图片原件、模型权重、LoRA 文件、ComfyUI 输出 | 用户原有目录，工作台不复制 | 数据库/数据目录只记录路径与缩略图索引 |
| 素材库图片副本 | `userdata/assets/copies/` | 仅「复制导入」落工作台副本；「路径引用」只记录外部绝对路径，不复制/移动/删除原图 |
| 注入角色/知识/规则文件 | `userdata/` 下配置目录（M3/M4 落地定格式） | 默认文件受保护，自定义副本可改可删 |

## 6. 核心安全管线

> R162 起：第 1–9 步（解析、白名单、规则匹配、候选围栏、确认写入、深拷贝、非白名单差异拒绝、回读校验）仍为现行口径；第 10–12 步（入队前 LLM/显存闸门、调用 ComfyUI 提交接口、WebSocket/HTTP 状态校准）已随代提交链路退役。

1.  读取原始 workflow，不修改源文件。
2.  将 UI workflow 解析/转换为内部只读模型；提交前使用 API workflow。
3.  为当前工作流建立内容指纹和 Prompt 节点白名单。
4.  规则引擎只根据当前工作流实际模型组合选择规则。
5.  Prompt 生成输出进入候选，做 schema、术语、语法、正负向和规则检查。
6.  用户审核候选并明确确认写入。
7.  基于原始 API workflow 深拷贝，只修改白名单路径。
8.  对比原始快照和新快照；任何非白名单差异即拒绝。
9.  再次读取/校验工作副本；确认 Prompt 值准确，其他字段不变。
10. 入队前执行 LLM unload + 状态验证 + GPU memory verification。
11. 全部通过后才调用 ComfyUI 提交接口。
12. WebSocket 事件与 HTTP
    状态接口共同确认任务状态；未知状态不推断成功/失败。

## 7. ComfyUI 集成

> R162（2026-11-22）口径：工作台不代提交出图。只把确认提示词写进工作副本，导出 API JSON 由用户拖入画布排队；画布原生出图经 `POST /api/history/sync` 手动拉 `/history` 反解补录，可自动归属或手动改挂。下列 HTTP 提交、WebSocket 监听、中断/重试为历史规划留档。

-   第一版只支持一个 ComfyUI 实例，但地址、端口可配置。
-   历史规划：使用 HTTP 接口提交 API workflow，接收 prompt_id。
-   历史规划：使用 WebSocket 监听执行事件；通过 queue/history/status 接口校准。
-   历史规划：支持断线重连、状态恢复、明确中断和手动重试。
-   ComfyUI 输出图片由 ComfyUI
    保存；工作台记录文件名、子目录、类型和解析后的路径。
-   输出目录从配置或 ComfyUI 返回数据确定，不写死某台电脑的路径。
-   工作台关闭不主动终止 ComfyUI 任务。

## 8. LLM 与显存策略

> R162 起：出图前强制 LLM 卸载/显存阈值闸门已退役，降级为只读状态展示 + 手动释放按钮；实际显存/LLM 占用由用户在画布出图前自行确认。下列 fail-closed 拦截与三态校验为历史规划留档。

-   通过统一 LLM Runtime 接口隔离 Ollama
    细节，未来可扩展其他本地运行时。
-   第一版只实现 Ollama adapter，不下载模型。
-   生成期间只允许一个 LLM 任务。
-   出图前调用运行时卸载模型，并验证模型不再处于 loaded/running 列表。
-   同时读取 GPU
    显存状态；如果显存释放状态无法判断或仍高于安全阈值，阻止提交并解释。
-   不以"请求已发送"作为卸载成功；必须有可观察的状态证据。
-   提交闸门在重新探测前 best-effort 执行释放：Ollama 用空 prompt 的 `/api/generate`（`keep_alive: 0`）逐个卸载驻留模型（Ollama 无 `/api/stop` HTTP 端点），ComfyUI `/free` 卸载已加载的底模/LoRA/CLIP/VAE；另提供手动释放端点与前端「卸载 LLM 和底模」按钮。逐目标失败只收集上报、不阻断提交，仍由下方显存空闲与 LLM 驻留闸门按可观察证据兜底拦截。
-   LLM 驻留校验分三态：进程未运行（连接被拒绝）视为无驻留、直接放行；服务在但响应解析异常/超时按未知 fail-closed 拦截；服务在且 `/api/ps` 仍列出模型则按驻留拦截。卸载后重新探测 `/api/ps` 复核，仍在列表的模型记入 `ollama_still_resident` 一并上报。
-   阈值和验证方式需在真实设备上测量，不能在未测试前硬编码为保证值。

## 9. 数据存储原则

SQLite 数据库建议包含： - model_identities / model_files - model_rules /
rule_versions / rule_sources - workflows / workflow_recognition -
lora_cards / lora_files / lora_tags / lora_groups / lora_combinations -
drafts / draft_versions / draft_tags - generation_jobs /
generated_images - queue_items - app_settings / llm_profiles -
app_events / diagnostics

图片原件、模型权重和原始工作流不放进数据库；数据库只保存必要元数据、路径、索引和快照。关键
JSON 快照应记录 schema_version。

## 10. 错误处理与恢复

-   外部服务连接失败、超时、解析失败、未知状态均使用明确错误类型。
-   任务状态至少区分
    queued、preparing、running、succeeded、failed、interrupted、unknown、cancelled。
-   已提交任务不能因 WebSocket 短暂断开直接标失败。
-   应用异常退出后恢复持久化队列记录；不自动重出。
-   草稿未保存内容不做自动备份；只保证最后一次用户主动保存的版本。
-   数据库迁移先备份/校验；迁移失败不得静默破坏旧数据。

## 11. 测试策略

-   单元测试：规则继承、工作流识别、白名单差异、Prompt
    校验、状态机、队列。
-   属性/模糊测试：未知节点和任意非白名单字段不得被改动。
-   集成测试：ComfyUI mock server、Ollama mock server、SQLite
    临时数据库。
-   安全测试：伪造节点、未知字段、Prompt
    节点误识别、工作流变化、写入中断、回滚失败、LLM 卸载失败。
-   本机运行测试：启动脚本起服务、端口与身份校验、停止脚本按进程树回收、数据目录权限。
-   硬件验收：在用户 RTX 4060 Laptop 8GB + 16GB RAM
    环境验证真实显存释放与队列行为。

## 12. 交付与运行

-   开发态：项目 `.venv` 运行 FastAPI；前端经 `tsc -b && vite build` 产出 `dist/`，由后端同源托管。
-   交付形态：免安装。双击 `启动工作台.bat` 起服务并打开浏览器，`停止工作台.bat` 回收进程树；不产出 EXE/NSIS 安装包（见 ADR-0002）。
-   不把 Ollama 模型、ComfyUI、Checkpoint、LoRA 或生成图片纳入工作台目录或备份导出。
-   正式运行只需要项目 `.venv` 的 Python 依赖；Node/Vite 仅用于构建前端产物。

## 13. 技术约束

-   模块化单体，不拆微服务。
-   Python 后端是业务逻辑唯一权威来源；前端只展示/发起命令。
-   SQLite schema 迁移必须可追踪。
-   外部导入的模型资料不能作为可执行指令。
-   对无法确认的模型/节点/显存/任务状态保持 unknown，安全失败。
