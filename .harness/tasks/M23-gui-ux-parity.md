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
  - [x] **G1 剩余**：捕获后**确认对话框对齐 Web**（改名 / selector 编辑 / 捕获时命中数展示 /
    同名覆盖保护；当前仅 `QInputDialog` 命名）——2026-09-18 落地 `ElementDialog`
    （`gui/element_panel.py`）：名称/selector 可编辑（browser=css、desktop=locator JSON
    校验）、metadata 只读、命中数 1 绿/其他红；`_confirm_element_save` 承担同名覆盖确认
  - [x] **G1 平台退化修正（macOS 真机报障，2026-09-18）**：非 Windows 上桌面捕获 agent 带
    `sys.platform != "win32"` 守卫，`pick()` **49ms 即返回** `{"error": "desktop capture
    requires Windows"}`；旧 `HybridCaptureSession` 按「先回传者胜」把这个**失败**当成
    「用户捕获了桌面元素」，抢在仍可用的扩展腿之前 `close()` 掉它（→ disarm）→ 用户侧表现为
    「点捕获元素，窗口闪一下就弹回，网页里 Ctrl+Click 毫无反应」，状态栏还显示「已取消捕获」
    （该 error 无 `timeout`/`kind`，落进了 `cancelled` 分支）。修 4 处：
    ①`DesktopCaptureSession.available` + 新增 `desktop_capture_available()`：非 Windows 不 spawn
    子进程、`pick` 立即报不可用、`HybridCaptureSession` 据此把该腿排除出竞速（连等都不等）；
    ②**胜出判据收紧为「有效捕获描述符」**（`kind` ∈ {desktop, browser}，`hybrid._is_capture_result`）
    ——腿的失败形态（`unavailable` / `error` / `timeout` / `cancelled` / agent 崩溃 / 非法输出）
    一律不抢跑，任一腿失败只是出局、另一腿继续等；两条腿都出局时**立即收场**并透出真实原因
    （不干等满超时、不伪装成取消）；③GUI 提示按真实平台能力改写（非 Windows 不再承诺
    「桌面也可用 F9」；两腿皆不可用时不再弹「仍要继续仅桌面捕获吗」这个假选项，直接给原因）；
    ④`_SocketChannel` 的 `send`/`recv` 把裸 `OSError` 收口为 `LocalTransportError`
    ——通道被别处 close 后 `recv` 曾抛 `OSError: [Errno 9] Bad file descriptor` 逃逸成
    `ExtensionCaptureSession._read_loop` 的线程未捕获异常
  - [x] **G1 平台退化回归**：`test_capture_hybrid` 对称用例 ×4（桌面不可用→退化扩展 / 失败腿不抢跑 /
    两腿皆不可用快速失败 / 不可用腿不跑 pick）、`test_gui_capture` ×3（桌面不可用提示不含 F9 /
    两腿皆不可用不弹假选项且不最小化 / `unavailable` 不显示成「已取消」）、`test_local_transport` ×2
    （关闭通道抛领域异常 / 领域异常不继承 `OSError`）。真机验证（macOS + Edge 148 真实扩展）：
    `HybridCaptureSession` 全路径 arm → 合成 Ctrl+Click（走真实 `content.js` onClick）→
    拿到真实描述符 `#s-hotsearch-wrapper`（含 id/classes/text/rect/url）
  - [x] **连带修：测试端点前缀隔离被穿透（2026-09-18 跨进程实测取证）**：`conftest.py` 的隔离前缀
    `rpa_core_ext_test_isolated_` **以默认前缀 `rpa_core_ext_` 开头**，而 `list_endpoints` 用
    `startswith` 过滤 → **真实进程会枚举到测试端点**。后果不止门禁漂移：开发者跑测试期间用
    GUI/CLI 捕获会 arm 到测试的假 bridge 端点、拿到测试描述符并落库（本机复现：真机捕获验证脚本
    被测试端点接管，返回了 `test_capture_hybrid` 夹具里的 `#go` 描述符）。改为互不包含的
    `rpacore-iso_`，并加守门测试 `test_isolated_endpoint_prefix_does_not_overlap_default_namespace`
