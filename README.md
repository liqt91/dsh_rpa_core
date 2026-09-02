# rpa_core

Clean-room 类型化 RPA 运行时核心实验：**显式 workflow 语义、隔离的执行器、强类型结果、可复现的运行证据**。控制流是 workflow AST（不是命令插件），一次运行绑定一份不可变的命令目录快照，用户代码只在隔离的 worker 子进程里执行。

> 面向维护者的任务、计划、验收、ADR 正文见 `.harness/`（中文）；本 README 面向使用者与贡献者。

## 定位

一条垂直切片贯穿全程：

```text
Workflow AST → validation/compiler → execution plan → orchestrator
  → browser.playwright / desktop.uia / desktop.win32 / python.worker
  → typed results + events.jsonl + result.json
```

已建成：确定性本地测试站 + CLI 端到端示例、真实站点示例、Win32/UIA 桌面垂直切片、带可视化编辑器的设计期 dev server、桌面与浏览器元素捕获、元素库（捕获→入库→插入→保存闭环）。

### 明确排除（当前不做）

FastAPI、数据库、React UI、DSH 插件、MCP、调度器、安装器、分布式 worker、exactly-once、在线命令生成 / 动态模块热替换 / orchestrator 进程内任意 Python 执行。决策记录见 ADR（`.harness/adr/`）与 `.harness/project_state.json` 的 `scope.out`。

## 核心约束

1. 控制流是 workflow AST，永不落在命令插件里。
2. 一个命令一个 manifest（`commands/**/*.json`）；executor 只含实现。
3. handler 返回 `CommandResult`，不直接改动 orchestrator 的计数器 / 变量 / 日志。
4. 浏览器与桌面操作都要显式 session：先 launch/attach，再用 `sessionId`。
5. 用户 Python 永不跑在 orchestrator 进程内（只经 `python.worker` 子进程）。
6. 一次运行使用一次不可变的命令目录快照（含 digest 校验）。
7. 每次运行都必须到达终态（succeeded/failed/cancelled/abandoned/recovery_required/indeterminate）或驻留 `paused`；成功 = `runFinished` 与 `result.json` 都落盘。
8. Executor 必须传播任务取消并在 `execute()` 退出前释放资源。
9. 跨平台 = 稳定契约 + 能力感知驱动（Windows 优先，非逐 OS 桌面行为一致）。

完整不变量见 `.harness/invariants.md`，架构见 `.harness/architecture.md`。

## 里程碑与当前状态

| 里程碑 | 状态 | 一句话 |
|---|---|---|
| M1 垂直切片 → M7 命令面 | done | 运行时/恢复/桌面/数据命令/API 契约打底 |
| M8 devserver / M9 编辑器 / M11 树画布 / M12 中文层 | done | 设计期工具与可视化编辑器 |
| M10 元素捕获 / M13 编辑器元素库 | done | 桌面 UIA + 浏览器双传输捕获；库面板 + 插入/删除/verify + 一键捕获 |
| M14 自研捕获扩展 | **active** | token 配对反连 dev server，免弹窗 |

进度表：`.harness/PROGRESS.md`；完整计划与后续项：`.harness/tasks/BACKLOG.md`；最近完成项由 `.harness/project_state.json` 指向。命令目录当前共 26 条：`browser.*`(8)、`data.*`(4)、`desktop.*`(UIA, 6)、`desktop.win32.*`(8)。

## 仓库布局

```text
rpa_core/
├─ src/rpa_core/
│  ├─ cli.py                  # validate / run / resume / devserver
│  ├─ catalog/                # 命令目录加载（不可变快照 + digest）
│  ├─ compiler/               # 静态编译：引用/能力/unsafe-retry → ExecutionPlan
│  ├─ runtime/                # orchestrator、checkpoint、事件、resolver、恢复
│  ├─ executors/              # browser.playwright / desktop.uia / desktop.win32
│  ├─ workers/                # python.worker 子进程（隔离用户代码）
│  ├─ model/                  # workflow / command / runtime / capture / desktop 类型
│  ├─ capture/                # 桌面 UIA hit-test、浏览器 picker 注入
│  └─ devserver/              # 设计期 HTTP 服务 + 静态编辑器（见 docs/devserver.md）
├─ commands/                  # 命令 manifest（JSON，id 如 browser.launch / desktop.win32.click）
├─ workflows/                 # 每流程一个目录 <流程名>/workflow.json（可入版本库），
│                             #   捕获元素作为流程资产存 <流程名>/elements/*.json
├─ examples/                  # 可运行示例（见下）
├─ docs/                      # 使用手册与设计文档（见"文档导航"）
├─ testsite/  testapps/       # 确定性本地站点（E2E 浏览器目标）与桌面测试应用
└─ .harness/                  # 架构检查、任务计划、ADR、feature 门禁
```

目录职责分离：`workflows/` = 流程定义与元素资产（每流程一目录）；`run_artifacts/` = 运行证据（gitignore）。

### workflow 形态（一个片段）

```json
{
  "schema_version": "1.0",
  "id": "search-and-save",
  "inputs": { "keyword": "RPA Core", "outputPath": "run_artifacts/search-results.json" },
  "root": {
    "type": "sequence",
    "children": [
      { "type": "action", "id": "launch", "command": "browser.launch", "with": { "headless": true } },
      { "type": "action", "id": "input", "command": "browser.input",
        "with": { "sessionId": "${steps.launch.outputs.sessionId}",
                  "selector": "#query", "text": "${inputs.keyword}" } },
      { "type": "action", "id": "save", "command": "data.writeJson",
        "with": { "workspace": "${inputs.workspace}", "path": "${inputs.outputPath}",
                  "data": "${steps.collect.outputs.items}" } },
      { "type": "return", "value": "${steps.collect.outputs.items}" }
    ]
  }
}
```

