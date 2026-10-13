# M60 — 验证码识别指令（ddddocr 指令族）

Status: `planned`

> **本里程碑进度**：**S1 已落地**（2026-10-13，一手证据见 **§八**）；S2~S4 未启动。

> 里程碑：M60（**新能力族**，非承接任何既有里程碑）
> 日期：2026-10-13
> 范围：`commands/ocr/`（3 条 manifest）、`src/rpa_core/executors/ocr.py`（新）、
> `src/rpa_core/workers/ocr_worker.py`（新）、`src/rpa_core/cli.py`（registry 加一行）、
> `pyproject.toml`（**required** 依赖）、`src/rpa_core/devserver/static/i18n.js`、
> `tests/commands/cases/ocr.json`、`tests/contract/`、`tests/unit/`
> 台账：`feature_list.json`（新增 `ocr-captcha-recognition`）/ `PROGRESS.md` /
> `.harness/tasks/BACKLOG.md` / 当日 memory。**`project_state.json` 的排期字段本轮未动**
> （`active_*` 仍 M52，沿用 M52 条目先例：M60 是维护者口径下的**插入推进**，
> 是否让它占 active 位**待维护者拍板**）。

## 一、起因（维护者原话）

> 「评估下，能否增加dddd验证码识别相关指令」

本轮先出取证与取舍（项目纪律：动手前先只读取证）。评估结论已给（对话内）：**可行**，
且与现有 87 条命令**同构**——manifest 声明 + handler（`module:func`），执行器注册点
`src/rpa_core/cli.py:812`，现挂 `browser.playwright` / `desktop.uia` / `desktop.win32` /
`python.worker` 四个。

**维护者拍板（AskUserQuestion，2026-10-13）**：

| 问题 | 选择 |
|---|---|
| 能力范围 | **完整档**：文本识别 + 目标检测 + 字符集收敛 + 滑块匹配 + 自定义模型 |
| 依赖引入 | **进 required 依赖**（不做 optional extra） |
| 组合指令 | **不做组合**（不做 `ocr.clickByText`），各能力拆成**独立命令** |

## 二、取证（动手前，已实测）

### 2.1 ddddocr 事实核对

| 项 | 事实 | 来源 |
|---|---|---|
| 许可 / 形态 | MIT；纯 Python wheel（`py3-none-any`）；**离线本地推理，无网络/语料下载** | PyPI / 官方 README |
| 运行时依赖 | numpy、onnxruntime、Pillow、`opencv-python`(win/mac) / `opencv-python-headless`(linux) | 官方 `pyproject.toml` |
| Python | `requires-python >=3.10`（支持到 3.13）；**本仓 `.venv` = 3.12.12 ✅** | 实测 `./.venv/Scripts/python.exe -V` |
| 模型 | `.onnx` **随包**（`common_old.onnx` / `common.onnx` / `common_det.onnx`），不需下载 | 官方 `package-data` |
| 能力 | 文本识别 `classification()`、目标检测 `detection()`、滑块匹配 `slide_match()`/`slide_comparison()`；支持导入自定义 onnx 模型 | 官方 API |
| 输入形态 | bytes / base64 / 文件路径 / `PIL.Image`；单图默认上限 8MB、4096px | 官方 README |
| 平台 | **仅 64 位**；Win32 / Linux-32 不支持（本仓 64 位，无碍） | 官方 README |

`uv.lock` 现**无** numpy / onnxruntime → 全新引入，无版本冲突。

### 2.2 本仓现状（决定了接入成本）

- **图像通道不用新造**：`browser.screenshot` 与 `desktop.screenshot` 已产出 `filePath`，
  OCR 输入直接引用上一步输出即可，「截图 → 识别 → 填输入框」三步成链。
- **隔离范式已有**：`invariants.md` #9「Python 命令走 subprocess worker」+ 现成
  `executors/python_worker.py` + `workers/python_worker.py`（stdin/stdout JSON 协议）。
- `invariants.md` 15 条**无一条**禁止外部依赖；规则 5「用户 Python 不在编排进程跑」是
  **要求隔离**，不是禁止引入。

## 三、设计

### 3.1 命令清单（3 条，`ocr` 命名空间）

