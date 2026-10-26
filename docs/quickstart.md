# 安装与运行

[返回项目首页](../README.md)

需要 Git 与 Python 3.12 或更新版本。目前主要在 Windows + Python 3.13 下验证；macOS/Linux 运行路径尚待对应环境验证。

## 获取项目

```powershell
git clone https://github.com/yxccai/forgecode-unified.git
cd forgecode-unified
```

## 安装

Windows PowerShell：

```powershell
.\setup.ps1
```

也可以手动创建隔离环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

macOS/Linux 可使用 `python3 -m venv .venv` 和 `.venv/bin/python -m pip install -e .`，后续用该解释器执行 `-m forgecode`。平台相关进程管理需在目标系统验证。

## 无密钥演示

```powershell
.\start.ps1 learn --json
```

预设模型回复驱动真实的工具、文件、worktree、SQLite 与测试过程。结果和复现说明见 [演示文档](showcase-demo.md)。

如果 PowerShell 执行策略不允许脚本，可直接运行：

```powershell
.\.venv\Scripts\python.exe -m forgecode learn --json
```

## 真实模型交互

```powershell
.\start.ps1 chat
```

进入后使用 `/api` 设置模型名称、OpenAI 兼容端点与 API key。密钥保存在本地配置，相关文件已列入 `.gitignore`。真实模型运行使用端点对应额度。

- `/verify`：设置用户认可的验证命令。
- `/sessions`、`/history`：查看会话。
- `/resume`：继续支持恢复的会话。

未配置验证时，普通回答状态为 `answered`，不等同于 `verified`。

## 仓库任务

先准备一个已提交的、工作区干净的独立 Git 样例仓库，再执行：

```powershell
.\start.ps1 run "修复具体 bug 并保留现有测试" --repo "样例仓库路径" --verify "python -m unittest discover"
```

命令行任务需要可用的模型配置。可通过 `FORGECODE_MODEL`、`FORGECODE_BASE_URL`、`FORGECODE_API_KEY` 环境变量配置；也可用 `python -m forgecode configure` 交互创建本地配置，再给 `run` 添加 `--config .forgecode/config.local.json`。密钥不要写进可公开的命令记录。

任务在独立 Git worktree 中修改，导出补丁供检查。worktree 隔离代码版本；获批命令仍拥有当前用户的系统权限。

## 测试与验收器自检

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -v
.\start.ps1 eval --offline
```

第一条检查执行机制，第二条检查 22 道题的错误基线和参考解是否能被验收器区分，都不能替代真实模型效果评测。

## 开发导航

- `forgecode/agent/`：状态、运行图、规划与测试回修。
- `forgecode/context/`：源码检索与上下文选择。
- `forgecode/tools/`：参数定义、工具注册、文件编辑和结果回读。
- `forgecode/execution/`：审批、路径、进程、worktree 与动作回执。
- `forgecode/evaluation/`：任务、独立验收、重复与模块对照评测。
- `tests/`：机制测试。

详细接口与边界见 [架构说明](architecture.md)。
