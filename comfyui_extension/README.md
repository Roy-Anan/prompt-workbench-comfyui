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
   ├─ rules_node.py          # 规则只读查询节点（首个落地节点）
   └─ web/                   # 侧边栏面板前端（占位，后续实现）
```

## 挂载方式（不污染整合包）

ComfyUI 启动时额外加载本目录的路径配置：

```
--extra-model-paths-config D:\AI\ComfyUI-Prompt-Workbench-Git\comfyui_extension\extra_paths.yaml
```

该 yaml 把 `workbench/` 包目录追加进 `custom_nodes` 扫描列表，与整合包
自带节点并存。若启动器界面不支持填额外参数，退路是在整合包
`custom_nodes/` 下建一个目录联接（junction）指向本包；链接可删，正本
仍在仓库。

节点运行在整合包 Python 里，通过回环地址访问现有 FastAPI 后端
（默认 `http://127.0.0.1:17890`，可用 `WORKBENCH_API_URL` 覆盖）。
鉴权走本地 Bearer 令牌（`WORKBENCH_SESSION_TOKEN`），与现有 CLI/脚本口径一致。

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

1. **骨架 + 最简节点**（当前）：规则查询节点，验证外部目录能被 ComfyUI
   识别加载。
2. 生图前卸载 LLM 节点，复用现有后端卸载链路。
3. LLM 候选节点，对接现有五模式与规则联动。
4. 侧边栏面板：草稿箱、素材库、LoRA 资料、规则编辑，对接后端数据库。
5. 画布桥：节点与画布节点读写、白名单确认；最后再评估服务层是否并入
   ComfyUI 自带服务。