AST 节点：`sequence` / `action` / `if` / `forEach` / `try` / `return`；引用 `"${steps.<id>.outputs.<key>}"`、`"${inputs.<key>}"` 支持。

## 快速开始

```powershell
uv sync --all-groups
uv run python -m playwright install chromium        # E2E / 浏览器执行需要

# CLI 试跑确定性示例
uv run python -m rpa_core.cli run examples/search-and-save/workflow.json
```

完整本地门禁（含架构、任务、feature 一致性）：

```powershell
uv run python .harness/scripts/check_all.py
```

## CLI

```text
uv run python -m rpa_core.cli validate examples/search-and-save/workflow.json
uv run python -m rpa_core.cli run     examples/search-and-save/workflow.json [--artifacts DIR]
uv run python -m rpa_core.cli resume  workflow.json --run-id <run_id> [--allow-indeterminate]
uv run python -m rpa_core.cli devserver [--port 8765] [--workflows DIR]
```

- `resume` 需 `--run-id`（见该 run 的 `result.json`），`indeterminate` 终态的人工续跑需 `--allow-indeterminate`。
- 运行证据落 `<artifacts>/<run_id>/`：`result.json`（终态结果）、`events.jsonl`（追加事件流）、`checkpoint.json`（边界进度）。

## 三种使用形态

### 1) CLI（验证 / 试跑 / 恢复）

见上；示例 workflow 都在 `examples/*/workflow.json`。

### 2) 进程内 Python API（第一形态，契约冻结 v1）

ADR 0006：不建 HTTP facade，直接 import 即用，四步模式：

```python
catalog = load_catalog(REPO / "commands")                    # 1) 不可变快照
plan = WorkflowCompiler(catalog).compile(workflow, {"workspace.write"})  # 2) 编译即 dry-run
registry = ExecutorRegistry({"python.worker": PythonWorkerExecutor()})   # 3) 调用方持生命周期
result = await orchestrator.run(plan, inputs={...})           # 4) await 到 RunResult
```

细节（executor 选择、artifacts 约定、状态机消费指引、pause/resume）见 `docs/api-usage.md`，可运行示例 `examples/api-usage/run_workflow.py`。

### 3) 可视化编辑器 + 元素捕获（设计期 dev server）

```powershell
uv run python -m rpa_core.cli devserver
# 浏览器打开 http://127.0.0.1:8765/
```

编辑器：左侧命令面板点/拖入节点 → 画布编辑（拖拽、多选、复制粘贴、撤销重做、控制节点表单）→ 右侧属性表单（`selector` 字段旁「捕获」按钮可开浏览器点选回填）→ 编译回显 `errors[]` → 保存/打开 workflow。

元素捕获（M10，`docs/devserver.md` 有 PowerShell 全流程）：

- 桌面：`POST /api/capture/desktop/start` → 鼠标移到目标控件按 F9 → `pick`（`saveAs` + `flow` 存入当前流程元素资产）。
- 浏览器：`persistent`（专用持久 profile，登录一次后 cookies 保留）或 `user-browser`（复用日常 Chrome/Edge 登录态，chrome-inspect-ws）。

元素库（M13 + 流程目录）：编辑器元素库面板显示**当前流程**（文件名框）的元素资产 `workflows/<流程名>/elements/` → 浏览/插入到选中节点字段 → 结构校验（verify）→ 删除；端点嵌套于流程：`/api/workflows/<流程名>/elements[/<元素>[/verify]]`。

端点与编辑器用法详见 `docs/devserver.md`。

## 示例

| 示例 | 内容 |
|---|---|
| `search-and-save` | 确定性本地站点搜索并保存 JSON（CLI/E2E 主样例） |
| `baidu-news-top10` | 真实站点：百度搜索「新闻」→ 标题前 10 条 → 文本文件 |
| `api-usage` | 进程内 API 四步 + run→读证据→pause→resume |
| `windows-desktop` / `uia-desktop` | 记事本 / WinForms 桌面垂直切片 |

## 测试与质量门禁

```powershell
uv run pytest                            # unit + contract + e2e（含真实 Chromium / Windows 桌面）
uv run ruff check .
uv run python .harness/scripts/check_architecture.py   # 架构不变量（38 py / 26 manifest）
uv run python .harness/scripts/check_all.py            # 完整门禁：测试 + 架构 + 任务一致性
```

E2E 依赖 `playwright install chromium`；桌面 E2E 在 Windows + pywinauto 上运行。

## 文档导航

| 文档 | 内容 |
|---|---|
| `docs/api-usage.md` | 进程内 API 使用契约 v1（四步模式 / 状态机 / 证据） |
| `docs/devserver.md` | dev server 手册：启动、端点速查、编辑器、元素捕获全流程 |
| `docs/editor-design.md` | 编辑器视觉设计（中文层、i18n、术语表） |
| `docs/capture-transport.md` | 浏览器捕获传输方案与降级链 |
| `docs/desktop_backends.md` | 桌面后端矩阵（UIA / win32） |
| `docs/m2_1_legacy_element_inventory.md` | 旧元素静态盘点（clean-room 边界） |
| `.harness/adr/` | ADR 0001-0008：replay 契约、桌面驱动、checkpoint、pause、API 面、devserver、编辑器 UI |
| `.harness/architecture.md` / `invariants.md` | 架构与不可变规则 |
| `.harness/PROGRESS.md` / `BACKLOG.md` / `feature_list.json` | 进度 / 计划 / feature 门禁 |
