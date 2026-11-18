# M75：图像/OCR 定位后端（自绘控件能力的轻量切口）

Status: `done`

> **2026-11-16 立项**。承接 M74「不在范围」里的能力层欠账：维护者定调「元素捕获粒度细到
> 微信控件」是目标效果，而现有 `_locator_candidates` 只从 UIA 语义属性生成候选，自绘/DirectX/
> canvas（含微信自绘区）拿不到 `controlType`/`automationId`/`name` ⇒ 候选为空，根本无法定位。
>
> 本任务给「UIA/win32 拿不到语义的自绘控件」补一条**显式选择**的图像/OCR 定位路径，是
> ADR 0003「分层边界」第 4 层（图像/OCR 与坐标动作）的正式落地。落 `planned`，不占 active 位
> （M52 仍主线）。
>
> 前置：M74（统一校验编排，已完成，本任务 S3 接它的 `verify_element` 门面）、
> M60（ddddocr 三条命令）、M47.9（Z 序）。约束：**ADR 0003**（禁止 UIA→坐标静默降级）、
> **ADR 0016**（GUI 主力 / Web 冻结）、**ADR 0019**（Qt 源码冻结）、**ADR 0022-R3**（分支共享能力层）。
>
> ★ 范围边界：**不做 HLay 式 DLL 注入造控件树、不做 Java hook、不做远端 AI 语义定位**——
> 那三项是更重的独立能力，本任务只做「截图 → 图像/OCR → 屏幕坐标矩形」这条最短路径。
>
> **2026-11-16 维护者拍板（两条设计取向，动手前留痕）**：
> ① **路线 A**（完整后端）：扩 `DesktopLocator` + 新增 `desktop.image.*` executor/命令族 + 全量同步，
>    不走 B 的「纯 Python 能力层、不接命令」。S4 不再是阻塞拍板点，改为「实施 GUI 接线」。
> ② **GUI 走 `ppx-only` 分支**：GUI/i18n 侧的接线（新命令中文标签、属性面板字段、卡片展示）
>    在 `ppx-only` 分支的 PPX 原型里做，**不在 `xhs-workbench` 补 Qt GUI 源码**（Qt 冻结 ADR 0019）。
>    两分支 `i18n.py` 完全一致、`i18n.js` 共享，新命令中文标签落 `i18n.js` 即可两分支共享；
>    PPX 通过 RPC 薄层消费能力层（`api.py`），**能力层不得反向依赖 `ppx_py`**。
>    能力层（model/executors/commands/cli/门禁登记）留 `xhs-workbench`，靠显式 cherry-pick 同步到 ppx。

## 一、起因与可验证目标

| 目标 | 可验证形态（不是口号） |
|---|---|
| 自绘控件能定位 | 给一张目标控件模板/关键词，从**指定窗口截图**中稳定算出 `{left, top, width, height}`（屏幕绝对坐标），唯一命中或明确报多匹配/零匹配 |
| 不静默降级 | UIA/win32 找不到时**不自动**改用图像；必须由 workflow/元素显式选 driver，且证据里记录实际使用层（ADR 0003） |
| 与 M74 衔接 | 图像定位结果能作为一次「校验命中」进入 `verify_element`，拿到统一的 `count/windowHandle/rect/error` 形状 |

## 二、取证（动手前现状，逐条实测）

> 行号为 2026-11-16 取证快照，实现时以符号名为准（行号会漂）。

### 2.1 后端不是枚举，执行分派也不看 backend（纠正前期口径）

- `model/desktop.py:7`：`DesktopBackend = str`（类型别名，**不是 Enum**）；白名单在
  `require_identity` 验证器 `:216-217`：只允许 `uia`/`win32`，否则 `ValueError`。
- `model/capture.py:31`：`ElementDescriptor.kind: Literal["browser","desktop"]`——**只有两值**，
  没有 image 预留位。locator **没有 `kind`/`rect` 字段**：`rect` 在元素 `metadata`
  （`capture/desktop_agent.py:536-558`）。
- 执行分派靠 **manifest 的 `executor` → `ExecutorRegistry`**（`cli.py:755-767`），
  两个现有 executor 的 `_find`（`executors/desktop.py:968-984`、`desktop_win32.py:889-901`）
  **都不读 `locator.backend`**。⇒ 新增后端 ≠ 在 `_find` 里加分支，而是新增 executor 类 + 命令族。

### 2.2 自绘控件候选为空的根因

`capture/desktop_agent.py:584-630` `_locator_candidates` 三层候选只从 `control_type` /
`automation_id` / `name` 生成，backend 硬编码 `"uia"`；`:655-699` `_evaluate_candidates`
写死 `"kind": "uia"`（`:693`）。自绘控件三者全空 ⇒ 三层 if 全不成立 ⇒ 候选真为空。
（注意：只拿到 `controlType` 时仍会产出一条弱候选，所以「自绘候选为空」要限定为「三语义属性全无」。）

### 2.3 已具备 vs 从零建（决定切片成本）