| 命令 | 输入 | 输出 | ddddocr 入口 |
|---|---|---|---|
| `ocr.recognize` | 图片 + 可选 ~~`charsetRange`~~（**不存在，见 §8.3 ①**）/ `modelPath` / `withProbability` | `text`（+ `confidence`） | `DdddOcr().classification()` |
| `ocr.detect` | 图片 + 可选 `modelPath` | `boxes[]` / `count` | `DdddOcr(det=True).detection()` |
| `ocr.slideMatch` | `templateImage` + `backgroundImage` | `x` / `y` / `confidence` | `DdddOcr(ocr=False, det=False).slide_match()` |

「自定义模型」**不是独立命令**，而是 `ocr.recognize` / `ocr.detect` 的可选参数
`modelPath`（ddddocr 的 `import_onnx_path`）。

**输入图片字段统一**（三条命令同一形状）：
- `imagePath`：文件路径（**主路径**，直接接 `screenshot.filePath`）；
- `imageBase64`：base64（备用，Web/扩展侧）；
- 二者**互斥**，执行器层守卫「都给或都不给 → `INVALID_INPUT`」。

**manifest 元数据**（三条同款）：
- `kind`: `query`；`risk`: `read`；`stability`: `stable`；
- `effect`: ~~`{"kind":"read","replay":"safe"}`~~ → 实取
  `{"kind":"pure","replay":"safe","idempotency":"none"}`（§8.3 ④）；`retryable`: `true`
  （纯函数；`validate_retry_policy` 只禁 unsafe replay 的命令重试）；
- `executor`: `ocr.ddddocr`；`implementation.handler`: ~~`ocr:recognize`~~ →
  实取 `ocr.ddddocr:recognize`（§8.3 ③）。

### 3.2 进程隔离（关键设计）

ddddocr 是 **C 扩展 + 全局单例模型 + 首次构造慢（秒级）**，官方明说**不要反复初始化**。

⇒ 新建 `workers/ocr_worker.py`，**常驻**（不是 `python_worker` 的「每次 spawn」形态）：
- 父子协议沿用 `python_worker` 的 stdin/stdout JSON 一请求一响应；
- worker 内按 ~~`(mode, modelPath, charsetRange)`~~ **`(mode, model_path)`** 缓存 `DdddOcr` 实例
  （`charsetRange` 不存在，见 §8.3 ①），摊薄首次加载；
- executor 持有常驻进程，`close()` / 取消 / **resume 跨进程**时重建；
- 放主进程的代价（已排除）：拖慢启动、模型加载阻塞事件循环、单例跨 run 污染。

### 3.3 接线

- `cli.py` 的 `executors` 字典加 `"ocr.ddddocr": OcrExecutor()`（**无条件**，进 required 后
  ddddocr 恒可用）；
- GUI 面板走既有 manifest 驱动（`param_form._build_field`），中文标签进 `i18n.js`
  的 `commands` / `commandFields` 两块（漂移闸 ②：每个字段都要有中文标签）；
- **Web 端不同步**（ADR 0016 冻结演进），只做 GUI + 后端能力层。

## 四、切片

| 片 | 内容 | 关键判据 |
|---|---|---|
| **S1** | 依赖进 required（`pyproject.toml` + `uv lock`）；`ocr_worker` + `OcrExecutor`；`ocr.recognize`（path/base64 → text）；registry 接线；i18n 标签；命令矩阵用例表 | 真图片识别出文本；extra/worker 生命周期；门禁四契约全绿 |
| **S2** | `charsetRange`（`set_charset_range`）+ `withProbability`；`ocr.detect`（bbox + count） | 字符集收敛真的生效（对照：给错字符集必失败/降精度）；detect 输出形状 |
| **S3** | `ocr.slideMatch`（双图输入 → 缺口坐标） | 双图互斥/缺失守卫；坐标落在图内 |
| **S4** | `modelPath` 自定义模型；文档（`docs/ocr-commands.md`）；四处台账同步；full gate | 自定义模型覆盖默认；FULL GATE PASSED |

**负向验证**（每片）：探针注入破坏 → 门禁必红 → 逐字节还原核 md5。判据不得恒绿。

## 五、验收口径

