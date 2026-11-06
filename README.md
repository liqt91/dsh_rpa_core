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
# ① 技能那一半（Node，零依赖）
npm i -g github:liqt91/dsh_rpa_core#ppx-only
rpa-skill install --target dsh

# ② 运行时那一半（Python）——也可以全走远端，不必先 clone
uv tool install "git+https://github.com/liqt91/dsh_rpa_core.git@ppx-only"
#   没有 uv 时：pip install --user "git+https://github.com/liqt91/dsh_rpa_core.git@ppx-only"
#   依赖里有 ddddocr（OCR 指令族），解压后约 250MB，装前先有心理准备

# ③ 自查 / 卸载
rpa-skill doctor           # 装完先跑这个：它会告诉你运行时连没连上
rpa-skill status
rpa-skill uninstall --target dsh
```

> 顺序无所谓：`②` 装完再跑一次 `rpa-skill install --force` 让安装器重新探测即可。
> `npx <pkg> install` 这种写法也能用，但参数归属在各 npm 版本上不完全一致
> （`install` 可能被当成包名），所以文档里只给 `npm i -g` 与 `node bin/…` 两种确定能跑的形态。
> 包未发布到 npm registry，所以只能写 `github:<owner>/<repo>#<branch>` 这种 git 源形式。

`npm i -g github:…` 实际从 `codeload.github.com/<owner>/<repo>/tar.gz/<sha>` 拉一个 tarball。
走代理的网络里这条大响应偶发 `ECONNRESET`（npm 默认只重试 2 次），报的是 network error，
跟包内容、命令写法都无关。按下面顺序试，三条都在 Windows / npm 11.6.2 / Node 24 上实测过：

```powershell
# ① 先加大重试重跑原命令（多数情况这次就过）
npm i -g --fetch-retries=5 github:liqt91/dsh_rpa_core#ppx-only

# ② 仍红：把 npm 指到本机在监听的代理端口（7890 只是示例，先 netstat 确认）
netstat -ano | findstr "127.0.0.1:7890"
npm i -g --proxy http://127.0.0.1:7890 --https-proxy http://127.0.0.1:7890 `
  --fetch-retries=5 github:liqt91/dsh_rpa_core#ppx-only

# ③ 仍红：绕开 codeload，手工下好 tarball 再本地装（PS 里 curl 是别名，要写 curl.exe）
curl.exe -L --proxy http://127.0.0.1:7890 -o rpa-skill.tgz `
  https://github.com/liqt91/dsh_rpa_core/archive/refs/heads/ppx-only.tar.gz
