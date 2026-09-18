# M23 GUI 体验对齐（用户体验 × 对标影刀）

状态：`active`
关联：ADR 0016（GUI 为唯一主力形态）、ADR 0014（GUI 宿主形态）、M19（Web 编辑器交互，作为对齐基准）、`docs/yingdao-web-cmds-benchmark.md`、`.harness/yingdao-gap-matrix.md`
分析依据：2026-09-17 GUI 代码审计（`src/rpa_core/gui/`）+ 影刀对标文档

## 背景与范围

GUI 已是唯一主力形态（ADR 0016），但交互体验仍落后于 Web 编辑器（M11 树形画布 / M12 中文化 /
M19 交互补强）与影刀基准。本任务按「用户体验 × 对标影刀」盘点缺口，分 G1–G4 切片 + 切片内次级项补齐。

范围约束：**只动 GUI 与必要的后端能力层**；不推进 Web 编辑器（`devserver/static/` 冻结，ADR 0016）。

## 任务（切片）

- [x] **G1 捕获链路（主体 done，2026-09-17）**：**单入口混合捕获**（影刀式「一个按钮，指哪捕哪」）
  - 元素面板「捕获桌面元素」→「捕获元素」单入口，接 `HybridCaptureSession`（网页正文走扩展、
    桌面走 UIA hover，先回传者胜）；捕获期间主窗最小化、结束还原；插件离线显式提示
    「网页区域无法捕获（桌面不受影响）」；重入拦截 + `closeEvent` 取消会话（规则 11）；
    命名默认按 kind（browser→tag / desktop→controlType）
  - **连带修 3 处存量缺陷**：① hybrid 让位标志在 CLI/devserver 工厂被 pop 后未转发给 desktop
    agent（浏览器内容区让位静默失效）→ `HybridCaptureSession` 构造时 `setdefault("hybrid", True)`；
    ② 扩展腿离线时其 `result_event` 在 start 已置位 → 旧 pick 误判「扩展先回传」秒回 cancelled
    → 记录 `extension_offline` 并在 pick 跳过离线腿；③ CLI 桌面捕获从未 `start()`（扩展腿从未 arm）
    → hover 默认 hybrid 且 `is_extension_capture` 时显式 start（与 devserver 同口径）
  - [ ] **G1 剩余**：捕获后**确认对话框对齐 Web**（改名 / selector 编辑 / 捕获时命中数展示 /
    同名覆盖保护；当前仅 `QInputDialog` 命名）
- [x] **G2 画布交互（2026-09-18）**：多选 + 批量移动/删除 + 右键菜单 + 画布内搜索定位（Ctrl+F）
  - 多选：`FlowTreeView` 改 `ExtendedSelection`；Ctrl/Shift 点击不再顺带折叠容器（`_toggle_on_click` 检测修饰键让路）
  - 批量移动：`FlowTreeModel.mimeData` 本就携带多个 id，`dropMimeData` 内部移动改为批处理——
    过滤「祖先也在被拖集合」的项（移动祖先已连带移动它）、成环守卫逐项判定、按落点顺序依次
    `takeRow/insertRow` 保持相对顺序；`canDropMimeData` 的 else-branch 守卫与成环守卫同步改为逐项
  - 批量删除：`_delete_selected_node` 支持多选（`_selected_canvas_items` 去重按树序；跳过「祖先也
    选中」的后代避免重复操作），状态栏报「已删除 N 个节点」
  - 右键菜单：`CustomContextMenu` + `_canvas_context_menu`（复制/粘贴/删除/添加「否则」）；
    可用性抽成 `_canvas_menu_state(index)` 便于测试；右键未选中项先选中、落在选中集内保留多选
  - Ctrl+F 查找：画布顶部查找条（默认隐藏，Esc 关闭），匹配 卡片标题/节点 id/类型/命令 id/参数摘要，
    Enter 循环定位（自动展开祖先 + 滚动居中），状态栏报「匹配 i/N」
  - 测试：新增 `tests/contract/test_gui_canvas_batch.py` 14 例（批量移动顺序/跨父级/跳过后代/成环拒绝、
    批量删除/容器+子只删一次/虚拟行保护、菜单可用性矩阵、多选与菜单接线、查找匹配与循环/Esc 关闭）
  - 顺带修：`_canvas_context_menu` 原先把 QModelIndex 传给 `_nearest_if`（要 item）→ 潜在死循环，已修正
  - **G2 稳定性修复（2026-09-18，维护者报障「连续操作后卡死」）**：查找匹配集缓存悬空 item（结构变更后
    C++ 对象已删）→ 变更后重算 + `isValid` 剔除；模型层加 `mutating` 重入护栏与死行/裸空行自愈；
    批量移动空 `takeRow` 不再插空行、invisibleRoot 不可拖、`_is_descendant` 补 invisibleRoot 祖先；
    删除/粘贴期间 `blockSignals` 屏蔽选中信号；压力脚本 2000 步跑满 1944 步零崩溃零卡死