1. 三条命令的 `input_schema.properties` **每个参数**都被用例表显式设置过；枚举值全覆盖；
   声明了 `minimum`/`maximum` 的参数至少两个不同取值；每条 ≥3 变体、≥1 `negative: true`。
2. `ocr.recognize` 对**已知内容的测试图**识别结果**等于**该图内容（判据用固定 fixture 图，
   不依赖网络）。
3. **「至少给一个图片输入」的守卫**：都不给 → `INVALID_INPUT`；都给 → `INVALID_INPUT`
   （两条都判，分开判）。
4. 常驻 worker 在 `close()` / 取消 / `resume` 后**仍可用**（重建后识别结果一致）。
5. 模型未加载完成即被取消 → `cancelled`，且不泄漏子进程（`active_process_count` 归零）。
6. `check_error_contract.py` / `check_param_consumption.py` / `check_command_matrix.py` 全绿。

## 六、代价与风险（如实登记）

| 项 | 说明 |
|---|---|
| **依赖体积** | ddddocr + onnxruntime + opencv + 模型，解包后 ≈ **258MB**（实测：ddddocr 84.3 + cv2 112.4 + onnxruntime 41.6 + numpy 19.6，见 §8.2）；**进 required 后所有安装者与 CI 都要付这个代价**（维护者已拍板，登记备查） |
| **首次加载** | 首次构造 `DdddOcr` **1~3s**；常驻后摊薄，但第一个节点仍会被计入耗时 |
| **识别率** | 是**模型对目标验证码样式的拟合问题**（官方自称「玄学」），**不是工程能兜住的** ⇒ 文档如实写，**不承诺准确率** |
| **平台** | 仅 64 位；非支持平台应显式失败（`PLATFORM_UNSUPPORTED`），不伪装成 `EXECUTOR_FAILED` |
| **常驻进程生命周期** | 新增一类「跨 run 存活」的资源，需覆盖取消/异常/resume 三条路径，否则是新的挂账 |
| **Web 端** | 不同步（ADR 0016 冻结面），GUI 是唯一消费面 |
| **clean-room** | 无影响（外部 PyPI 包，非 `rpa_script` 导入） |

## 七、门禁清单（新命令 = 新 manifest，四契约都要同步）

- `src/rpa_core/devserver/static/i18n.js`：`commands`（标题）+ `commandFields`（每个字段中文标签）
- `.harness/scripts/check_command_matrix.py` + `tests/commands/cases/ocr.json`：命令/参数/枚举/边界/变体/负路径
- `tests/contract/`：catalog 契约 + 运行时集成（`invariants.md` #12：stable 命令三件套）
- 若新增错误码（如 `OCR_MODEL_MISSING`）→ 过 `check_error_contract.py`
- 收口：`check_all.py` 末行须为 `FULL GATE PASSED`，报绿前做负向验证

## 八、S1 落地记录（2026-10-13）

### 8.1 交付物

| 方案稿 | 实际落地 | 状态 |
|---|---|---|
| `pyproject.toml` 进 required + `uv lock` | `ddddocr>=1.5,<2` 进 `dependencies`（带「为何必须走常驻 worker」+ 体积的理由注释）；`uv lock` 已更新 | ✅ |
| `workers/ocr_worker.py`（常驻） | 已建：一行一 JSON；`_ENGINES` 按 `(mode, model_path)` 缓存；stdin 关闭即退出 | ✅ |
| `executors/ocr.py` | 已建：持常驻进程、`active_process_count`、`close()`、取消即丢弃重建 | ✅ |
| `ocr.recognize`（path / base64 → text） | `commands/ocr/recognize.json` + `cli.py` registry 接线 | ✅ |
| i18n 标签 | `i18n.js` 的 `commands`（标题）+ `commandFields`（4 个字段中文标签） | ✅ |
| 命令矩阵用例表 | `tests/commands/cases/ocr.json`（7 变体：4 正 3 负） | ✅ |
| 契约测试 | `tests/contract/test_ocr_contract.py`（9 条，含 runtime integration） | ✅ |

★ 用例表用**内联 base64**（64×26 PNG，内容 `58`）而不是磁盘 fixture：判据不依赖字体文件，
且与契约测试用的是**同一张图**，两端可对照。**没有任何 catalog 命令能生成图片**，
所以「先用截图命令造图再识别」这条链在用例表里走不通。