- [x] **G1 后继：真实手势缺陷——macOS 把 Ctrl+Click 改写成"次要点击"（2026-09-19 真机报障）**：
  用户在 Mac 上报「网页里有红框，但 Ctrl+Click 捕获不到元素」。根因在**平台层**：macOS 把
  Control+Click 改写成次要点击（Apple secondary click），浏览器因此只派发
  `mousedown(button=2)` / `contextmenu` / `auxclick`，**永远不派发 `ctrlKey===true` 的 `click`**；
  而 `content.js` 的捕获只挂在 `click` 上 → 手势进不了处理器。**上一轮的"真机验证"用的是
  `page.eval` 合成 ctrl+click，走不到这条平台改写路径，所以漏掉了它。** 修（`extension/content.js`）：
  ①判定抽成纯函数 `isCaptureModifier`（ctrl **或 meta**）+ `isSecondaryClick`（**以事件类型为准**
  ——`contextmenu` 的 `button` 在 Mac 上常见是 0，不能只看 button）；②新增 `mousedown`/`contextmenu`
  捕获期监听：次要点击（右键/双指点按/Mac 的 Ctrl+Click）一律捕获并 `preventDefault` 掉系统右键菜单，
  `capture` 内 300ms 去重保证同一次手势只回传一次；③红框旁新增**平台化提示条**
  （Mac「⌘ + 单击 或 右键捕获」，Windows/Linux「Ctrl + 单击」），收到不生效的单击时把系统
  **实际派发的事件回显**出来（`未捕获：click ctrl=false meta=false button=0`）——把"点了没反应"
  变成可直接读的证据；④`chrome.runtime` 失效（扩展重载后旧脚本孤立）与 `sendMessage` 抛错
  都改为提示条显式告知，不再静默失败。配套 `capture_click_label()`（`capture/extension.py`，
  按 `sys.platform`）统一 GUI/Web 用户可见文案，清掉写死的「Ctrl+Click」
- [x] **G1 后继回归**：`check_capture_helpers.mjs` +8（⌘/Ctrl 修饰键、contextmenu 不看 button、
  普通 click 不算次要点击）；`test_capture_extension` +4（文案按 darwin/win32/linux 三分支 +
  content.js 必须挂 contextmenu/mousedown）；`test_gui_capture` 断言状态栏手势文案与
  `capture_click_label()` 一致。真机取证：**版本探针**（合成 mousemove 后数覆盖层数）证明报障时
  页面里跑的是**旧版** content.js（1 个覆盖层、无提示条）→ 复验需重载扩展**并刷新页面**
  （扩展 reload **不会**替换已打开页面里已注入的 content script）
- [x] **G1 后继：macOS 窗口层级两处平台缺陷——浮窗随主窗口消失、确认框要点 Dock（2026-09-19 真机报障）**：
  用户报「运行时右下角小窗会跟主窗口一起隐藏；捕获后的确认弹窗需要额外点一次 Dock 图标才显示
  （该图标叫 python3.12）」。两个都是 **macOS 平台行为**，纯 Qt 可解、不引入新依赖：
  ① `RunFloatWindow` 用 `Qt.Tool`，而 macOS 上 Qt.Tool 对应 NSPanel——**应用一旦不激活，系统会隐藏
  全部 tool window**（Qt 文档原话："By default, tool windows will disappear when the application is
  inactive"；源码侧即 `window.hidesOnDeactivate = ((type & Qt::Tool) == Qt::Tool) && !flag`）。运行期间
  主窗口被 `showMinimized()`、用户正在别的应用里，浮窗因此跟主窗口一起没掉——而"主窗口最小化后还能
  看执行进度"恰恰是它存在的全部意义。修：加 `WA_MacAlwaysShowToolWindow`（Qt 文档指定的开关）。
  **向 AppKit 实测取证**：浮窗原生窗口 `hidesOnDeactivate=False`，对照的普通 Tool 窗口 `=True`。
  ② macOS 为防焦点窃取会**忽略后台应用的自激活请求**，`raise_()`/`activateWindow()` 不足以让窗口
  出现在最前；捕获期间我们必然是后台应用（主窗口已最小化、用户在浏览器里点元素），于是确认框被
  创建了却停在浏览器之后。修：新增 `present_window()`——短命模态对话框临时 `WindowStaysOnTopHint`
  置顶（唯一纯 Qt 可用的硬保证）+ 统一 show/raise/activate + `QApplication.alert` 兜底提示（macOS 弹跳
  Dock 图标）；用于捕获确认框、捕获结束还原、运行结束还原。**常驻主窗口不置顶**（会一直压住其它应用）。
  遗留：Dock 显示 `python3.12` 是**无 bundle 进程**的固有行为（Apple QA1544：
  `NSRunningApplication.localizedName` 取 CFBundleDisplayName → CFBundleName → 进程名），零依赖改不了，
  正规解法是打包 `.app`——见"风险 / 注意"。