- [ ] **G3 属性面板追平 Web**：消费 `x-param-groups` 分组折叠（GUI `param_form` 仍平铺）+
  输出别名（`output_aliases` / `x-outputs`）编辑 UI + 重试/超时字段（含 unsafe 禁用防呆，
  对齐 Web `retryCountField`）
- [ ] **G4 失败定位闭环**：运行失败点击错误 → 跳转失败节点 + 结构化错误详情
  （借鉴 Web `startupError` 透出经验）；运行日志加耗时 / 输出值预览
- [ ] **切片内次级项**：变量面板（设计期静态收集 `output_aliases` / inputs 列表）、菜单栏
  （QMenuBar + 快捷键一览）、卡片摘要按关键字段（url/selector/text）优化
- [x] **关联已交付（同批，2026-09-17）**：GUI 插件对话框补齐 **bridge host 注册入口**
  （`gui-extension-dialog-bridge`）——每浏览器注册状态行 + 「注册 bridge」按钮
  （`ensure_native_host` 幂等自愈、逐浏览器容错）+ 引导第 0 步 + 三行联动刷新；
  修「纯 GUI 用户装完扩展通道仍离线且无处自助」

## 验收

- 每切片：GUI 合同测试（offscreen）+ `check_all.py` 全门禁通过；PROGRESS 追加一行
- G2–G4 以 Web 编辑器现行实现为对齐基准（M11/M12/M19 成果），**行为不得回退**
- 真实平台观感需人工确认（offscreen 无 CJK 字体，卡片/表单观感须实机核对）

## 风险 / 注意

- GUI 是「全量重绘 + `QStandardItemModel`」范式：批量移动/删除与右键菜单须保证**选中集与
  撤销栈一致性**（参考 Web 的快照式撤销，M11 经验）。
- PySide6 所有权陷阱：`insertRow(row, item)` 不转移所有权（须用 `[item]` 形式），批量操作时尤其危险。
- 捕获链路已依赖扩展腿（Native Messaging）：离线时的降级与提示须与 M20/ADR 0015 的在线语义一致。

## 完成证据

- G1（主体）：PROGRESS 2026-09-17 `M23-gui-ux-parity | G1 done`——单入口混合捕获 + 最小化还原 +
  离线提示 + 重入/关闭取消 + 3 处存量缺陷修复；`test_capture_hybrid` +2、新增 `test_gui_capture` 4 例；
  full gate 516 passed（3 个失败为已知桌面 E2E 焦点抖动，HEAD 基线同失败）
- 关联（插件对话框 bridge 注册入口）：PROGRESS 2026-09-17 `gui-extension-dialog-bridge | done`；
  `test_gui_panels` +3；full gate 521 passed
- G2（画布交互）：PROGRESS 2026-09-18 `M23-gui-ux-parity | G2 done`——多选/批量移动/批量删除/
  右键菜单/Ctrl+F 查找；新增 `test_gui_canvas_batch` 14 例；FULL GATE PASSED
- G3–G4：待补
