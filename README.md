# rpa_core

Clean-room 类型化 RPA 运行时核心实验：**显式 workflow 语义、隔离的执行器、强类型结果、可复现的运行证据**。控制流是 workflow AST（不是命令插件），一次运行绑定一份不可变的命令目录快照，用户代码只在隔离的 worker 子进程里执行。

> 面向维护者的任务、计划、验收、ADR 正文见 `.harness/`（中文）；本 README 面向使用者与贡献者。

## 定位

一条垂直切片贯穿全程：

```text
Workflow AST → validation/compiler → execution plan → orchestrator
  → browser.extension（自研扩展单通道，复用用户真实已登录浏览器）/ desktop.uia / desktop.win32 / python.worker
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
| M14 自研捕获扩展 | **active** | content-script 扩展反连 dev server，无缝捕获免弹窗 |

进度表：`.harness/PROGRESS.md`；完整计划与后续项：`.harness/tasks/BACKLOG.md`；最近完成项由 `.harness/project_state.json` 指向。命令目录当前共 26 条：`browser.*`(8)、`data.*`(4)、`desktop.*`(UIA, 6)、`desktop.win32.*`(8)。

## 仓库布局

```text
rpa_core/
├─ src/rpa_core/
│  ├─ cli.py                  # validate / run / resume / devserver
│  ├─ catalog/                # 命令目录加载（不可变快照 + digest）
│  ├─ compiler/               # 静态编译：引用/能力/unsafe-retry → ExecutionPlan
│  ├─ runtime/                # orchestrator、checkpoint、事件、resolver、恢复
│  ├─ executors/              # browser.extension（扩展单通道）/ desktop.uia / desktop.win32
│  ├─ workers/                # python.worker 子进程（隔离用户代码）
│  ├─ model/                  # workflow / command / runtime / capture / desktop 类型
│  ├─ capture/                # 桌面 UIA hit-test、浏览器 picker 注入
│  └─ devserver/              # 设计期 HTTP 服务 + 静态编辑器（见 docs/devserver.md）
├─ commands/                  # 命令 manifest（JSON，id 如 browser.navigate / desktop.win32.click）
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
      { "type": "action", "id": "openPage", "command": "browser.navigate",
        "output_aliases": { "sessionId": "web" },
        "with": { "url": "http://127.0.0.1:8765/", "headless": true } },
      { "type": "action", "id": "input", "command": "browser.input",
        "with": { "sessionId": "${web}",
                  "selector": "#query", "text": "${inputs.keyword}" } },
      { "type": "action", "id": "save", "command": "data.writeJson",
        "with": { "workspace": "${inputs.workspace}", "path": "${inputs.outputPath}",
                  "data": "${results}" } },
      { "type": "return", "value": "${results}" }
    ]
  }
}
```

AST 节点：`sequence` / `action` / `if` / `forEach` / `try` / `return`。

### 用户变量

命令的输出可命名（`output_aliases`），后续节点用 `${变量名}` 直接引用，不必写冗长的 `${steps.<id>.outputs.<key>}`：

| 写法 | 含义 |
|---|---|
| `output_aliases: { "sessionId": "web" }` | 把该命令的 `sessionId` 输出命名为变量 `web` |
| `${web}` | 引用变量整值 |
| `${web.sessionId}` | 引用变量的子字段 |
| `data.setVar` 命令 | 显式赋值：`{"varName": "keyword", "value": "${inputs.keyword}"}` |

变量是**可写**的：同一变量可被后续 `data.setVar` 重新赋值（覆盖旧值），再被更后面的节点读到新值——即「节点 1 定义 → 节点 2 修改 → 节点 3 读取改后值」。

编译期校验：变量名唯一（重复声明报 `Duplicate alias`）、不得占用内置作用域根名（`inputs` / `steps` / `loop`，报 `Reserved alias name`）、不得前向引用（先引用后声明报 `Forward variable reference`）。

节点参数的**键集合**也在编译期按 manifest 的 `input_schema` 校验（与运行期同一份 schema）：出现未声明的键报 `Unknown parameter(s)`、缺 `required` 报 `Missing required parameter(s)`。两类例外是有意的：`elementRefs` 覆盖的键不算缺（引用模型下被引用参数不写进 `with`，运行期由元素库换入）。**值**（类型/枚举/范围）不在这里判，留给运行期——运行期校验的是 `resolve` 求值 + python 字段子进程求值之后的值，静态看到的是另一样东西（`"${steps.x.outputs.items}"` 是字符串、`expr_modes=python` 的字段里存的是源码）。这条判据的意义是「编译即 dry-run」要成立——过去只校验 schema 自身合法，不看节点参数，于是「编译通过、第一个节点就 `INVALID_INPUT`」的漂移能一路走到运行期。

内置作用域（始终可用）：`${inputs.<key>}`、`${steps.<id>.outputs.<key>}`、`${loop.<item_var>}`、catch 块内的 `${<error_var>.code}`。

## 快速开始

```powershell
uv sync --all-groups

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
uv run python -m rpa_core.cli run      examples/search-and-save/workflow.json [--artifacts DIR]
uv run python -m rpa_core.cli resume   workflow.json --run-id <run_id> [--allow-indeterminate]
uv run python -m rpa_core.cli catalog                                   # 命令目录 digest + 清单
uv run python -m rpa_core.cli capture browser|desktop [选项] [--save-as 名 --flow 流程]   # 交互捕获
uv run python -m rpa_core.cli elements list|show|verify --flow 流程 [--workflows DIR]      # 元素资产
uv run python -m rpa_core.cli devserver [--port 8765] [--workflows DIR]
```

- CLI 与 devserver 同为能力层薄通道（ADR 0006 §6 通道对齐）：catalog/capture/elements 两通道同权。
- `capture` 为一次性会话：启动 → 阻塞等待用户手势（extension：⌘/Ctrl+Click 或右键；desktop：热键 F9）→ 打印描述符 JSON → 可选 `--save-as --flow` 落库到 `workflows/<流程>/elements/`。`--click-css` 供自动化验收合成 Ctrl+Click。
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

运行控制（M18，ADR 0011）：编辑器「▶ 运行」spawn 子进程跑 `rpa-core run`（devserver 进程内无 orchestrator/run 状态，隔离不破）→ 轮询 `/api/runs/{runId}` 状态 + 事件流面板 → 「■ 取消」终止子进程；pause/resume/indeterminate 对话后置。

端点与编辑器用法详见 `docs/devserver.md`。

## 装进 AI agent（技能分发）

`workbuddy-suite/skills/workflow-studio/` 是一份**宿主无关**的 agent 技能（`SKILL.md` +
`references/` + `scripts/`），DeepSeek Harness、Claude Code、Codex 共用同一份源文件。
分发靠 `bin/rpa-skill.mjs`，零依赖、不需要先安装任何东西。

```bash
# ① npm：发布到 registry 后
npm i -g rpa-workflow-studio
rpa-skill install          # 自动装到本机已存在的宿主

