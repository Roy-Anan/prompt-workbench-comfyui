# comfyui_extension — 工作台融入 ComfyUI 的外部扩展

本目录把工作台的执行类功能做成 ComfyUI 自定义节点，管理类功能做成
ComfyUI 侧边栏面板。**所有代码与数据都留在本仓库，不复制进 ComfyUI
整合包目录**；整合包升级、换版本不会覆盖这里的节点和面板。

## 目录结构

```
comfyui_extension/
├─ extra_paths.yaml          # 给 ComfyUI 的外部节点路径配置
└─ workbench/                # 节点包（被 ComfyUI 扫描加载）
   ├─ __init__.py            # 注册 NODE_CLASS_MAPPINGS、WEB_DIRECTORY、代理路由
   ├─ backend_client.py      # 回环 HTTP 客户端（仅标准库，对接现有后端）
   ├─ proxy.py               # 本地只读代理：浏览器→ComfyUI 同源→后端（带节点令牌）
   ├─ rule_context_node.py   # 规则清单节点（按层级查全局规则）
   ├─ release_node.py        # 卸载 LLM / 释放显存节点
   ├─ candidate_node.py      # 本地 LLM 提示词候选生成节点
   └─ web/
      └─ workbench_panel.js  # 侧边栏页签 + 画布动态字段回填/本地记忆
```

## 挂载方式（不污染整合包）

扩展代码全部留在本仓库，不复制进整合包 `custom_nodes/`。ComfyUI 启动时
通过官方 `--extra-model-paths-config` 参数加载本目录的路径配置：

```
--extra-model-paths-config D:\AI\ComfyUI-Prompt-Workbench-Git\comfyui_extension\extra_paths.yaml
```

该 yaml 把 `workbench/` 包目录追加进 `custom_nodes` 扫描列表，与整合包
自带节点并存。三种等价启动方式（任选其一）：

1. **仓库根双击入口**：`启动 ComfyUI（工作台扩展）.bat`，内部调用
   `scripts/comfyui_launcher.py`，自动拼好上面的参数并前台启动；默认带
   `--auto-launch` 在服务起来后自动打开默认浏览器，设环境变量
   `COMFYUI_AUTO_LAUNCH=0` 可关闭；整合包根目录可用环境变量 `COMFYUI_ROOT`
   覆盖（默认指向本机秋叶整合包）。
2. **命令行**：用整合包 Python 启动 `ComfyUI/main.py`，附加
   `--extra-model-paths-config <本目录>/extra_paths.yaml`。
3. **绘世启动器 GUI**：在高级选项/启动参数里填入同一条
   `--extra-model-paths-config` 参数，再按平时方式启动。

> 退路：若启动器不支持额外参数，可在整合包 `custom_nodes/` 下建目录联接
> （junction）指向本包；链接可删，正本仍在仓库。优先用上面的参数方式。

**已真机验证**（秋叶整合包 ComfyUI v0.30.2，2026-11-20）：带参数启动后
日志出现 `Adding extra search path custom_nodes ...\comfyui_extension`，
工作台节点在 `/object_info` 注册成功（现状三个：`WorkbenchRuleContext`、
`WorkbenchReleaseVram`、`WorkbenchPromptCandidate`，中文名正确；早期的
`WorkbenchRuleQuery` 与 `WorkbenchWorkflowContext` 两个只读节点已合并进
`WorkbenchRuleContext`）；面板 JS 由
`/extensions/workbench/workbench_panel.js` 正常下发。

节点运行在整合包 Python 里，通过回环地址访问现有 FastAPI 后端
（默认 `http://127.0.0.1:17890`，可用 `WORKBENCH_API_URL` 覆盖）。

### 节点凭据（默认只读 + 窄白名单）

ComfyUI 节点进程看不到启动器内存里的 bootstrap 令牌。后端首次启动时
在数据根持久化一个节点专用令牌 `settings/node_token.json`（不入库）。
客户端按以下顺序取凭据：节点输入框 > 环境变量 `WORKBENCH_SESSION_TOKEN`
> 凭据文件。该令牌默认只放行 GET/HEAD/OPTIONS；少数幂等或只读派生的
本地受控动作在后端窄白名单内放行：`POST /api/system/release-vram` 卸载
LLM/释放显存，以及按工作流调用的 `POST /api/workflows/{id}/prompt/candidates`
本地候选生成（只回候选、不写回草稿/规则/设置）；其余写请求一律 403。
文件位置可用 `WORKBENCH_NODE_TOKEN_FILE` 或 `WORKBENCH_DATA_ROOT` 覆盖。

## 查重结论：复用现成插件 vs 自建

融入前先核对了 ComfyUI 生态已有能力，避免重复造轮子。

