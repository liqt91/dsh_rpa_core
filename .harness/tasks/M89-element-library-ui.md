# M89：元素库操作界面升级——把编辑语义收口到内核，再给工作台做影刀式编辑框

Status: `done`

> **2026-11-18 用户诉求**：「捕获元素没问题了，按图捕获目前先不做……你再看下影刀 rea
> 目录，提到了影刀的元素编辑、元素保存、元素使用的方式，更新我们的元素库操作界面，
> 尤其目前的捕获后的编辑界面，太简陋了。」用户随后把形态问题提升为架构问题：
> 「本质是怎么切割元素捕获 kit 和工作台的边界，这个结合最佳实践再评估下」。

## 1. 取证

影刀侧（`D:\Users\Administrator\Documents\代码\rea-yigndao`，只读）：

- `影刀元素编辑方式报告.md`：双入口编辑（设计器元素面板 + 指令参数就地编辑）、
  CefSharp HTML 编辑器 + JS 桥（`GetInitSelector` / `SaveSelector`）、
  保存前 `ValidSelectorNameAndGroup` + `_store.Contains` 判重 + `RemoveEmptyAttribute`。
- `影刀元素库与流程编辑器逆向报告.md`：`SelectorModel` 字段（路径节点/锚点/XPath/截图）、
  `uitarget` 参数编辑器、失效元素 `IncompatibleSelector`。
- 中文按钮文案与真实 `selectorsV2.xml` 样本不在工作区，只有资源键名，未据此编造。

本仓侧：

- 捕获确认框 `#elemDialog` 只有**资产名称**可改 + 只读 meta + 保存/取消；
  `capturePoll` 的 preview 只有定位字符串。
- 层级树/属性表能力在 `main` 分支还留着（`gui/element_editor.py`），
  当前 `xhs-workbench` 被 `ppx-only` 裁剪掉；后端 `selector_tree` 纯函数仍在。
- **边界失守**：`api.py` 直接 import `capture_kit.ElementEditDialog`（win32 UI 类），
  还在应用层自写名字校验、定位校验、编辑模型。ADR 0022-R1 的白名单只有
  `catalog/compiler/model`，现实早越过却未成文。

## 2. 边界决策（ADR 0022-R4）

吸收影刀做法：**HTML 编辑器 + 语义桥 + 后端服务封装模型/校验/错误码**。

- 内核 `capture_kit.element_editing` 是无 UI 权威：名字/定位校验、编辑模型、层级重组
  （转出 `selector_tree.compose`）；不碰文件、会话、窗口。
- 工作台只做会话与落盘，前端不感知 UIA / CDP / 对话框实现；win32 框降为详情/降级入口。
- 参数键口径改取内核 `runtime.element_refs`（前端不硬编码 `selector`/`locator`）。

## 3. 改动

- 内核：新增 `element_editing.py`（9 条契约），`capture_kit/__init__` 导出。
- 后端：`elementEditModel` / `elementCompose` / `elementValidate` / `recaptureElement` /
  `verifyAllElements` / `elementCandidates`；`commitElement` 收编辑后的定位且非法不吞会话；
  `listElements` 带 `refCount`（与引用清单同源）。
- 前端：编辑框扩成左层级勾选 + 右属性表 + 组合选择器 + 校验 / 重新捕获 / 保存并继续；
  元素面板加搜索、按类型分组、引用数、全部校验；参数面板加「从元素库选」下拉
  （只写 `nodeRefs`，随保存全量提交，ref-only 不变）。
- 门禁：`check_element_pick_ui.mjs` 的按钮断言精确为「捕获按钮恰好两个 + 管理按钮白名单」，
  并新增编辑框结构/语义 RPC/层级渲染断言；`check_element_library.mjs` 新增搜索与接线断言。
- 前端产物 `gui/dist` 已重建（`npm run build` 只在 Inno Setup 一步失败，dist 正常产出）。

## 4. 验证

- 判据：内核 9 条 + 新 RPC 6 条 + 既有工作台 26 条全绿；两个 UI 门禁 29/29、16/16。
- 负向验证 `.harness/spike/probe_m89_negative.py`：5 个注入失败集逐项相等 +
  4 文件 md5 逐字节还原 + 还原后复跑全绿。
- 全量门禁 `FULL GATE PASSED`，rc=0，993 passed / 20 skipped / 2 xfailed。

## 5. 边界与欠账

- 活体校验仍是保存后单独走 `verifyElement`；编辑框内的「校验」只做静态校验（名字/定位/重名），
  没有影刀那种编辑期页面高亮。
- 桌面元素仍只有定位 JSON 文本框，没有控件级层级树（ADR 0018 的 path 是容器级）。
- PPX 原型目录是三分支共享资产，本次改动**未跨分支 cherry-pick**，只在本地工作区。
- `npm run build` 的 Inno Setup 打包步骤因环境缺 ISCC.exe 失败，不影响 dist 与门禁。
