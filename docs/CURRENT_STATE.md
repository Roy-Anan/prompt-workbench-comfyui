# CURRENT_STATE — 项目当前有效状态

- 更新日期：2026-11-20
- 口径：如实记录，计划中功能不计为已完成；仅代码未验证的项明确标注“未验证”。历史流水账见 `docs/archive/STATE_HISTORY.md`，各模块落地切片与剩余方向以 `docs/ROADMAP.md` 为准。

## 1. 当前阶段

- 定位：本机 Windows 单机单用户的 ComfyUI 提示词创作与管理工作台，免安装浏览器 Web 应用；FastAPI 同源托管前端产物与 API，本地 SQLite + `userdata/` 文件。不做账号/团队/云端同步/素材社区，不产出 EXE/NSIS 安装包（ADR-0002）。
- 阶段：**主体模块落地，辅助切片收尾**。工作流解析与白名单、规则库与参数比对、本地 LLM 候选审核、任务队列与显存闸门、生成历史、多命名草稿与收藏、只读诊断/设置/备份导出、素材库、ComfyUI 外部扩展画布桥均已落地；详见 `docs/ROADMAP.md` 当前剩余方向段。
- 开发分支：`dev`（日常开发），`main` 保持稳定快照不动。
- 运行地址：后端工作台 `http://127.0.0.1:17890`（仅绑定回环）；ComfyUI `127.0.0.1:8188`；Ollama `127.0.0.1:11434`。ComfyUI、Ollama 由本机自行启停，工作台经 HTTP 连接。

## 2. 已落地并经回归验证的能力（概览）

- **工作流与规则（M1–M3）**：UI/API 工作流只读解析与 Prompt 白名单写入；模型身份库（按需 SHA256、多副本归并）；规则三级继承（checkpoint > 架构 > 全局，专用套优先于通用套）、冲突/未知保守标注、档案候选审核导入、结构化参数比对与前端只读展示。
- **本地 LLM 候选（M4）**：Ollama 模型只读发现；候选生成走本地模型、不切云端；多创作模式（起草/润色/精简/中英对照/负向补全）；参数白名单透传、受保护术语、请求级锁定片段、候选围栏清洗与空结果 fail-closed；会话内多候选与并排对比。
- **三段注入与两类身份（R140，2026-11-20）**：同一本地 LLM 可切换提示词类 / 写作类身份，各槽位可留空，全空即裸模型直连（不发 system 消息）。后端 `personas/genres/styles` 文件注册表与 `/api/personas`、`/api/genres`、`/api/styles` 只读列表；候选请求含 `persona`/`genre`/`style`/`positive_in`/`negative_in`，规则套三态（auto / 指定 / none）。提示词类走正负双框 + 规则套 + 负向模式，写作类走单框 + 题材/文风。画布节点共用一个，固定 `positive_text`/`negative_text`/`raw_json` 三口，`workflow_id` 可选走独立轮次。规范见 `docs/PROJECT_CONTROL/THREE_SLICE_SPEC.md`。
- **队列与显存（M6）**：ComfyUI 设备/显存/队列只读探测；任务提交 fail-closed 闸门；取消/中断/重试；本地排队层暂停/恢复/调序；WebSocket 实时进度与断线重连；LLM 与 ComfyUI 互斥反向闸门；出图前 5 GiB 空闲显存阈值闸门；排队项 Prompt 快照冻结。
- **生成历史（M7）**：Prompt/模型/采样参数快照；图片存在性标注与只读代理缩略图；列表/网格/时间轴三视图；筛选/导出；历史反向创建草稿；大图灯箱与并排对比。
- **草稿与版本（M5）**：多份独立命名草稿箱；收藏/标签/置顶/搜索/回收站/跨流复制/环境快照/双向索引；版本快照只读列表、恢复与片段级 diff。
- **LoRA 资料（M8）**：同名 json 元数据只读扫描与完整度标注；手动资料优先；触发词标签与组合只读建议；手动资料前端编辑；身份归并/同底座搭配/共享触发词只读提示；身份哈希缓存（checkpoint 排除）。身份归并与角色卡落盘维持只读。
- **数据维护（M9）**：整库备份只读导出与备份恢复写入（含完整性校验）；诊断只读巡检（损坏 JSON/孤儿文件/DB integrity）；设置白名单导入导出。数据库结构与损坏数据不做自动迁移/自动改写修复。
- **UI 与交付（M10）**：深浅主题（跟随系统）、字号三档、布局与筛选偏好本地记忆；中文优先界面；素材库分组/标签/检索与两种导入。