### 8.2 实测数字（一手）

| 项 | 实测 |
|---|---|
| `import ddddocr` / 首次构造 / 首次推理 | **1.11s / 0.13s / 0.44s**（第二次推理 **0.01s**） |
| 随包模型 | `common.onnx` / `common_det.onnx` / `common_old.onnx`（**离线，无下载**） |
| 解包体积 | ddddocr 84.3MB + cv2 112.4MB + onnxruntime 41.6MB + numpy 19.6MB ≈ **258MB**；`.venv` 301MB → 576.1MB |
| 真机端到端 | 真 `rpa-core run` 跑含 `ocr.recognize` 的流程 → `status: succeeded`、`text: "5834"`、`confidence ≈ 0.99994` |
| 识别口径 | `A1B2` → `a1B2`（**大小写不敏感**，模型行为，不是工程选择） |

### 8.3 与方案稿的偏离（逐条登记，以本节为准）

1. **`charsetRange` 不存在**：ddddocr 1.6.1 的 `DdddOcr.__init__` **没有** `charset_range` 参数，
   也**没有** `set_charset_range` 方法（`inspect.signature` 实测）。本切片**未实现**该参数，
   **S2 必须重设计**（候选口径：自建字符集表做后处理过滤 / 换 `import_onnx_path` 自定义模型）。
   方案稿 §3.1/§3.2 里的 `charsetRange` 属**未验证的设想**。
2. **`withProbability` 的输出键是 `confidence`**（不是方案稿写的 `probability`）：
   `probability=True` 的返回体里 `text` + `confidence` 才是真值，`probabilities`（按整个字符集的
   概率矩阵）与 `charset`（数千项的表）是**单张 64×26 图即可达数 MB** 的附带品 ⇒ **绝不透传**，
   判据另钉「序列化结果 < 4096 字节」。
3. **`implementation.handler` 实取 `ocr.ddddocr:recognize`**（方案稿写 `ocr:recognize`）：
   与 `executor` 同名便于目视对齐；`check_architecture.py` 只校验 `module:func` 形状，都合法。
4. **`effect.kind` 实取 `pure`**（方案稿写 `read`）：语义是「同输入必同输出、不触碰外部状态」，
   `pure` 更准。`replay: safe` + `retryable: true` 与方案稿一致。
5. **worker 缓存键 `(mode, model_path)`**（同 ①）。
6. **解包体积 258MB**（方案稿 §六写「300MB+」）：结论不变——进 required 的代价照付。

### 8.4 ★ 本片撞出的两条门禁漏检（已修 + 已负向验证）

1. **`ocr.ddddocr` 没登记进 `EXECUTOR_FILES`** ⇒ `check_param_consumption` 与
   `check_error_contract` 都把它算成 `skipped`：门禁 rc=0、汇总行照报 PASS，而这条命令的
   **参数消费与错误码从来没被检查过**。「跳过」不是「通过」。补登记后 `checked 77→78`、
   `skipped 1→0`。
2. **判「本期未实现」的口径比它自己 docstring 写的宽**：两条门禁都写「**分支体**里出现
   `COMMAND_NOT_FOUND` 即整条豁免」，实现却是 `if "COMMAND_NOT_FOUND" in ast.unparse(node)`——
   `node` 是**整条 If**，`orelse` 一并算了进去。于是
   `if cmd == "ocr.recognize": … else: return COMMAND_NOT_FOUND` 这种**最自然的分派写法**
   会让 `ocr.recognize` 被**静默豁免**（门禁照报 PASS、覆盖归零）。
   已改为只看 `node.body`；对既有三个 `browser.*` 未实现命令**零影响**（它们的
   `COMMAND_NOT_FOUND` 本就在分支体里）。

★ 顺带定下一条**实现侧形状约定**：worker 的分派写成**显式 `if command_id == "<id>"` 链**
而不是 `_HANDLERS` 注册表——注册表分派下两条门禁「看得见命令、看不见契约」，
`checked` 会 +1 而实际**零覆盖**（假绿）。分支体里同时写上「该命令读取的参数」与
「能返回的错误码」，错误码走 `ErrorCode.X`（不写裸字符串）。

