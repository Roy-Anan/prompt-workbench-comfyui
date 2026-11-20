# 仓库与推送说明

远端仓库：

https://github.com/Roy-Anan/prompt-workbench-comfyui

- `main`：稳定快照分支；`dev`：日常开发分支。
- 本地提交先落在 `dev`，收口时快进合并到 `main` 再推送。

## 常用命令

```bash
# 查看状态与未推送提交
git status
git log --oneline origin/dev..dev

# 推送当前分支
git push -u origin dev
git push -u origin main
```

远端别名：`origin` 指向上面的新仓；`legacy-origin` 保留早期草稿仓地址，仅作留档，不再推送。

## 不入库的内容

`userdata/`（SQLite 数据库、工作流副本与快照、档案、草稿、历史、素材、备份、导出、运行日志、节点令牌）、`.venv/`、`frontend/dist/`、`__pycache__/`、环境变量文件与运行 PID 记录均已在 `.gitignore` 中忽略。

## 隐私提醒

- 不要把 GitHub 密码、Personal Access Token 或任何密钥提交进仓库。
- 不要上传 ComfyUI 模型、LoRA 权重、生成图片或私人配置文件。
- 节点令牌 `userdata/settings/node_token.json` 只存在本机数据目录，不入库。