## 3. 未验证 / 遗留项

- **R140 真机验收已通过**：本地 LLM 候选全链路（提示词类/写作类/裸模型三种身份）、画布新节点下拉与三口连线、题材/文风注入效果已在真机跑通核对；前端 tsc/build 与后端单测/扩展测试回归兜底。
- **工作台前端 standalone 入口已放开（2026-11-20）**：新增独立「写作助手」页签，不绑定工作流即可选身份/题材/文风/模式直接生成，打后端独立轮次路由 `POST /api/workflows/prompt/candidates`（无 `workflow_id`）；候选仅在本页使用，点「存入历史」才归档到写作历史，不写入工作副本与草稿。
- **身份按页签分割（2026-11-20）**：工作流工作区固定提示词助手身份（`prompt_assistant`），不提供身份下拉、不读身份记忆，保留规则套与负向模式；写作助手页签身份下拉仅列裸模型（`none`）与写作助手（`writing_assistant`），去掉规则套下拉，题材/文风恒显，模式不含负向词。
- **图片识别（工作台内，2026-11-20）**：新增「图片识别」页签，后端 `POST /api/vision/describe` 调本机 Ollama VL 模型描述图片（最多 2 张、单张 ≤6MB，复用 ComfyUI/LLM 互斥闸门）。图片可从素材库挑选（历史只引用 `asset_id`、不存副本），也可本地上传临时图（临时图不进历史、不留图）；识别结果可连同素材引用主动存入图生文历史。后端按 `/api/show` 的投影器（`projector_info`）判定真实视觉能力，未挂投影器的模型不进入下拉并给出明确提示。**已真机跑通识别**：由同源 Qwen3-VL-8B-Instruct 视觉投影器（mmproj Q8_0，ModelScope 官方 GGUF 仓库）与本机 heretic 语言权重组装出新标签 `qwen3-vl:latest`，工作台页签上传图片可返回中文描述；纯文本底模与未挂投影器的 `qwen3:latest` 被精确过滤、不进下拉。
- **统一生成历史（2026-11-20）**：历史页签聚合三类记录——提示词（沿用 `generation_history`，仅出图任务）、图生文（新表 `vision_history`，素材引用）、写作（新表 `writing_history`，仅主动保存落库）；后端 `GET /api/history/all` 按分项聚合并返回计数，`DELETE /api/history/{kind}/{id}` 彻底删除（不做回收站，不联动草稿/素材原图/ComfyUI 原图）。三类各一张表、同一 SQLite 库，随整库备份自动纳入。
- **已知环境型测试失败（非回归，勿改测试逻辑）**：
  - `test_scripts.py::test_stopper_fallback_no_health_match_does_not_touch`：仓库根残留当前运行实例的 `.workbench-runtime.json` 触发 corrupt 分支。
  - `test_sidebar_summary.py::test_empty_sections_report_zero_totals`：读到本机真实非零计数。
- **显存约束**：本机 RTX 4060 Laptop 8GB，本地 LLM 与 ComfyUI 不同时驻留；出图前必须确认 LLM 已卸载、显存达标，闸门 fail-closed。
- 其余开放方向（布局记忆深化、视觉打磨等）见 `docs/ROADMAP.md` 当前剩余方向段；安全边界（白名单写入、unknown 不臆测、CSRF、回环绑定、fail-closed 闸门）不放宽，未拍板口径不擅自实现。

## 4. 设备与环境

- Windows 11，i7-12650H，16GB RAM，RTX 4060 Laptop 8GB VRAM。
- 后端独立 `.venv`（不复用整合包内部 Python）；Node/Vite 仅用于构建前端产物。
- 本机 Ollama 模型：`qwen3-vl:latest`（8.2B，已挂载 Qwen3-VL-8B-Instruct mmproj Q8_0 视觉投影器，可读图识别；语言权重为本机 heretic Q4_K_M）、`qwen3:latest`（8.2B，VL 架构纯语言底模，未挂投影器，不能读图，作为 VL 标签的语言层复用）、`qwen3-heretic:q4_k_m`（8.2B，纯文本）。原 `comfyui-prompt:v1` 已删除：它仅是 `qwen3:latest` 底模叠加一段系统提示词与固定参数的封装，无独立权重。
- 不入库：`userdata/`、`.venv/`、`frontend/dist/`、模型权重、原图与生成图片。