### 8.5 负向验证（`.harness/spike/probe_m60_ocr_gates_negative.py`）

四向全部落在预期，注入逐字节还原核 md5：

| 向 | 注入 | 期望 | 实际 |
|---|---|---|---|
| ① | worker 把 `inputs.get("withProbability")` 读成 `…X` | 参数门禁红并点名 `withProbability`；错误门禁不受影响 | ✅ |
| ② | 分支体里 `INVALID_INPUT` → 未声明的 `CAPABILITY_DENIED` | 错误门禁红并点名该码；参数门禁不受影响 | ✅ |
| ③ | 把兜底写成 `else: return COMMAND_NOT_FOUND` | 修复后**不再**豁免（计数与未实现名单均不变），并直接算出「旧口径本会豁免 = True」把机制钉死 | ✅ |
| ④ | `browser.py` 三个未实现命令的 `COMMAND_NOT_FOUND` 换成别的码 | 不再豁免、必须真被检查（红灯点名三条命令） | ✅ |

探针自带三道自保：起手**基线对照**（干净树上两条门禁必须 PASS 且 0 skipped，不干净就硬失败退出，
兼作「上轮注入残留」的哨兵）· 注入包 `try/finally` 还原并核 md5 · 调用侧输出**重定向到文件**
（不接管道，避免 SIGPIPE 打断还原）。

### 8.6 门禁

- `check_all.py` 末行 **`FULL GATE PASSED`**、rc=0；pytest **1645 passed / 0 failed / 21 skipped /
  2 xfailed**；`ruff check .` → `All checks passed!`；五组静态门禁逐条 rc=0；
  `check_command_matrix` 已校验 **87 条命令**。
- ★ **全量门禁先抓出 3 条「硬编码事实」的既有判据**（新命名空间一进来就红），已按事实源修：
  - `gui/app.py` 的 `NAMESPACE_LABELS` / `NAMESPACE_ORDER` 补 `ocr`（**唯一来源**，Web 侧没有这张表）；
  - `tests/unit/test_protocols.py` 的 `assert len(first) == 86` → 改为从 `commands/**/*.json`
    **现算**（写死数字每加一条命令都要改，而这条判据的本意是「读盘没漏文件」）；
  - `tests/contract/test_gui_smoke.py` 注释里的「4 个命名空间」去掉硬编码。
- 环境级噪声（如实记）：本机 pytest 收尾阶段的临时目录清理会撞 WorkBuddy 的批量删除守卫
  （`SystemExit: 1` 落在 atexit，rc 不变）；**另记一个自找的坑**——`--basetemp` 指到**仓内**
  目录会让**第二次**运行 pytest 整体 ERROR（它先 `rmtree` 那个目录）。
- **提交后同树复跑一次**（`e59f3bf` 之后）：pytest `1 failed, 1644 passed, 21 skipped, 2 xfailed`。
  唯一红是 `tests/contract/test_gui_node_edit.py::test_delete_suppressed_while_param_editor_focused`
  ——已登记的环境级 flaky（offscreen Qt 焦点竞争，M57 条目录过同一条）；**单文件复跑两次各
  `25 passed` / rc=0** ⇒ 判 flaky 而非回归（M60 未触碰该模块）。两次全量的 `passed + failed`
  之和一致（1645 = 1645）。

### 8.7 未做（明确留给后续切片）

- S2：`charsetRange` 重设计（见 8.3 ①）+ `ocr.detect`；S3：`ocr.slideMatch`；
  S4：`modelPath` 自定义模型（字段已在 manifest 里，但**「覆盖默认模型」的判据未做**）+
  `docs/ocr-commands.md` + 四处台账 + full gate 收口。
- **模型路径非法时的错误码**现落到 `EXECUTOR_FAILED`；方案稿 §七 提过 `OCR_MODEL_MISSING`，
  本轮**不加新码**（能用既有码就不扩枚举），S4 再定。
- **识别率不承诺**（模型对样式的拟合问题，官方自称玄学）——文档如实写。
- 验收口径 4 的 **`resume` 跨进程重建**目前只由「`close()` 后重建结果一致」+「取消后无泄漏」
  覆盖，**编排层 resume 的真机路径未单独取证**，属 S4 收口项。
