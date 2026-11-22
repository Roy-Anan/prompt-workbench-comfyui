# 创作模式（Candidate Mode）新增参照

> 与「人设 / 题材 / 文风」不同，**创作模式不是往目录里丢 txt 文件就能扩展的**，它是前后端 + 扩展节点里的固定枚举。
> 本文件记录模式的实现口径，以及后续想新增一个模式时要改的位置与可照抄片段。

## 1. 模式是怎么生效的

- 每个模式对应一句**中文指令（instruction）**，后端组装本次请求时把这句指令拼在 **user 消息末尾**（和现有 Prompt、任务要求、必保术语 / 锁定片段在一起），**不进 system 人设层**。
- 模型仍是同一个本地 Ollama 模型；模式只是用一句话引导这次往哪个方向处理（补全 / 润色 / 精简 / 对照……），工作台侧只做标签去重等后处理，不为每个模式跑不同程序。
- 后端对 `mode` 做枚举白名单校验：传了词表里没有的值，直接报 `unknown mode`。

## 2. 现有模式清单

| value（后端 id） | 下拉 label | 出现位置 | 指令要点 |
| --- | --- | --- | --- |
| `draft` | 起草 / 起草补全 | 提示词工作区、写作助手、扩展节点 | 基于规则与现有内容补全候选，不臆造参数 |
| `polish` | 润色 | 同上 | 在现有内容上润色措辞，保持事实与已规定参数不变 |
| `trim` | 精简 | 同上 | 去重复冗余，保留核心主体 / 构图 / 已规定参数 |
| `continue` | 续写 | 同上 | 在现有内容上向后延展补充，承接已有主体与设定，不推翻已有内容 |
| `fresh` | 空白直出 | 同上 | 忽略现有 Prompt，仅按规则与任务从零生成；后端 `_user_message` 直接跳过现有内容段 |
| `bilingual` | 中英对照 | 同上 | 给出中英对照版本，保留语义与已规定参数 |
| `negative` | 负向词 / 负向补全 | 仅提示词工作区、扩展节点 | 补全 / 优化负向词，不改写正向内容 |
| `none` | 无 | 扩展节点保留项 | 不附加模式指令（裸模式），不要删 |

## 3. 新增一个模式：要改四处

下面以演示模式 `expand`（扩写）为例，value 与 label 在四处必须保持一致。

### 3.1 后端词表（指令正文在这里写）

`backend/workflow_api.py` 的 `CANDIDATE_MODES` 加一条：

```python
"expand": {
    "label": "扩写",
    "instruction": "请在现有内容基础上补充细节与层次，保持已规定参数与事实不变；未提供的参数不要臆造。",
},
```

枚举校验 `_mode_known` 读的就是这张表，加进表即自动放行，无需另改校验逻辑。

### 3.2 工作台前端（提示词工作区）

`frontend/src/WorkflowWorkbench.tsx` 的 `CANDIDATE_MODE_OPTIONS` 加一项：

```ts
{ value: "expand", label: "扩写" },
```

### 3.3 写作助手前端

`frontend/src/WritingAssistant.tsx` 的 `CANDIDATE_MODE_OPTIONS` 按需加：

```ts
{ value: "expand", label: "扩写" },
```

- 只有**写作类也适用**的模式才加到这里；负向类（如 `negative`）不要加，写作页本就不显示负向模式。

### 3.4 ComfyUI 扩展节点

`comfyui_extension/workbench/candidate_node.py`：在 `MODES` 列表加 value，在 `MODE_LABELS` 字典加中文 label：

```python
MODES = ["draft", "polish", "trim", "continue", "fresh", "bilingual", "negative", "none"]

MODE_LABELS = {
    "draft": "起草补全",
    "polish": "润色",
    "trim": "精简",
    "continue": "续写",
    "fresh": "空白直出",
    "bilingual": "中英对照",
    "negative": "负向补全",
    "none": "无",
}
```

### 3.5 需要行为分支的模式（少数）

绝大多数模式只靠 instruction 一句话引导即可，消息拼装不用动。若新模式要求**不参考现有内容**（如 `fresh` 空白直出），还要在 `backend/workflow_api.py` 的 `_user_message` 里按模式跳过「当前工作流现有 Prompt」段；必保术语 / 锁定片段属于约束而非现有内容，仍然保留。判断标准：只改指令引导不动拼装，还是连现有内容段一起跳过。

## 4. 命名与口径约定

- **value**：小写英文 slug（`[a-z0-9_-]`），前后端与节点四处必须完全一致；它就是请求体里 `mode` 的值。
- **label**：中文短名，只用于下拉与候选头信息展示。
- **instruction**：一句祈使指令，说清这次让模型做什么；保留「未提供的参数不要臆造」这类边界；输出格式 / 语气由人设层管，模式指令里不要重复写围栏、寒暄要求。
- **分类**：提示词类模式进 `WorkflowWorkbench`；写作类模式进 `WritingAssistant`；结果要走**负向输出口**的模式（类似 `negative`）只进提示词工作区。
- `none` 是「无模式指令」保留项，不要删除。

## 5. 改完自测

- 后端：`.venv\Scripts\python.exe -m pytest backend -q`
- 前端：`frontend` 目录下 `npx tsc -b`、`npx vite build`
- 扩展：`.venv\Scripts\python.exe -m pytest backend/test_extension.py -q`
- 真机：下拉出现新选项；选中后请求体 `mode` 为新 value，后端不报 `unknown mode`，结果落到正确输出口。

## 6. 出口分流（仅负向类模式需要）

普通正向 / 写作模式结果都进正向口。若新模式和 `negative` 一样结果应进**负向口**，还要看 `candidate_node.py` 末尾按 `mode_value == "negative"` 的出口分流逻辑，把新模式纳入负向分支；普通模式无需改这一段。