npm i -g .\rpa-skill.tgz
```

代理是**本机网络环境**，不是这个包的要求：环境变量 `HTTPS_PROXY` 会让 npm 自动走代理，
所以原命令里看不见它；能直连 GitHub 的机器以上全都不用加。

**技能依赖 `rpa-core` 的 Python 运行时，而 npm 包里不带它**——这是分发包最容易断的一环：
技能被拷进宿主目录后，原仓库的 `src/`、`editor/`、`extension/` 都不在了，裸命令
`rpa-core catalog` 会 `command not found`。所以安装器会现场探测「哪个解释器能 import
`rpa_core`、源码根在哪」，把结果写进技能目录的 `runtime.json`，并把文档里的
`{{PYTHON}}` / `{{RPA_CORE}}` 换成绝对路径。

实测的三种运行时形态，三种都能装（区别只在于 `runtime.json` 里记什么）：

| 形态 | 怎么来的 | 文档里的命令 |
|---|---|---|
| `src` | 从仓库跑（开发态） | `<python> <技能目录>/scripts/rc.py …` |
| `module` | `pip install` / `uv tool install` | `<那个解释器> <技能目录>/scripts/rc.py …` |
| `exe` | PATH 上已有 `rpa-core.exe` | `"<rpa-core.exe>" …`（不绕 rc.py，免得白要一个 python） |

探测不到时**安装不会失败**（文件是好的），但会在 `runtime.json` 缺席 + stderr 上报警，
`rpa-skill doctor` 同步报红。补上只需重装一次并指一下仓库根：

```bash
rpa-skill install --force --rpa-core-dir <rpa_core 仓库根>
# 或：--python <能 import rpa_core 的解释器>
```

安装器做四件事：拷目录、探测运行时并写 `runtime.json`、替换 `{{SKILL_DIR}}` /
`{{PYTHON}}` / `{{RPA_CORE}}` 三个占位符（agent 的 cwd 通常是项目根，相对写
`scripts/studio.py` 一定找不到）、生成 `skill.json`。
它不跑 postinstall、不写注册表；`--force` 时旧目录挪到同级的 `.rpa-skill-backups/` 并给里面的
`SKILL.md` 改名 `.replaced` 保留（不删除）——备份不能留在技能扫描目录里，否则宿主会看到两个同名技能。

### 编辑窗口（可选，只有 Windows）

窗口是仓库里的 PPX 应用（`.harness/demo/ppx_probe/app`），wheel 与 npm 包都不带。
它单独打成便携包放在 GitHub Release，安装时按需下载（约 16MB）：

```bash
rpa-skill install --target dsh --with-editor   # 或装完技能后单独补：rpa-skill editor install
rpa-skill editor status
rpa-skill doctor                               # 窗口装好时 editor 那行是 [PASS]
```

- 落到 `~/.rpa-core/editor/current/`，清单 `~/.rpa-core/editor.json`（窗口的工作区与
  `rpa-core` 可执行文件都从这份清单读）。`RPA_HOME` 换根目录，`--editor-home` 换安装位置。
- **安装路径不能含中文**：实测 `pythonnet` 加载 `Python.Runtime.dll` 时会把路径里的中文编码弄坏。
- 没有 Linux/macOS 产物，其它平台返回 `EDITOR_UNSUPPORTED`（SKIP，不算安装失败）。
- 离线 / 内网：`--editor-zip <本地 zip>` 或 `--editor-url <镜像>`；release 的 `.sha256`
  旁挂文件会自动校验，也可 `--sha256 <hex>` 手工指定。
- **连 GitHub 需要代理时**：`editor install` 用 Node 内置 `fetch` 下载，和 npm 不是同一套配置。
  部分环境里它不读 `HTTP_PROXY`，会报 `UND_ERR_CONNECT_TIMEOUT`；Node 22 上设
  `NODE_USE_ENV_PROXY=1`（有一条实验特性警告，不影响结果），Node 24 默认可用（v24.13 实测
  不设也能通）。仍不通就手工下好 zip 与同名 `.sha256` 后走 `--editor-zip`。
- 不装窗口照样能生成流程、跑流程、用 `rpa-core capture` 拾取元素；少的只是「人在窗口里改 + 点选」。

维护者发版（推荐走 CI）：仓库有 `.github/workflows/editor-release.yml`，在 Windows
runner 上装 PPX / pnpm、构建前端与便携包、校验产物，再创建 Release 并上传 zip + `.sha256`。
一个 tag 就够：

```bash
git tag editor-v<版本>
git push origin editor-v<版本>
```

> tag 必须与 `.harness/demo/ppx_probe/app/ppx.toml` 的 `version` 一致 ——
> `verify_editor_artifact.py` 会在上传前对齐 tag / 包内版本 / exe / sha256，不一致直接失败。

改了构建链（构建脚本 / 前端 / 命令清单 / workflow 本身）推到 `ppx-only` 时，会先跑一遍
**只构建、不发版** 的自测；也能在 Actions 页手动 `workflow_dispatch`。失败时构建日志尾部会以
`::error::` 注释公开 —— 匿名拿不到 job 日志也能排错。CI 把 ppx-py 的解释器路径写进
`RPA_PPX_PYTHON` 交给构建脚本；本地不设这个变量时，脚本自己去 `uv tool dir` 里按结构找，
不依赖 tool 目录叫什么名字。

> Windows runner 是 en-US，Python 的 stdout 默认 **cp1252**，构建脚本里第一句中文日志就会抛
> `UnicodeEncodeError` —— 本机是中文 locale，所以这种失败只会在 CI 上出现。workflow 设了
> `PYTHONIOENCODING=utf-8`；脚本自身另有 `errors=replace` 兜底，并给生成 spec 的子进程强制
> UTF-8（那边本来就按 utf-8 解码）。三层互为兜底，别只删其中一层。

本地手工发版（没有 CI 时）：

```bash
./.venv/Scripts/python.exe .harness/scripts/build_editor.py --release
./.venv/Scripts/python.exe .harness/scripts/verify_editor_artifact.py
gh release create editor-v<版本> \
  dist/RPA-Editor-win64.zip dist/RPA-Editor-win64.zip.sha256 \
  --title "Editor <版本>" --notes "编辑窗口 <版本>"
```

资源名**固定不带版本**：安装器走 `releases/latest/download/<asset>`，省掉一次 API 解析；
版本号由包内 `editor-version.json` 提供，安装器也拿它当结构判据（随便一个带 exe 的 zip
不该被当成窗口装上）。构建脚本不调 `ppx build` —— 它会额外要求 Inno Setup 做安装包 ——
而是让 ppx 生成 spec 后直接跑 PyInstaller，只取那个便携目录。

> 许可：PPX 是 AGPL-3.0。随 Release 分发它的 PyInstaller 产物时，Release 说明里请附上
> PPX 源码链接与本仓的构建说明（`build_editor.py`），不要只给二进制。

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