#    未发布时直接从 git 源装（同样得到 rpa-skill 命令）
npm i -g github:<you>/rpa_core
rpa-skill install --target dsh

# ② clone（完全不碰 npm）
git clone <repo> rpa_core && cd rpa_core
node bin/rpa-skill.mjs install

# ③ 自查 / 卸载
rpa-skill doctor           # 装完先跑这个：它会告诉你运行时连没连上
rpa-skill status
rpa-skill uninstall --target dsh
```

> `npx <pkg> install` 这种写法也能用，但参数归属在各 npm 版本上不完全一致
> （`install` 可能被当成包名），所以文档里只给 `npm i -g` 与 `node bin/…` 两种确定能跑的形态。

**技能依赖 `rpa-core` 的 Python 运行时，而 npm 包里不带它**——这是分发包最容易断的一环：
技能被拷进宿主目录后，原仓库的 `src/`、`editor/`、`extension/` 都不在了，裸命令
`rpa-core catalog` 会 `command not found`。所以安装器会现场探测「哪个解释器能 import
`rpa_core`、源码根在哪」，把结果写进技能目录的 `runtime.json`，并把文档里的
`{{PYTHON}}` / `{{RPA_CORE}}` 换成绝对路径（`{{RPA_CORE}}` 指向技能自带的
`scripts/rc.py`，它按 `runtime.json` 二次定位，换 venv / 换 PATH 也还能找回）。

探测不到时**安装不会失败**（文件是好的），但会在 `runtime.json` 缺席 + stderr 上报警，
`rpa-skill doctor` 同步报红。补上只需重装一次并指一下仓库根：

```bash
rpa-skill install --force --rpa-core-dir <rpa_core 仓库根>
# 或：--python <能 import rpa_core 的解释器>
```

安装器做四件事：拷目录、探测运行时并写 `runtime.json`、替换 `{{SKILL_DIR}}` /
`{{PYTHON}}` / `{{RPA_CORE}}` 三个占位符（agent 的 cwd 通常是项目根，相对写
`scripts/studio.py` 一定找不到）、生成 `skill.json`。
它不跑 postinstall、不写注册表；`--force` 时旧目录改名成 `.replaced-<时间戳>` 保留而不删除。

目标目录（不指定 `--target` 时自动装到本机已存在的宿主）：

| target | 路径 |
|---|---|
| `dsh` | `~/.dsh/skills/workflow-studio` |
| `claude` | `~/.claude/skills/workflow-studio` |
| `agents` | `~/.agents/skills/workflow-studio`（Codex 等跨宿主共享） |
| `gemini` | `~/.gemini/config/skills/workflow-studio` |

`--dir <PATH>` 可装到任意目录（CI / 冒烟测试用）。

**装完还缺什么**（技能只带指令和脚本，不带运行时）：

1. 宿主能执行 shell —— agent 要跑 `studio.py`；
2. Python 3.10+ —— `studio.py` 只用标准库，无 pip 依赖；
3. `rpa-core` 可用 —— 生成流程时要用 `rpa-core catalog --view agent` 取能力清单；
4. 需要可视化窗口时另装 PPX，或给 `studio.py gui` 传 `--exe`。

两个说明：`SKILL.md` frontmatter 里的 `display_name` / `description_zh` 等是 WorkBuddy
商店的扩展字段，其他宿主只读 `name` 与 `description`（DSH 上限 500 字符，当前 400），
不影响安装与运行；`dsh plugin --profile web add` 装的是 Cordis 插件（要 package.json、
要 cordis.yml 声明、要构建），**只分发技能不必走它**。

## 示例

| 示例 | 内容 |
|---|---|
| `search-and-save` | 确定性本地站点搜索并保存 JSON（CLI/E2E 主样例） |
| `baidu-news-top10` | 真实站点：百度搜索「新闻」→ 标题前 10 条 → 文本文件 |
| `api-usage` | 进程内 API 四步 + run→读证据→pause→resume |
| `windows-desktop` / `uia-desktop` | 记事本 / WinForms 桌面垂直切片 |

`browser.navigate`（打开网页）在用户真实浏览器里新建标签页（经自研扩展），输出网页对象 `sessionId` 供后续命令引用；`browser.close`（关闭网页）回收。**浏览器执行与网页捕获统一收敛到自研扩展单通道**——复用用户真实已登录的浏览器，无独立自动化浏览器。

## 测试与质量门禁

```powershell
uv run pytest                            # unit + contract + e2e（含 Windows 桌面）
uv run ruff check .
uv run python .harness/scripts/check_architecture.py   # 架构不变量（43 py / 71 manifest）
uv run python .harness/scripts/check_all.py            # 完整门禁：测试 + 架构 + 任务一致性 + 前端一致性脚本
```

前端一致性脚本（编辑器纯函数与后端语义共用同一份源码；已并入 `check_all.py`，单独跑亦可）：

```powershell
node scripts/check_param_groups.mjs     # 参数分组/字段联动判定
node scripts/check_retry_policy.mjs     # 重试能力判定 vs manifest.retryable（并交叉校验全量 manifest）
```

## 文档导航

| 文档 | 内容 |
|---|---|
| `docs/api-usage.md` | 进程内 API 使用契约 v1（四步模式 / 状态机 / 证据） |
| `docs/devserver.md` | dev server 手册：启动、端点速查、编辑器、元素捕获全流程 |
| `docs/editor-design.md` | 编辑器视觉设计（中文层、i18n、术语表） |
| `docs/manifest-param-config.md` | manifest 参数配置能力审计：现有配置全景 + 缺口清单 + 建议关键字草案 |
| `docs/capture-transport.md` | 浏览器捕获传输方案与降级链 |
| `docs/desktop_backends.md` | 桌面后端矩阵（UIA / win32） |
| `docs/m2_1_legacy_element_inventory.md` | 旧元素静态盘点（clean-room 边界） |
| `.harness/adr/` | ADR 0001-0008：replay 契约、桌面驱动、checkpoint、pause、API 面、devserver、编辑器 UI |
| `.harness/architecture.md` / `invariants.md` | 架构与不可变规则 |
| `.harness/PROGRESS.md` / `commands-plan.md` / `feature_list.json` | 进度 / 计划 / feature 门禁 |
