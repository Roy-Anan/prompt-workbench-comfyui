# DOCUMENTATION_MAP — 文档目录与处置地图

- 更新日期：2026-11-20
- 目的：理清仓库内文档的现行定位、重复与过时项，指导后续拆分归档。
- 事实优先级沿用 `MAINTENANCE_AND_AGENT_HANDOFF.md` 第 1 节：当前对话要求 > 需求基线与 ADR > 架构/接口/测试 > 历史笔记。

## 1. 现行必读（交接顺序，见交接手册）

| 文档 | 定位 | 状态 |
|---|---|---|
| `README.md` | 项目入口、目标、不可破坏原则、技术方向 | 现行，保留在仓库根 |
| `docs/PROJECT_OVERVIEW.md` | 产品定位、使用流程、产品范围、安全边界、各模块概览 | 现行 |
| `docs/REQUIREMENTS_BASELINE.md` | 已确认需求决策（A–J 分组），权威基线 | 现行 |
| `docs/ARCHITECTURE.md` | 现行技术架构与模块边界（ADR-0002 本机 Web 形态） | 现行，架构唯一权威 |
| `docs/ROADMAP.md` | M0–M10 阶段路线图与开发节奏 | 现行 |
| `docs/MAINTENANCE_AND_AGENT_HANDOFF.md` | 安全红线、代码组织、测试要求、交接模板 | 现行 |

## 2. 现行规范与决策

| 文档 | 定位 | 状态 |
|---|---|---|
| `docs/PROJECT_CONTROL/INJECTION_SYSTEM_SPEC.md` | LLM 注入系统规范：文件分类、组合、追溯、安全边界 | 现行 |
| `docs/PROJECT_CONTROL/THREE_SLICE_SPEC.md` | 三段可选注入与两类身份规范（已落地） | 现行 |
| `docs/decisions/ADR-0001-TECH-STACK.md` | 早期技术栈决策（Tauri 路线，已废弃） | 历史决策，留档 |
| `docs/decisions/ADR-0002-LOCAL-WEB-RUNTIME.md` | 本机浏览器 Web 运行时决策（现行） | 现行 |

## 3. 历史归档（只读，不再回写）

| 目录 / 文件 | 定位 | 处置 |
|---|---|---|
| `docs/TASK_EVIDENCE/` | R2–R86 各切片证据回执、diff、测试结果 | 保持只读，不拆分 |
| `legacy-materials/`、`legacy/` | 旧项目素材与早期交接记录 | 保持只读 |
| `docs/ui-review/` | UI 评审前后截图与记录 | 留档 |

## 4. 待处置的根级重复 / 过时文档

下列文件位于仓库根，与 `docs/` 现行文档存在重复或已被取代。处置原则：**不删除历史内容**，迁入 `docs/archive/` 并在原位置留废弃指引；git 历史可追溯。

| 根级文件 | 现状判断 | 处置 |
|---|---|---|
| `PROJECT_ARCHITECTURE.md` | 早期 Tauri/桌面宿主架构，已被 `docs/ARCHITECTURE.md`（ADR-0002 Web）取代 | **已迁入** `docs/archive/PROJECT_ARCHITECTURE_legacy.md`，头部标注废弃并指向现行架构 |
| `PROJECT_MASTER_PLAN.md` | 早期总计划，产品定位/模块划分/安全规则已被 OVERVIEW、REQUIREMENTS_BASELINE、ARCHITECTURE 覆盖 | **已迁入** `docs/archive/PROJECT_MASTER_PLAN_legacy.md` |
| `PROJECT_CHANGELOG.md` | 版本时间线记录（仅到 dev.5 早期） | **已迁入** `docs/CHANGELOG.md`，后续版本记录写这里 |
| `PROJECT_STATE.md` | 306KB / 172 节流水账；仅最新几节反映当前状态，早期切片为历史记录 | 待拆分：当前有效状态提炼到 `docs/CURRENT_STATE.md`；第 1–159 节历史切片整体迁入 `docs/archive/STATE_HISTORY.md` |
| `UPLOAD_GUIDE.md` | 仓库远端、推送命令、不入库范围、隐私提醒 | **已迁入** `docs/UPLOAD_GUIDE.md`，README 已补引用 |

## 5. 目标目录结构

```
README.md
docs/
  PROJECT_OVERVIEW.md
  REQUIREMENTS_BASELINE.md
  ARCHITECTURE.md
  ROADMAP.md
  MAINTENANCE_AND_AGENT_HANDOFF.md
  CHANGELOG.md                 # 由根级 PROJECT_CHANGELOG 迁入
  UPLOAD_GUIDE.md              # 由根级 UPLOAD_GUIDE 迁入
  CURRENT_STATE.md             # 由 STATE 提炼的当前有效状态
  PROJECT_CONTROL/
    INJECTION_SYSTEM_SPEC.md
    THREE_SLICE_SPEC.md
  decisions/
  archive/
    PROJECT_ARCHITECTURE_legacy.md
    PROJECT_MASTER_PLAN_legacy.md
    STATE_HISTORY.md
  TASK_EVIDENCE/
  ui-review/
```

## 6. 拆分 STATE 的口径

- **只搬不改写历史**：历史切片原文保留，不重排、不更正结论；新认知以现行规范和代码为准。
- `CURRENT_STATE.md` 只放：当前阶段、开发分支与版本、已验证能力、未验证/遗留项、进行中的任务（三段式重构，见 THREE_SLICE_SPEC）。
- 节号与日期在归档文件中保留，便于和 `TASK_EVIDENCE/` 回执互相追溯。

## 7. 执行顺序

1. 建 `docs/archive/` 目录。
2. 先迁无争议的旧架构、旧总计划、变更日志、上传说明，并更新交叉引用。
3. 最后拆 `PROJECT_STATE.md`：先写 `CURRENT_STATE.md`，再把历史段落整体归档。
4. 全量检索根级文件名引用（README、交接手册、节点说明书），改指新路径。
5. 后端 pytest、前端构建回归；文档变更单独提交。
