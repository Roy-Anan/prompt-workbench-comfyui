# comfyui_extension — 工作台融入 ComfyUI 的外部扩展

本目录把工作台的执行类功能做成 ComfyUI 自定义节点，管理类功能做成
ComfyUI 侧边栏面板。**所有代码与数据都留在本仓库，不复制进 ComfyUI
整合包目录**；整合包升级、换版本不会覆盖这里的节点和面板。

## 目录结构

```
comfyui_extension/
├─ extra_paths.yaml          # 给 ComfyUI 的外部节点路径配置
└─ workbench/                # 节点包（被 ComfyUI 扫描加载）
   ├─ __init__.py            # 注册 NODE_CLASS_MAPPINGS、WEB_DIRECTORY
   ├─ backend_client.py      # 回环 HTTP 客户端（仅标准库，对接现有后端）
   ├─ rules_node.py          # 规则只读查询节点
   ├─ release_node.py        # 卸载 LLM / 释放显存节点
   └─ web/
      └─ workbench_panel.js  # 侧边栏页签（只读：健康探针 + 概览计数 + 跳转工作台）
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
   `scripts/comfyui_launcher.py`，自动拼好上面的参数并前台启动；整合包
   根目录可用环境变量 `COMFYUI_ROOT` 覆盖（默认指向本机秋叶整合包）。
2. **命令行**：用整合包 Python 启动 `ComfyUI/main.py`，附加
   `--extra-model-paths-config <本目录>/extra_paths.yaml`。
3. **绘世启动器 GUI**：在高级选项/启动参数里填入同一条
   `--extra-model-paths-config` 参数，再按平时方式启动。

> 退路：若启动器不支持额外参数，可在整合包 `custom_nodes/` 下建目录联接
> （junction）指向本包；链接可删，正本仍在仓库。优先用上面的参数方式。

**已真机验证**（秋叶整合包 ComfyUI v0.30.2，2026-11-20）：带参数启动后
日志出现 `Adding extra search path custom_nodes ...\comfyui_extension`，
两个节点在 `/object_info` 注册成功（`WorkbenchRuleQuery` /
`WorkbenchReleaseVram`，中文名正确）；面板 JS 由
`/extensions/workbench/workbench_panel.js` 正常下发。

节点运行在整合包 Python 里，通过回环地址访问现有 FastAPI 后端
（默认 `http://127.0.0.1:17890`，可用 `WORKBENCH_API_URL` 覆盖）。

### 节点凭据（默认只读 + 窄白名单）

ComfyUI 节点进程看不到启动器内存里的 bootstrap 令牌。后端首次启动时
在数据根持久化一个节点专用令牌 `settings/node_token.json`（不入库）。
客户端按以下顺序取凭据：节点输入框 > 环境变量 `WORKBENCH_SESSION_TOKEN`
> 凭据文件。该令牌默认只放行 GET/HEAD/OPTIONS；仅少数幂等的本地受控动作
（当前为 `POST /api/system/release-vram` 卸载 LLM/释放显存）在后端白名单内
放行，其余写请求一律 403。文件位置可用 `WORKBENCH_NODE_TOKEN_FILE` 或
`WORKBENCH_DATA_ROOT` 覆盖。

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

## 落地顺序

1. **骨架 + 最简节点**（已落地）：规则查询节点，验证外部目录能被 ComfyUI
   识别加载；配套节点专用只读令牌机制（凭据文件 + 后端只读放行）。
2. **卸载 LLM / 释放显存节点**（已落地）：复用后端 `release-vram` 链路，
   节点令牌经窄白名单调用；返回卸载摘要与原始 JSON。
3. LLM 候选节点，对接现有五模式与规则联动（依赖画布桥的工作流上下文，
   排到画布桥阶段）。
4. **侧边栏面板**（已落地只读）：注册工作台页签，探针后端连通状态、
   拉取 `/api/overview` 展示规则/工作流/草稿/素材/历史计数，并跳转完整
   工作台；草稿/素材/LoRA/规则编辑仍在工作台页面，待后续接入只读
   浏览器会话后再在页签内展示明细列表。
5. 画布桥：节点与画布节点读写、白名单确认；最后再评估服务层是否并入
   ComfyUI 自带服务。