- [x] **G1 后继回归**：`test_gui_run` +1（浮窗带 `WA_MacAlwaysShowToolWindow` 且仍是 `Qt.Tool`）；
  `test_gui_capture` +1（`present_window`：短命对话框置顶、常驻窗口不置顶）+ 断言命名对话框确实被
  置顶（`FakeDialog` 改为真 `QDialog` 子类——`present_window` 需要 `setWindowFlag/show/raise_` 等
  QWidget 契约，裸桩会被打到）。FULL GATE PASSED
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
- [x] **G3 属性面板追平 Web**：~~消费 `x-param-groups` 分组折叠~~（2026-09-18 slice A done）+
  ~~输出别名（`output_aliases` / `x-outputs`）编辑 UI~~（2026-09-18 slice B done）+
  ~~重试/超时字段（含 unsafe 禁用防呆，对齐 Web `retryCountField`）~~（2026-09-18 slice C done）
- [x] **G4 失败定位闭环**：~~运行失败点击错误 → 跳转失败节点~~（2026-09-18 slice A done）+
  ~~结构化错误详情（借鉴 Web `startupError` 透出经验）~~（2026-09-18 slice B done）；
  ~~运行日志加耗时 / 输出值预览~~（2026-09-18 slice C done）
- [ ] **切片内次级项**：~~变量面板（设计期静态收集 `output_aliases` / inputs 列表）~~
  （2026-09-18 variable-panel done）、
  ~~菜单栏（QMenuBar + 快捷键一览）~~
  （2026-09-18 menu-bar done）、~~卡片摘要按关键字段（url/selector/text）优化~~
  （2026-09-18 card-summary-opt done）
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
- **macOS 平台差异集中区（真机踩坑记录，改窗口/手势前先看这几条）**：
  ① `Qt.Tool` = NSPanel，**应用失活即隐藏**，跨应用场景必须显式 `WA_MacAlwaysShowToolWindow`
  （运行浮窗已修）；② 后台应用的 `raise_()`/`activateWindow()` 会被系统忽略，要让窗口"必须被看见"
  得置顶（`present_window`）——两处都不是 bug 而是系统策略，别按其它平台的直觉写。
- **待定：Dock 显示名 `python3.12`**。用户是从终端启动（`uv run python -m rpa_core.cli gui`），
  进程无 bundle，macOS 按 Apple QA1544 的回退链取到**解释器名**并显示在 Dock。
  `QApplication.setApplicationName()` 改不了它。两条路：**(A) 打包 `.app`**（`CFBundleName`/
  `CFBundleDisplayName`）——正规且是发行必须做的，推荐；**(B) `CPSSetProcessName`**（ApplicationServices
  的**私有 SPI**，ctypes 可调）——能改 Dock 长名与活动监视器，但改不了菜单栏 CFBundleName，且私有
  API 随系统版本失效。**未擅自引入**：A 是交付形态变更、B 是私有 SPI，都需先决策。

## 完成证据

- G1（主体）：PROGRESS 2026-09-17 `M23-gui-ux-parity | G1 done`——单入口混合捕获 + 最小化还原 +
  离线提示 + 重入/关闭取消 + 3 处存量缺陷修复；`test_capture_hybrid` +2、新增 `test_gui_capture` 4 例；
  full gate 516 passed（3 个失败为已知桌面 E2E 焦点抖动，HEAD 基线同失败）
- 关联（插件对话框 bridge 注册入口）：PROGRESS 2026-09-17 `gui-extension-dialog-bridge | done`；
  `test_gui_panels` +3；full gate 521 passed
- G2（画布交互）：PROGRESS 2026-09-18 `M23-gui-ux-parity | G2 done`——多选/批量移动/批量删除/
  右键菜单/Ctrl+F 查找；新增 `test_gui_canvas_batch` 14 例；FULL GATE PASSED
- G3–G4：待补
- G3 slice A（x-param-groups 分组折叠）：PROGRESS 2026-09-18 `M23-gui-ux-parity | G3 slice A done`——
  `_CollapsibleSection` + `_build_grouped_form` 对齐 Web `paramGroupPlan`/`paramGroupSection`；
  `app._show_action_form` 传 manifest；无 x-param-groups 命令向后兼容平铺；
  `test_gui_param_form` +8（分组渲染/字段数/collapsed 默认折叠+有值展开/toggle/
  无分组向后兼容/未声明字段归其他/values 收集一致）；FULL GATE PASSED