| 工作台功能 | 生态成熟方案 | 决策 |
| --- | --- | --- |
| LLM 提示词候选（本地 Ollama、五模式、规则联动、受保护片段/术语、候选审核清洗） | comfydv Chat Completion、Local LLM Prompt Enhancer、NeonLlama、Ollama Prompt Planner：通用 Ollama 对话/增强，无规则库、术语保护、候选审核 | **自建节点**，复用后端候选逻辑；底层 Ollama 调用可参考通用节点 |
| 规则引擎（规则库、解析比对、专用/通用套优先级、版本历史回退、与工作流比对） | 无对应插件 | **自建（后端保留）**，节点只做只读查询入口 |
| 草稿箱（按工作流多命名草稿、收藏置顶、标签、版本快照、回收站、跨流复制、写入校验） | PromptManager（SQLite 存提示词+标签+评分+去重）、Prompt-Assistant（历史/标签）：单条收藏，无工作流分组/版本快照/写入校验 | **自建（后端保留）**，可借鉴其 SQLite/标签思路，数据模型沿用现有 |
| 素材库（参考图导入复制/引用、分组标签、创作调用） | SmartGallery DAM、PixlStash、smart-comfyui-gallery：成品图管理/评分标签，定位不同 | **自建（后端保留）**；成品图历史交给内置 History / 画廊插件 |
| LoRA 触发词拉取与预览 | ComfyUI-Lora-Auto-Trigger-Words、LoRa Loader with TriggerDB、Civitai Toolkit、ComfyUI LoRA Manager | **优先复用现成插件**；手动资料、身份归并、触发词共享、组合冲突裁决仍自建后端 |
| 生图前卸载 LLM（Ollama 驻留显存释放）+ 显存闸门 | ComfyUI 内置 Dynamic VRAM / Unload-Model 节点只管自身扩散模型；外部 Ollama 卸载无节点覆盖 | **自建节点**，调后端现有卸载链路；ComfyUI 自身模型卸载用内置/现成节点 |
| 出图成品历史、画廊回看 | 内置 History + 上述画廊插件 | **复用**，不重复造 |
| ComfyUI 自身模型卸载/显存管理 | 内置 Dynamic VRAM、lowvram；SeanScripts ComfyUI-Unload-Model | **复用** |
| 任务队列/历史/快照 | 内置 History + 画廊覆盖成品回看；本地排队/快照为工作台自有 | 后端保留，画布模式下可弱化本地队列 |
| 工作流解析、Prompt 白名单确认、写回画布（画布桥） | 无 | **自建**（路线一后期阶段） |
| 设置/诊断/备份导出 | 无 | 后端保留，面板只读 |

## 建议安装的现成节点（复用侧）

工作台自建部分（候选生成、规则引擎、草稿箱、素材库、工作流解析）不依赖
下列通用插件；下表是查重后建议从生态复用的成熟节点，按优先级排列。本机
已装 ComfyUI-Manager，可在 Manager → Custom Nodes Manager 搜仓库名安装，
装完重启 ComfyUI。

| 优先级 | 插件 | 仓库 | 用途 | 备注 |
| --- | --- | --- | --- | --- |
| 高 ✅已装 | ComfyUI-Lora-Auto-Trigger-Words | `idrirap/ComfyUI-Lora-Auto-Trigger-Words` | 自动拉取 LoRA 触发词（Civitai API + safetensors 元数据），支持堆叠 LoRA | 2026-11-20 已 clone 到整合包 `custom_nodes`，仅依赖 `requests`（整合包已带）；查重首选复用，LoRA 手动资料/身份归并/触发词共享/组合冲突裁决仍走工作台后端 |
| 高 ✅已装 | ComfyUI-Unload-Model | `SeanScripts/ComfyUI-Unload-Model` | 卸载 ComfyUI 自身模型 / 全部模型释放显存 | 2026-11-20 已 clone；纯 Python/Torch 无额外依赖；只管 ComfyUI 模型，外部 Ollama 卸载仍用工作台自建节点 |
| 中 ✅已装 | ComfyUI-Lora-Manager | `willmiao/ComfyUI-Lora-Manager` | LoRA 可视化网格、预览缩略图、触发词、可收藏组合（recipes） | 2026-11-20 已 clone 到整合包 `custom_nodes`；补装缺失依赖 olefile/natsort/aiosqlite/brotli；真机验收 22 个 `(LoraManager)` 节点注册、前端 JS 下发 200、日志无 Traceback；LoRA 手动资料/身份归并/触发词共享/组合冲突裁决仍走工作台后端 |
| 可选 ⚠️跳过 | LoRa Loader with TriggerDB | `benstaniford/comfy-lora-loader-with-triggerdb` | 本地 JSON 持久化每个 LoRA 的触发词 | 上游仓库 GitHub 404、API 无公开匹配仓，判定已下架/私有，本机不可得，跳过；触发词能力由 Auto-Trigger-Words 与 FindingLora 覆盖 |
| 可选 ✅已装 | FindingLora | `shootthesound/comfyui-lora-FindingLora` | LoRA 书签 + 模糊搜索 + 触发词一键复制 | 2026-11-20 已 clone；纯标准库/Comfy 内部依赖、无第三方 requirements；真机验收 `LoraLoaderFindingLora` 节点注册、日志无 Traceback |