| 能力 | 状态 | 证据 |
|---|---|---|
| 窗口/屏幕截图 | **已具备，可直接复用** | `capture/screen_shot.py:81-91` `_ScreenAPI` Protocol（替身接缝）、`:118-133` `_WinScreen.grab`（PNG bytes）、`:381-408` `grab_window`（dataUrl+windowOrigin+imageSize+windowRect） |
| OCR 识别/检测 | **已具备（M60）** | `workers/ocr_worker.py:54-92` `_engine`、`:151-165` `detect` → `{boxes:[{x,y,width,height}],count}`（坐标形状已对齐 `browser.getPosition`）、`:95-111` `_decode_image`（只收 imagePath/imageBase64，不收 hwnd） |
| cv2 / 模板匹配 / SIFT / ORB | **从零建** | 全仓无 import；opencv 仅 ddddocr 传递依赖（`pyproject.toml:10-13` 注释，win_amd64 解包约 250MB），未显式声明 |
| 「截图→识别→回坐标」桥接 | **从零建** | ocr_worker 只吃文件/base64，不接窗口句柄；无坐标回写定位器的现成路径 |
| 坐标点击命令 | 不存在 | 命令族只有 browser/data/desktop/desktop_win32/ocr/workflow；`docs/element-mvp-boundaries.md:26,65,68` 明写「无 DOM 控件只有元素级事件，没有坐标点击」 |

### 2.4 ADR 0003 硬约束（写验收必须带）

`.harness/adr/0003-windows-desktop-driver.md:49-53`：图像/OCR 是第 4 层；**禁止从 UIA 静默降级到坐标点击**，降级必须由 workflow 显式选择不同 locator/driver 并在证据中记录实际使用层；图像库、截图 provenance、DPI/坐标系、置信度、模板版本属于「后续独立里程碑」——本任务正是那个里程碑，必须把这些要素一并定义。

### 2.5 GUI 源码树不完整 ⇒ 新增命令是阻塞路径

`src/rpa_core/gui/` 当前只剩 `__init__.py` / `i18n.py` / `i18n.js`；`devserver/static/i18n.js` 不存在。
新增命令要动四契约（`test_gui_element_editor` / `devserver/static/i18n.js` /
`test_editor_i18n` / `test_extension_installer`），**GUI 侧缺源码，无法落地**。
⇒ M75 默认走「不新增命令」的能力层路径（S1~S3），命令化单列为 S4 的显式拍板点。

## 三、路线取舍（S1 前需拍板，不替维护者定）

| 路线 | 做法 | 成本 / 障碍 |
|---|---|---|
| **B（推荐先行）** | 图像定位**能力层**：纯 Python 函数 + 契约测试，产出矩形，**不新增命令 manifest、不碰 GUI** | 最小切口，绕开 GUI 障碍；先证明核心假设「图像能稳定定位自绘控件」 |
| A（完整后端） | 扩 `DesktopLocator` + 新增 `executors/desktop_image.py` + `commands/desktop_image/` + cli 注册 + 四契约 + 门禁登记（约 10 个同步点，见下） | 被 GUI 源码缺失阻塞；且工作量大、易造第二权威 |

**已拍板 A**（⑨ 走 ppx 分支，见文首维护者拍板）。需同步的点（逐条不可漏）：
① `model/desktop.py:216` 白名单 + 新字段 + penalty
② 候选生成 `desktop_agent.py:584-630/655-699`（`:693` 写死的 `"kind":"uia"` 参数化）
③ 新 executor 类 ④ `commands/desktop_image/*.json`（executor/kind/effect/errors/input/output/manifest 必填）
⑤ `cli.py:755-767` 注册 ⑥ `tests/commands/cases/desktop_image.json`（命令矩阵台账，现 86 条全有表）
⑦ `check_param_consumption.py:80-91` `EXECUTOR_FILES` 登记（**漏登=零覆盖假绿**）
⑧ `check_error_contract.py` 错误码 ⑨ i18n.js 中文标签（共享，两分支通吃）+ ppx 原型卡片/字段接线（ppx 分支）
⑩ `docs/desktop_backends.md` + ADR 0003 分层说明。

**显式选择机制**（S2 定）：图像后端如何在元素/workflow 层被「明确选中」而不静默兜底？
候选方案：元素 descriptor 加 `preferredBackend` 字段 / 独立 `kind="image"` / workflow 显式命令选择。
判据必须包含「UIA 找不到时不自动调图像」的负向。

