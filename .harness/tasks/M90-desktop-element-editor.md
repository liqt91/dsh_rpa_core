# M90：桌面元素补上影刀式详细编辑框

Status: `done`

> **2026-11-18 用户诉求**：「网页元素有捕获后的详细编辑框了，桌面元素还没有，要补上。」
> 这是 M89 的延续——M89 明确留了「桌面只有定位 JSON 文本框」的边界，本次填掉它。

## 1. 取证

影刀侧（`D:\Users\Administrator\Documents\代码\rea-yigndao`，只读）：

- `影刀元素编辑方式报告.md` + `src/tools/ShadowBot.UIAutomation.Tools.LocalizationResources.Strings.resx`：
  桌面与网页共用 `SelectorEditorView`（CefSharp 网页编辑器），形态是「元素名称 + 编辑/元素预览双 Tab
  + 左侧层级树勾选 + 右侧五列表格（启用｜元素节点｜属性名称｜匹配方式｜属性值）
  + 实时预览 + 重新捕获/校验元素(F7)/保存并继续(F2)/完成」。
- `src/uiat/ShadowBot.UIAutomation.Primitives/SelectorOperator.cs`：14 种运算符
  （equal/regex/wildcard/contains/…）；`SelectorAttribute.cs`：`{name, value, operator, required}`；
  `SelectorNode.cs`：`{selected, name, type, attributes}`。
- **纠正 M89 的一处猜测**：影刀**没有**「可排序的 locator 候选优先级列表」。
  `RectAndIndex.Selectors[]` 是预览命中矩形，不是候选定位方案。
- 截图与编辑器前端 HTML 不在仓库内（项目 .html/.js 数量为 0），字段从资源表 + BAML + VM 签名反推。

本仓侧：

- 桌面捕获**早就产出了结构化数据**，只是编辑框把它拍平成 JSON：
  `selector.locator = {backend, controlType, automationId, name, className, path}`，
  `selector.candidates = [{kind, locator, matchedCount, penalty, proposed}]`，
  `metadata = {windowTitle, className, rect, ...}`。
- 运行期 `model.desktop.match_mode` 已支持 `exact/contains/regex`，但**只作用于
  `name` / `automationId`**；`controlType` 恒等值，`path` 各级（`_step_matches`）逐字段恒等值。
  ⇒ 匹配方式列只能给这两个字段，其它行锁「精确」，否则就是「点了不生效」的假 UI。

## 2. 实现

- **内核** `src/rpa_core/capture_kit/desktop_editing.py`：
  - `nodes_from_descriptor`：窗口信息行（只读、不进 path）→ path 容器级 → 目标级；
  - `compose_selector`：勾选/属性/匹配方式 → **完整 selector 字典**，保留 `candidates`
    与 `driver/sourceRect/handle` 等未编辑字段；目标级至少留一个属性；
    两个 fuzzy 字段模式不同就报错（运行期只有一个 matchMode，不能悄悄选一个）。
  - `element_editing.build_edit_model` 对桌面同时给 `nodes` 与 `selector` 文本。
- **后端** `.harness/demo/ppx_probe/app/api/api.py`：`rpa.elementCompose` 新增
  `nodes/path/capture_id` 入参，桌面分派到内核；保存与校验沿用既有 selector 字符串链路。
- **前端** `main.js`：复用层级树与属性表两个 pane 渲染桌面模型；属性行是
  「启用｜属性名｜匹配方式｜属性值」，只有目标级 name/automationId 允许切方式；
  组合定位预览由后端给，前端不拼 JSON。
- **ADR 0022-R4 边界不变**：内核只做语义，工作台做会话与落盘。

## 3. 过程中发现并修掉的真实缺陷

`pollCapture()` 在 `openCaptureDialog()` **之前**调用 `endCapture()`，所以编辑框开着时
`captureSession === null`。原 `elementCompose`（M89）用 `captureSession?.id` 传 capture_id，
结果桌面每次改属性都会丢掉原 selector 的 `candidates`（用户看不到系统实测过的方案），
编辑框内的校验也拿不到会话（覆盖同名元素会误报重名）。

改法：编辑框自带 `dialogCaptureId`（`showElemDialog` 时从 options 存，关闭时清），
重组与校验都用它。**这条是静态门禁 + 渲染层门禁共同抓出来的**：注入
`dialogCaptureId = null` 后两条判据精确红。

## 4. 验证

- 新增：桌面编辑内核 10 条 + RPC 3 条，全绿；既有 element_editing 9 条无回归。
- UI 门禁：`check_element_pick_ui` **31/31**、`check_element_library` **16/16**。
- 负向验证 `probe_m90_negative.py`：5 个注入失败集逐项相等、4 文件 md5 逐字节还原、
  还原后三组判据复跑全绿。期间修正三处**我自己的期望写错**（目标级初始选中、
  shim 字段是 `tagName`、capture_id 真缺陷被门禁抓出）。
- 全量 `check_all.py`：`FULL GATE PASSED` + `rc=0`，**1003 passed / 20 skipped / 2 xfailed**。

## 5. 边界

- 桌面详情编辑仍走 win32 框（M89 定的降级/详情入口），本次只补**捕获后**的 HTML 弹框。
- 图像元素（`driver=image`）的属性不在编辑模型里：`sourceRect` 等字段原样保留，但不提供编辑入口。
- `npm run build` 的 vite 步骤已更新 dist；末尾 Inno Setup 打包因本机无 `ISCC.exe` 返回非零，
  与前端产物无关（dist 内已含本次新代码，dist 一致性测试通过）。
- PPX 原型是三分支共享资产，本次未跨分支 cherry-pick；代码按用户规矩只留本地、未提交未 push。
