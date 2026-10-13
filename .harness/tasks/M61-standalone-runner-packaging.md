# M61 — 独立运行器打包（Nuitka，脱离仓库发行）

Status: `planned`

> 里程碑：M61（**调研已收口，未启动**；本文件是留档，不是开工令）
> 日期：2026-10-13
> 范围（若启动）：`pyproject.toml` / 新增构建脚本与 Nuitka 配置 / 两处子进程启动改造 /
> 新增 ADR（放行 `installer`）/ 打包冒烟口径（独立于默认门禁）
> 台账：`project_state.json` / `features` / `PROGRESS.md` / 当日 memory

## 一、起因（维护者原话）

> 「本项目的流程，具备导出为可执行文件后，脱离本项目运行吗？有什么成本？这样使用人不需要安装完整的软件就能用」
>
> 后续澄清：「**我说的脱离运行是指纯运行，不带编辑、修改功能**」
>
> 收口决定：「功能先不用做……没有的话调研结束落到文档后续再启动，**先做 dddd 集成**」

## 二、结论

**技术可行。** 且比同类 Python 桌面项目便宜——因为浏览器腿**没有浏览器内核要分发**。

**范围 = 纯运行器**：只保留跑流程所需（引擎依赖一个都不减），**不含可视化编辑器**。

**但必须先放行 scope 排除项**：`installer` 在 `README.md:21` 与 `scope.out` 中是明确排除项，
**ADR 0006 §3** 原文写着「继续排除：…、安装器——**各自需要新的 ADR**」⇒
按本仓规矩，启动前**必须先写一份新 ADR**。

## 三、取证（2026-10-13 实测，非推测）

### 3.1 决定性实测：运行路径零 GUI

| 实测 | 结果 |
|---|---|
| `import rpa_core.cli` 后 `sys.modules` 里 `PySide6`/`qdarkstyle`/`shiboken6`/`qtpy` | **0 个** |
| 真跑 `data.setVar` → `data.log`（`status: succeeded`, rc=0）加载的第三方顶层包 | **22 个，无一个 Qt** |
| rpa_core 子包加载面 | `catalog / cli / compiler / control_channel / devserver / executors / extension_* / local_transport / model / runtime`（**无 `gui`**） |

**结构性证据**：`cli.py` 里对 `rpa_core.gui` 的导入只有 3 处、**全在 `_cmd_gui` 函数体内**
（`:584/594/595`）；`cli.py:8-15` 顶层注释已说明「把 executors/capture 导入移到子命令内，
保证 GUI 路径不碰 pywinauto 的 COM 初始化」⇒ **运行链与 GUI 链本来就是两条独立设计**；
`_cmd_gui` 已有「缺 gui extra」的可读错误分支 ⇒ 纯运行器敲 `gui` 会自然报缺依赖、不崩。

### 3.2 运行必需的依赖（实测 22 个顶层包，一个都不能减）

| 类别 | 包 | 为什么省不掉 |
|---|---|---|
| 模型与校验 | pydantic / pydantic_core / jsonschema / jsonschema_specifications / referencing / rpds / attrs / attr / annotated_types / typing_extensions / typing_inspection | workflow 与 manifest 的类型校验 |
| **桌面操作** | pywinauto / comtypes / pythoncom / pywintypes / win32api / win32con / win32gui / win32gui_struct / win32ui / win32process / win32event / pywin32_bootstrap / pywin32_system32 / commctrl | `desktop.uia` / `desktop.win32` 两条腿 |
| 截图 | PIL | `desktop.screenshot` 回落路径 |
| 传递依赖 | six | 上述包带进来的 |

★ **即使跑一条纯 data 流程，pywinauto 也会被加载**（registry 装配全部 executor）⇒ 它是引擎的一部分，不是「用才装」。
**仅 PySide6 + QDarkStyle + qtpy + shiboken6（207MB）可整个去掉。**

### 3.3 体积（实测）

| 项 | 数值 |
|---|---|
| `.venv` 全量 | **301 MB**（其中 PySide6 **207 MB**） |
| 纯运行器依赖侧 | 约 **35 MB**（PIL 15 / pywin32 系 ~8 / pywinauto 2.8 / comtypes 2.7 / pydantic_core 5.4） |
| 估计 one-dir 产物 | 纯运行器 **约 90 MB**；完整包 **约 300 MB** |

### 3.4 ★ 五处 `sys.executable + "-m"`（frozen 后全部失效）

编译后的 exe **不认 `-m`** 这个解释器参数 ⇒ 下列全部要改：

| 位置 | 用途 | 纯运行必需 |
|---|---|---|
| `executors/python_worker.py:25-28` | `sys.executable -m rpa_core.workers.python_worker` | ★ **必需**（invariants #9） |
| `extension_installer.py:692` | native host = `sys.executable` 同目录的 `rpa-core-ext-host.exe` | ★ **必需**（Chromium 不解析 manifest 参数） |
| `capture/desktop.py:58` | 桌面捕获子进程 | 否（设计期） |
| `capture/desktop_agent.py:895` | `-m rpa_core.capture.desktop_click_hook` | 否（设计期） |
| `devserver/runs.py:56/156/183` | `-m rpa_core.cli run/resume` | 否（纯运行直接调 cli） |

⇒ **必改两处**：① `python_worker` 改「同一 exe + argv 分发」；② native host 第二入口，同目录、无参。

### 3.5 ★★ 用户 Python 执行环境缩水（语义代价，非配置问题）

`python.worker` 的语义是「用 Python 跑用户写的代码」。现状子进程 = `.venv/Scripts/python.exe`
⇒ 用户 `pip install` 的包都能 import。**frozen 后子进程 = exe** ⇒ 只能 import 编进 exe 的包。

