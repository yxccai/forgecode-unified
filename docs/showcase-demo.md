# ForgeCode 可复现演示

[返回项目展示入口](../README.md)

## 演示内容

在临时样例 Git 仓库中，通过同一套 Runtime 展示工具执行、审批、状态保存、测试反馈与补丁导出。模型回复由 ScriptModel 预设；文件操作、测试进程和 SQLite 存档实际执行。它适合检查运行机制，不能用来衡量真实模型解题能力。

## 运行

需要 Python 3.12 或更新版本。在本仓库根目录打开 PowerShell：

```powershell
# 首次安装
.\setup.ps1

# 运行演示，不需要 API key
.\start.ps1 learn --json
```

产物默认保存在 `artifacts/learning/run-*/`。终端结果中的 `trace`、`checkpoint`、`patch`、`workspace` 给出本次运行的具体位置。

## 2026-10-26 本地复跑结果

本次使用项目独立虚拟环境运行 `python -B -m forgecode learn --json`，并将输出目录指定到工作区临时目录。进程退出码为 0，以下为实际汇总中选取的字段，省略本机绝对路径：

```json
{
  "mode": "scripted_learning",
  "status": "verified",
  "model_calls": 6,
  "tool_attempts": 6,
  "repairs": 1,
  "tokens": null,
  "baseline_passed": false,
  "final_passed": true,
  "source_repository_preserved": true
}
```

这里的 `model_calls` 是脚本模型接口调用次数，不是云端模型请求数；`tokens: null` 不代表真实模型零成本。

发布副本已于同日再次运行演示，结果一致。可直接查看 [演示汇总](../examples/demo-summary.json) 和 [实际导出补丁](../examples/repair.patch)，无需先安装项目。

## 建议检查的三个产物

1. **补丁：** 打开 `patch` 指向的文件，确认实际修改内容。
2. **事件轨迹：** 打开 `trace.jsonl`，跟踪工具调用、测试失败与后续回修。
3. **原始仓库与工作区：** 比较两者，确认工作区修改与原仓库分离。

## 接入真实模型

```powershell
.\start.ps1 chat
```

进入后用 `/api` 配置模型端点；用 `/verify` 设置验证命令。单次仓库任务可使用：

```powershell
.\start.ps1 run "描述具体开发任务" --repo "干净的样例仓库路径" --verify "python -m unittest discover"
```

真实模型运行需要可用端点及额度。普通命令按策略审批；仓库任务使用 worktree，获批命令仍以当前用户权限运行。

## 检查恢复机制

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_recovery -v
```

阅读 [恢复测试](../tests/test_recovery.py) 时，重点检查副作用后的故障注入、恢复后的文件内容，以及效果未知命令是否停止。发布前已执行完整 57 项测试与 22 道验收器自检，结果见 [验证记录](../examples/validation.json)。