**不建议安装**（已被内置能力或工作台自建覆盖）：

- 通用 Ollama / LLM 对话与增强节点（comfydv、Local LLM Prompt Enhancer、
  NeonLlama、Ollama Prompt Planner 等）：候选生成走工作台后端五模式 + 规则
  联动 + 受保护片段/术语 + 候选清洗，不接通用对话节点。
- 通用提示词收藏/管理插件（PromptManager、Prompt-Assistant 等）：草稿箱
  由工作台后端自建（多命名、版本快照、收藏置顶、标签、写入校验）。
- 成品图画廊插件（SmartGallery DAM、PixlStash、smart-comfyui-gallery 等）：
  内置 History + 工作台素材库已覆盖；确有多图集中管理需求再评估。
- 模型动态卸载：ComfyUI 内置 Dynamic VRAM / lowvram 已具备。

## 落地顺序

1. **骨架 + 最简节点**（已落地）：规则查询节点，验证外部目录能被 ComfyUI
   识别加载；配套节点专用只读令牌机制（凭据文件 + 后端只读放行）。
2. **卸载 LLM / 释放显存节点**（已落地）：复用后端 `release-vram` 链路，
   节点令牌经窄白名单调用；返回卸载摘要与原始 JSON。
3. **侧边栏只读代理 + 分区摘要**（已落地）：扩展内 `proxy.py` 在 ComfyUI
   同源侧注册白名单只读路由 `/workbench-api/*`，由 Python 侧持节点令牌
   转发后端（绕过浏览器跨源无法读取令牌/cookie 的限制）；后端新增
   `GET /api/sidebar/summary` 六域脱敏聚合投影；面板改为代理优先、直连
   回退，分区折叠展示工作流（id/标题/确认态）/规则/草稿/素材/LoRA/历史摘要，
   超出条数引导回工作台。节点侧 `backend_client.list_workflows()` 复用
   `GET /api/workflows` 提供只读清单，为画布字段回填与记忆预留契约。
4. **规则清单节点 + 候选生成节点**（已落地前置切片）：
   `WorkbenchRuleContext` 不绑定工作流，只按层级（checkpoint / architecture /
   global）+ 可选 `scope_key` 浏览全局规则清单；`WorkbenchPromptCandidate`
   不绑定后端工作流，节点上直接三段点击选择——真实本地 Ollama `model`、LLM
   `persona` 人设、`prompt_profile` 模型提示词规则（`genre`/`style` 为写作类
   可选层）——调后端本地候选，候选只回画布、不写回草稿。输出分两套：
   `positive_text` / `negative_text` 带来源头信息（模式/模型/身份/Profile/题材/
   文风）供人核对，`clean_positive` / `clean_negative` 只带正文，可直接接
   CLIPTextEncode 或复制回工作台做安全写入。
5. **画布桥：动态下拉与记忆**（已落地）：候选节点的 `model`、`persona`、
   `prompt_profile`、`genre`、`style` 均为 combo 下拉——Python 侧用占位列表
   声明，真实选项来自工作台后端；节点以 `VALIDATE_INPUTS` 把这些字段排除出
   ComfyUI 的静态枚举校验（否则提交会报 `Value not in list`），扩展前端经代理
   分别拉 `/api/ollama/models`、`/api/prompt-profiles`、`/api/personas`、
   `/api/genres`、`/api/styles` 清单，用纯 value 填充下拉并记忆浏览器本地选择；
   新建节点优先沿用同画布已绑定节点的选择，其次回退本地记忆。节点不回写后端，
   草稿/规则/素材管理留 Web 主阵地。
6. 后续：候选节点与画布 CLIPTextEncode 的写入衔接深化；再评估服务层是否并入
   ComfyUI 自带服务。

### 代理边界与实测踩坑

- 代理只转发 `health` / `overview` / `sidebar-summary` / `workflows` /
  `ollama-models` / `profiles` 只读后缀；
  其他后缀返回 403；非安全方法（POST 等）返回 405；后端不可达时 502 JSON。
- 本机 aiohttp 的 `RouteTableDef` 没有 `add_route` 方法，注册必须用
  `routes.get(path)` 装饰器/回调式；GET 路由已隐含允许 HEAD，不可再显式
  注册 HEAD，否则启动即 `RuntimeError: method HEAD is already registered`。
- 2026-11-20 真机（ComfyUI v0.30.2）实测：白名单 200、
  非白名单 403、POST 405；工作台节点在 object_info 注册、面板 JS 正常
  下发、`workflows` 代理回传清单；启动日志无 Traceback。