## 四、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| **S0** | ~~路线拍板~~ **已拍板 A + GUI 走 ppx 分支**；成像口径定义：坐标系（屏幕绝对）、DPI 缩放、置信度阈值、模板 provenance 字段 | 拍板记录已写进本任务单文首；坐标系用已知 bbox 换算做等值断言（不是 `>=`） | done |
| **S1** | 图像定位能力层 `capture/image_locator.py`：复用 `grab_window` 截图 → OCR `detect` 文本/关键词定位 + 模板匹配定位 → 输出 `{left,top,width,height}` + 命中数 + 置信度 | 契约：替身注入截图与检测结果（不依赖真窗口）下，命中矩形**等值**、零命中/多命中/低于阈值三态分明、截图 bytes↔base64 复用 `ocr_worker._decode_image` 口径。负向：把阈值判据放宽为恒真必须红 | done |
| **S2** | 显式选择接线：定义「只有 workflow/元素明确选 image 才走图像定位」的开关，禁止 UIA 静默降级；证据记录实际使用层 | 契约：UIA 空命中时图像后端**不被调用**（探针计数=0）；显式选中时才调用且证据落 `usedLayer=image`。负向：删掉「显式选择」守卫必须红 | done |
| **S3** | 接 M74 `verify_element` 门面：图像命中按统一 `VerificationResult` 形状回传，`windowHandle` 取命中窗口 hwnd | 契约：`count/windowHandle/rect/error` 字段与 browser/desktop 两通道一致、无重复目标选择逻辑。负向：改坏形状/绕过门面必须红 | done |
| **S4** | 命令化（路线 A）：新增 `desktop.image.*` executor + 命令族 + 全量 10 点同步；GUI 接线（i18n.js 标签 + ppx 原型卡片/字段）在 `ppx-only` 分支做 | 第三节 10 点逐条核（`EXECUTOR_FILES` 漏登=假绿要单测）；命令矩阵用例表齐；GUI 侧在 ppx 分支的接线单独留证，能力层禁止 `import ppx_py`。负向：静默降级/漏登记必须红 | done |
| **S5** | 收口：台账同步（PROGRESS / feature_list / STATE / 当日 memory）+ 全门禁 | 末行 `FULL GATE PASSED` + 退出码 0；负向验证逐字节还原核 md5 | done |

## 五、判据总则（每片都要过）

1. 门禁末行 `FULL GATE PASSED` + 退出码，只认这一条；报绿必做负向验证（对照跑 /
   `N failed` 且无 `N error` / 超时报出 / 逐字节还原核 md5 / 哨兵自检 / 锚点唯一性）。
2. 坐标、置信度、命中数一律**等值断言**，不写 `>=`（阈值松=假绿，用户 memory 踩过）。
3. 覆盖**用户实际走的路**：从 `verify_element` 门面进，不是只测纯函数（M74 教训：纯函数层绿≠接线层绿）。
4. 单一权威：截图只走 `screen_shot.py` 现成函数，OCR 只走 `ocr_worker` 现成引擎；
   不建第二套截图/OCR 实现，也不在 `_locator_candidates` 里偷偷塞图像分支。
5. 显式选择守卫生死线：任何「UIA 没找到就自动试图像」的降级路径都必须被负向验证打红（ADR 0003）。
6. 新增命令若拍板，走第三节 10 点清单 + 四契约；`EXECUTOR_FILES` 漏登=假绿，必须单独核。
7. 沙箱环境：计时用例假红先 `--basetemp=<系统temp>` 复跑；长命令前台 + 真豁免（回显 `Sandbox bypassed`）。

## 六、不在本任务范围（登记 BACKLOG）

- **HLay 式 DLL 注入造控件树**：影刀 `HLayFrontend.dll`（`SelectorFromPoint`/`HtmlFromPoint`/
  `QuerySelectorAll`/`GetAllAttributes`）给自绘界面凭空造 DOM，是最强但也最重的路径；
  自建等价物需 native 开发 + 注入稳定性验证，另立里程碑。
- **Java hook**（`ShadowBotJavaBridgeHook.dll` + `JavaBridge`）、**远端 AI 语义定位**
  （`ISelectorAnalyzeEngine` 算法在服务端，本地只是调用壳）——同样是独立能力线。
- **GUI 源码恢复**（`devserver/static/i18n.js` 等缺失文件）：属宿主壳问题，不在能力层任务内；
  S4 若因此阻塞，按前置阻塞登记。
- **操作前自动前置目标窗口**（执行层）、**XPath 后端（M69-S2）**：各有既定归属，不重复立。

## 七、风险与已踩坑

- **ddddocr 体积**：win_amd64 解包约 250MB（`pyproject.toml:10-13`）；模板匹配若显式引入 opencv
  要评估体积/依赖声明，别假设「传递依赖存在」就能 import（当前全仓无 cv2 import）。
- **DPI / 缩放 / 主题 / 遮挡**：图像坐标随这些变量漂移，S0 必须把坐标系与 provenance 定义清楚，
  否则「同一模板不同 DPI 命中不同矩形」会变成假判据。
- **GUI 四契约阻塞**（2.5）：新增命令路径当前走不通，别在 S1~S3 偷偷加 manifest 制造假绿。
- **第二权威**：图像定位若塞进 `desktop_agent._locator_candidates`（候选权威）或 executor `_find`，
  会造出两套定位逻辑；本任务以「独立能力层 + 门面接线」为边界。
- **编辑态/运行期漂移**：元素编辑态字段（如 proposal）与运行期定位是两条路，新增字段别只改一边。
