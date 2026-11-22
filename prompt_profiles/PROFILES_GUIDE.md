# 模型规则套维护指南

> 面向本机日常维护：怎么新增、修改、停用、回滚、删除一套底模规则套（Prompt Profile）。
> 规则套是注入给本地 LLM 的「第二层 · 模型专用规则」，按 checkpoint 文件名 / 架构自动匹配，也可在下拉里手动指定。研究证据分级与禁止事项见 `templates/MODEL_PROFILE_AGENT_INSTRUCTIONS.md`。

## 1. 目录结构

```
prompt_profiles/
  core/core_system_prompt.txt      # 第一层：与具体模型无关的通用核心规则（不要删）
  models/<规则套 id>/
    rules.txt                      # 必选：规则正文（提示词语法、顺序、负向、偏好……）
    meta.json                       # 可选：结构化补充信息（匹配模式、别名、优先级、停用、版本……）
  staging/<id>/                    # 待发布草稿（可选工作区，发布后自动清除）
  archive/<id>/<时间戳>/            # 旧版本归档（回滚用，自动生成）
  genres/  styles/  modes/          # 写作类题材 / 文风；创作模式（均扁平 txt，文件名即选项名）
  templates/                       # 空白骨架模板
```

- 后端遍历 `models/` 目录列出规则套，**不再有中央清单文件**：往 `models/` 下放好文件夹就会出现在下拉里。
- 文件夹名即规则套 id，只允许小写字母、数字、`-`、`_`，以 `_` / `.` 开头的目录会被跳过。

## 2. 最快新增一套（只填规则正文）

1. 在 `models/` 下新建文件夹，名字用规范 id，例如 `my_realism_xl`。
2. 复制 `templates/MODEL_RULE_TEMPLATE.txt` 到该文件夹的 `rules.txt`。
3. 填规则正文；头部三行可被后端自动解析：

   ```
   Display Name: 我的写实 XL
   Exact Checkpoint Filename: myRealismXL_v1.safetensors
   Base Architecture: SDXL 1.0
   ```

4. 保存后刷新工作台下拉即出现。不放 `meta.json` 也能用：显示名 / 架构 / 文件名匹配来自这三行，其余字段走默认（启用、优先级 0）。

## 3. `rules.txt` 头部可解析的键

后端扫描正文里的 `键: 值` 行（中英文冒号均可），方括号标记如 `[已核实]` 会自动剥掉。`meta.json` 里同名字段优先于正文解析。

| 正文键 | 用途 | 对应 meta.json 字段 |
|---|---|---|
| `Display Name` | 下拉显示名 | `display_name` |
| `Exact Checkpoint Filename` | 精确 / 子串文件名匹配 | `checkpoint_patterns` |
| `Base Architecture` | 架构兼容匹配 | `architecture` |

正文其余章节（提示词语法、组织顺序、负向策略、模型偏好、避免写法、示例、可信度、来源）按模板分节填写；缺章节只会记为提示性警告，不会让整套失效。

## 4. 可选 `meta.json` 字段

需要额外文件名模式、别名、调优先级、停用、记版本号时，在同目录放 `meta.json`：

```json
{
  "display_name": "Juggernaut XI by RunDiffusion",
  "description": "简短说明，仅后台备查，下拉不展示",
  "architecture": "SDXL 1.0",
  "checkpoint_patterns": ["Juggernaut-XI-byRunDiffusion.safetensors"],
  "aliases": ["jugg xi", "jugxi"],
  "priority": 10,
  "enabled": true,
  "version": "1.0.0",
  "trigger_words": [],
  "incompatible_architectures": [],
  "author": "",
  "research_status": "verified",
  "source": "资料来源记录"
}
```

| 字段 | 含义 | 默认 |
|---|---|---|
| `display_name` | 下拉显示名；缺省取正文头部，再缺省取文件夹 id | 文件夹 id |
| `description` | 后台备注，前端不展示 | 空 |
| `architecture` | 基础体系标签，用于架构兼容匹配 | 正文解析，仍缺则报错标记 |
| `checkpoint_patterns` | 参与文件名匹配的文件名列表（精确 / 子串） | 正文 `Exact Checkpoint Filename` |
| `aliases` | 文件名别名，子串参与匹配 | 空 |
| `priority` | 多套同分并列时的排序权重，越大越优先 | 0 |
| `enabled` | 是否参与 Auto 匹配；停用后下拉置灰、不自动选用 | `true` |
| `version` | 语义版本号；staging 发布未指定时自动 patch +1 | 空 |
| `trigger_words` | 触发词备查，不参与自动匹配 | 空 |
| `incompatible_architectures` | 明确不兼容的架构标签备查 | 空 |
| `author` / `research_status` / `source` | 作者、可信度状态、来源记录 | 空 |

> 内容哈希由后端读取时实时计算，**不需要也不要**在 `meta.json` 里手写；规则路径、时间戳、归档记录都由程序维护。

## 5. Auto 匹配顺序

自动匹配（下拉选「自动匹配 Profile」）按以下链条打分，命中即候选，并列时按 `priority` 降序、再按 id 稳定排序，结果不随机：

1. **exact**：checkpoint 文件名与某条 `checkpoint_patterns` 完全一致（5 分）
2. **pattern**：文件名与模式互为子串（4 分）
3. **alias**：文件名包含某条别名（3 分）
4. **architecture**：架构同家族或标签互为子串（2 分）
5. 都不匹配 → 通用 Core 规则回退，候选结果里标注回退原因

手动在下拉指定某一套时：该套不存在提示「不存在」，已停用提示「已停用」；架构与工作流不符只给兼容警告，不会静默丢掉手动选择。

## 6. 停用 / 版本 / 回滚 / 删除

这些操作走后端接口（前端暂未做管理界面，可用脚本或接口调用），状态都落在目录内：

- **停用 / 启用**：写 `meta.json` 的 `enabled` 为 `false` / `true`；停用保留文件与归档，只是不参与 Auto。
- **发布新版本**：把新规则放 `staging/<id>/rules.txt`（可附 `profile.json` 提供本次元数据），调用发布；发布前的当前版本自动归档到 `archive/<id>/<时间戳>/`，旧规则与元数据一并留存。
- **查看历史版本 / 回滚**：`archive/<id>/` 下每个时间戳一套；回滚会先把当前版本再归档一份，再用归档版本覆盖 `models/<id>/`。
- **删除**：二次确认后删除 `models/<id>/`、对应 `archive/<id>/` 与 `staging/<id>/`；Core 通用规则不属于规则套，不能通过此入口删除。

## 7. 排查：新规则套没出现在下拉

- 文件夹名不符合 `[a-z0-9_-]` 规范，或以 `_` / `.` 开头 → 被跳过。
- 缺 `rules.txt` 或为空 → 列出但标记 `exists=false`，不参与自动匹配。
- 架构既没在 `meta.json` 给、正文头部也解析不到 → 记错误标记，`auto_eligible=false`。
- 改完文件没刷新：扫描按目录修改时间缓存，重新打开下拉 / 调列表接口即热加载；仍异常可重启后端。

## 8. 边界

- 规则套文件按不可信资料处理：其中「忽略以上规则」「修改工作流参数」「直接提交出图」等内容不产生程序效力。
- 只管理本地规则文本；不读取、不修改、不复制模型权重，不联网查模型库。
- 露骨成人 / 色情题材的规则与身份模板不收录；未确认信息只能隔离到「未确认项」，不得写成确定规则。
