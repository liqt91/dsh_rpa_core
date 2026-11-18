# M91：原生元素编辑框补上桌面详细编辑页

Status: `done`

> **2026-11-18 用户诉求**：「桌面元素捕获后的编辑框还是没有详细的编辑页面。」
> 用户补充：捕获和点「元素详情」拉起的都是同一个 win32 原生框，所以看不到 M90 做的网页详细编辑框。

## 1. 取证：不是没做，而是没接线

- 网页编辑框确实存在（`.harness/demo/ppx_probe/app/gui/src/main.js` 的 `#elemDialog`），
  `openCaptureDialog()` 会通过 `rpa.elementEditModel` 拿桌面 `nodes` 模型。
- 但 Windows 上两条用户路径都被原生框接管：
  1. 捕获成功后 `CaptureController._work()` 直接 `_open_edit_dialog()`；
  2. `openAssetDetail()` 先调 `rpa.editElement`，主动交给同一个 `ElementEditDialog`。
- 网页框只剩「非 Windows / 原生框不可用」降级路径 ⇒ 用户看到的一直是
  `edit_dialog._build_simple_children()`：资产名 + 一大坨 locator JSON + 只读信息。
- 结论：M90 的桌面模型是真的，但对 Windows 用户**等于没接线**。修在原生框，不改用户习惯。

## 2. 实现

- `src/rpa_core/capture_kit/edit_dialog.py`：新增桌面层级形态。
  - 左侧节点勾选（窗口只读、path 容器可勾掉、目标级链尾不许取消）；
  - 右侧属性行（勾选 / 值可改 / 精确·包含·正则），匹配方式只对运行期真吃
    `matchMode` 的 `name` / `automationId` 开放；
  - 底部实时预览是**完整 selector JSON**，由 `desktop_editing.compose_selector` 重组，
    保留 `candidates` 与未编辑字段；
  - 保存也走同一内核重组，杜绝「预览看着对、存下去错」。
- `src/rpa_core/capture_kit/controller.py`：把 `edit_model` 的 `nodes` 与原始
  `descriptor` 一并传给原生框（捕获后入口）。
- `app/api/api.py`：`rpa.editElement` 改用 `build_edit_model`，桌面资产传
  `nodes` + `descriptor`（元素详情入口）；保存仍先校验定位再动盘。
- ADR 0022-R5：原生框在 Windows 上是**主编辑面**，不再只是降级/详情入口；
  网页框保留为降级面，两者共用同一内核语义层。

## 3. 验证

- 真窗口探针 `_edit_probe.py` 新增第 8 组：节点/属性控件齐、只读窗口点不中、
  目标级取消会被弹回、改 name 后预览与保存都吃到新值且候选没被抹掉。
- 新增单测 1 条（捕获路径传 nodes/descriptor）+ RPC 测试 1 条（详情路径传模型、
  保存回写完整 selector）；契约门禁新增「原生桌面详细编辑框：两入口接线」。
- 负向验证 `probe_m91_negative.py`：3 个注入失败集逐项相等、3 文件 md5 逐字节还原、
  还原后三组判据复跑全绿。
- 全量 `check_all.py`：`FULL GATE PASSED` + `rc=0`，**1004 passed / 20 skipped / 2 xfailed**。

## 4. 边界

- 图像元素（`driver=image`）的 `sourceRect` 等字段仍只原样保留，不提供编辑入口。
- 网页层级编辑框与简式框的既有行为不变；只有传入 `nodes` 才走新的桌面形态。
- PPX 原型是三分支共享资产，本次未跨分支同步；代码按用户规矩只留本地、未提交未 push。