**实证**：`pygments 2.21.0` 确在 `.venv/Lib/site-packages/pygments/`（现在可 import），
但跑流程时检测 `pygments 在运行路径里? False` ⇒ 引擎自身不用它 ⇒ **打包后 `import pygments` 会 `ModuleNotFoundError`**。

**处置三选（待维护者拍板）**：(a) 如实限制 + 文档写明内置包清单；(b) 预编常用包（+几 MB）；(c) 回落系统 Python（破坏「免安装」）。

## 四、★ ddddocr 集成（M60）对上述结论的影响

**维护者要求**：「再评估下集成 dddd 是否打破上述结论」。

**结论：不打破「运行路径不需要 PySide6」，但推翻了「纯运行器体积小」这一直觉，并新增一类打包适配点。**

### 4.1 不受影响

- **「不需要 PySide6」仍然成立**：OCR 是**运行期能力**，命令经 `workers/ocr_worker.py` 子进程执行，
  **完全不进 GUI 链路**；`import rpa_core.cli` 的零-GUI 实测结论不变（ddddocr 不在 `gui/` 的 import 图上）。
- **打包可行性的方向不变**（仍是「依赖 + 两个入口 + 子进程改造」三件事）。

### 4.2 被推翻的直觉：纯运行器会比「现在带编辑器的完整包」还大

ddddocr 依赖的真实体积（PyPI，win_amd64/cp312，**压缩后**）：

| 包 | 压缩 | 说明 |
|---|---|---|
| **ddddocr** | **72.5 MB** | 大头是**随包的 3 个 `.onnx` 模型**（`common_old` / `common` / `common_det`） |
| opencv-python | 42.0 MB | `sys_platform == "win32"/"darwin"`（Linux 走 headless） |
| onnxruntime | 14.2 MB | 另带 `flatbuffers` / `protobuf` / `numpy` / `packaging` |
| numpy | 12.0 MB | |
| **合计（压缩）** | **≈ 141 MB** | 解包后估计 **≈ 250 MB** |

对比：**PySide6 = 207 MB**。⇒ **OCR 依赖比整个 PySide6 还重。**

| 形态 | 集成 dddd 前 | 集成 dddd 后（估） |
|---|---|---|
| 纯运行器 | ~90 MB | **~340 MB** |
| 完整包（含编辑器） | ~300 MB | **~550 MB** |

★ **后果**：集成 dddd 后，「不带编辑器的纯运行器（~340MB）」**比现在这个带编辑器的完整版（~300MB）还大**。
「纯运行器」的性价比优势基本消失——将来若真要打包，**值得重新权衡是否干脆连编辑器一起打**。

### 4.3 新增的打包适配点

1. **第 6 处 `sys.executable + "-m"`**：`workers/ocr_worker.py` 也是子进程（M60 §3.2 设计为**常驻**形态）
   ⇒ frozen 必改点从 **2 处变 3 处**（python_worker / **ocr_worker** / native host）。
2. **ddddocr 内部的模型路径解析**：ddddocr 按包内相对路径找 `.onnx` ⇒ frozen 后必须作为 **data 文件**
   收集且路径可达（Nuitka `--include-package-data=ddddocr`），**这是本轮未实测的未知点**。
3. **原生扩展收集**：`onnxruntime` 的 `.pyd` + 多个 DLL、`opencv` 的 `.pyd`、`protobuf`（C 扩展）
   —— 都要显式收集，否则运行期 `ImportError`。
4. **反向影响（开发态）**：dddd 进 **required** 后（M60 已拍板），开发者 `.venv` 从 301MB → **约 550MB**。

### 4.4 ★ 由此得到的一条可执行建议（给 M60）

**M60 实现 `ocr_worker` 时，就把子进程启动方式做成「exe 兼容」形态**（同一 exe + argv 分发，
而不是裸 `sys.executable -m`）⇒ 将来打包时该处**零改动**，不必回头返工。
这是一条「现在几乎零成本、将来省一次改造」的顺带收益。

## 五、未决事项（启动前必须拍板）

1. **是否新写 ADR 放行 `installer`**（ADR 0006 §3 明确要求；建议 ADR 0019）。
2. **用户 Python 环境缩水的处置**：a / b / c（见 §3.5）。
3. **打包形态**：Nuitka `--standalone`（推荐，onefile 会让 native host 每次解压）／PyInstaller／正式安装器。
4. **★ 是否仍按「纯运行器」打**：受 §4.2 影响，若 dddd 已在 required 里，「纯运行器」相对完整包
   只省约 200MB 而牺牲全部编辑能力 ⇒ **建议届时重新确认范围**。
5. 打包冒烟的口径（构建慢，**不能进默认门禁**）。

## 六、Nuitka 做法要点（备查）

- entry 用薄 `__main__` 包装，不直接指 `cli.py`。
- **最干净的排除法：构建环境不装 gui extra**（`uv sync` 不带 `--extra gui` ⇒ PySide6 不在环境里，编不进）；
  再用 `--nofollow-import-to=rpa_core.gui` 兜底（`cli.py:584` 是函数内 import，Nuitka 会 follow 到）。
- `--include-package-data=rpa_core`（`commands/`、`extension/`）+ `--include-package-data=ddddocr`（模型）。
- `--standalone`（**不用 onefile**：native host 需常驻同目录，onefile 每次解压拖慢扩展握手）。
- Windows 需 MSVC 编译器。

## 七、启动条件

维护者拍板 §五 的 1–4 后，把本任务单 `Status` 改 `active` 并交 `active_plan`。

---

**本轮（2026-10-13）状态**：调研收口、留档；**未改任何产品代码、未写入 scope**。