- G3 slice B（x-outputs 输出别名编辑）：PROGRESS 2026-09-18 `M23-gui-ux-parity | G3 slice B done`——
  `_build_output_aliases` + `_alias_fields` + `output_aliases()` 收集；`app.py` apply 写回
  + `_action_form_dirty` 别名变更检测；`test_gui_param_form` +6；FULL GATE PASSED
- G3 slice C（重试/超时字段）：PROGRESS 2026-09-18 `M23-gui-ux-parity | G3 slice C done`——
  `_build_retry_timeout` + `retry_timeout_values()`；超时隐藏（命令有 timeoutMs）+ 重试
  （retryable 渲染 / 不支持有遗留值警告+清除 / 无值不渲染）；`app.py` apply 写回
  + `_action_form_dirty` 超时/重试变更检测；`test_gui_param_form` +9；FULL GATE PASSED
- G4 slice A（失败节点跳转）：PROGRESS 2026-09-18 `M23-gui-ux-parity | G4 slice A done`——
  运行面板新增「跳转到失败节点」按钮（失败有 nodeId 时显示、新运行隐藏）；
  `_jump_to_failed_node` + `find_by_id` + `setCurrentIndex` + `scrollTo` 定位画布节点；
  `test_gui_param_form` +4；FULL GATE PASSED
- G4 slice B（结构化错误详情）：PROGRESS 2026-09-18 `M23-gui-ux-parity | G4 slice B done`——
  `_run_error_widget`（code/node/message/detail 四行，对齐 Web `renderRunError`）+ 启动失败
  也展示；`test_gui_param_form` +3；FULL GATE PASSED
- G4 slice C（运行日志耗时/输出预览）：PROGRESS 2026-09-18 `M23-gui-ux-parity | G4 slice C done`——
  `_format_event` 格式化事件流（▶/✓/✗/↻/▸ 图标 + 耗时 + 输出 keys / error code）；
  `_step_start_times` + `_run_started_at` 追踪；`test_gui_param_form` +7；
  `test_gui_run` 断言适配；FULL GATE PASSED
- 卡片摘要优化：PROGRESS 2026-09-18 `card-summary-opt done`——`summarize_args` 新增
  `_PRIORITY_KEYS`（url/selector/text/content/name/locator 等），关键字段优先展示；
  `test_gui_param_form` +4；FULL GATE PASSED
- 菜单栏 + 快捷键一览：PROGRESS 2026-09-18 `menu-bar done`——`_build_menu_bar` 四菜单
  （文件/编辑/运行/帮助）复用工具栏 QAction；帮助→`_show_shortcuts_dialog` 弹窗
  （11 条快捷键，QFormLayout）；`test_gui_param_form` +3；FULL GATE PASSED
- 设计期变量面板：PROGRESS 2026-09-18 `variable-panel done`——`_variables_dock` +
  `_refresh_variables`（iter_real_nodes 收集 output_aliases + manifest.x_var_write 变量赋值）；
  编辑菜单→变量面板开关 + 应用参数后自动刷新；`test_gui_param_form` +3；FULL GATE PASSED
- G1 剩余（捕获确认对话框）：PROGRESS 2026-09-18 `M23-gui-ux-parity | G1 剩余 done`——
  `ElementDialog`（改名/selector 编辑/命中数/metadata 只读）+ `_confirm_element_save`
  同名覆盖确认；`test_gui_panels` +3（browser 默认值与回写、desktop locator JSON 校验、
  空名校验）、`test_gui_capture` +2（取消不落库、同名覆盖确认）；full gate 557 passed
  （uia 桌面 E2E 门禁内抖一次、单独重跑过——已知环境敏感老毛病）
- G1 后继（macOS 窗口层级：浮窗随主窗口消失 / 确认框要点 Dock）：PROGRESS 2026-09-19
  `M23-gui-ux-parity | G1 后继 macOS 窗口层级 done`——`run_float.py` 加 `WA_MacAlwaysShowToolWindow`；
  `app.py` 新增 `present_window()`（短命对话框置顶 + `QApplication.alert` 兜底）用于捕获确认框、
  捕获结束还原、运行结束还原；`test_gui_run` +1、`test_gui_capture` +1 并把 `FakeDialog` 升级为
  真 `QDialog` 子类；FULL GATE PASSED。取证：真实 cocoa 平台向 AppKit 查询 `hidesOnDeactivate`
  （浮窗 `False` / 对照普通 Tool 窗口 `True`）
