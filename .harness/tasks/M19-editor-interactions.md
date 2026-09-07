# M19 编辑器交互补强（可拖分栏 / 元素库底部 Tab / 表单与捕获体验）

状态：`done`
关联：ADR 0008（零构建边界）、docs/editor-design.md、隔壁 rpa_script `src/ui/workflow-editor`（仿影刀交互，仅借鉴不搬码）

## 背景与范围

对照隔壁仿影刀编辑器，盘点 rpa_core 编辑器缺失/较弱的交互点。**保持零构建 vanilla 边界**
（ADR 0008），分批切片；在触到"手写状态同步"高成本切片（C/D/E）前设技术栈决策点
（重估是否立 ADR 迁 React，打包产物入 devserver/static）。

## 技术栈评估结论（2026-09-07）

- 现状：app.js 1921 行 / 74.7KB 单文件，全量重绘 + 事件委托。复杂度在功能面不在栈。
- 新增交互里，A/B/F/G 是纯 CSS/低状态，vanilla 无压力；C/D/E 是手写 DOM 同步高发区。
- **决策点**：完成 B 后、做 C 前，若 app.js > ~2500 行且回归增多 → 立 ADR 评估迁 React 19
  （打包产物入 devserver/static，不动 devserver 隔离）；否则继续 vanilla。此决策单独立项。

## 任务

- [x] （草案）对照隔壁提取交互清单，产出切片排期与技术栈评估
- [x] **切片 A：面板可拖拽分栏 + localStorage 记忆**
  - 左(#palette)/右(#props)宽度可拖；拖后记忆、重开恢复
  - 尺寸下限/上限；body 拖拽态 class 防选中
  - E2E：拖 handle 后宽度变化 + 刷新后保持（`test_editor_resizable_panels_persist`）
- [x] **切片 B：元素库移入底部 dock（可拖高 + 记忆）**
  - 元素库从右侧 props 移到底部 `#bottom-panels`（resize-h 可拖高、localStorage 记忆、高度下限/上限）
  - 元素列表改横向网格（auto-fill minmax 280px）；运行面板维持 footer 不动
  - E2E：dock 内有元素 + props 不再含元素库 + 高度拖动/刷新保持
- [x] **切片 F：捕获后自动轮询刷新（仅元素库激活且页面可见时）**
  - 2s 轮询元素列表，与 `state.elementsSeen` 差集比较，仅变化时重渲染（后台捕获落库自动带出）
  - 门控：`document.visibilityState==="visible"` 且已命名 flow；visibilitychange→可见即刷
  - E2E：`test_editor_element_auto_refresh_on_poll`（外部新增元素，无手动 ↻ 自动出现）
- [x] **切片 G：运行参数对话框 + 运行中 beforeunload 警告**
  - 顶层 inputs 声明时 ▶ 运行先弹对话框（按原始类型 string/number/bool 渲染输入），取消不启动
  - 无 inputs 声明直接运行；运行中（activeRunId）离开页 beforeunload 拦截
  - E2E：`test_editor_run_params_dialog_when_inputs_declared`（弹窗+取消不启动）；既有 run_control E2E 适配（确认参数后运行）
- [x] **决策点**（A/B/F/G 完成，已评估）：**结论——切片 C/D/E 继续 vanilla，不立 ADR 迁 React**
  - 实测：app.js M19 起点 1886 → 现 2049 行（A+B+F+G 净 +163 行），远未到 ~2500 拐点；每片 30-56 行收敛于既有全量重绘模式，零范式回归
  - 迁移固定成本高（重写 2049 行 + 产物入库 devserver/static + 破零外链 ADR + E2E 重验），不因 C/D/E 属 React 友好而减少
  - 保留后置触发条件（任一即重估立 ADR）：app.js>2500 行且连续 2 切片状态同步回归 / 需虚拟滚动或自由 DAG 画布 / 需 devserver 外独立复用
- [x] **切片 D：属性表单 selector 字段「从元素库选」下拉 + kind 徽标**（重排优先，纯前端）
  - browser.* 命令 `selector` 字段：下拉列当前流程元素，选中浏览器元素自动填 css + 显示 kind 徽标；与「捕获」按钮并列
  - 桌面字段语义未纳入（click 用 elementId / 捕获落库用 locator，不一致，避免臆测）
  - E2E：`test_editor_selector_field_picks_element_from_library`
- [x] **切片 E：多 tab 属性表单——留接口，字段多再划分（维护者决策）**
  - 字段密度实测：26 命令参数字段 1-6 个（多数 3）；三 tab（参数/元素/高级）会空分区负优化
  - 决策：当前单列即为既有形态，不引入虚假 tab/无行为骨架；未来当单命令 schema 字段数显著增多（>~8）再按「常规/参数/…」划分，此处保留该触发条件
- [ ] **切片 C：元素截图灯箱 + 上传 + 缩略图（后置，待捕获截图源就绪）**
  - 现状缺口：捕获产物无图（全仓库无截图），stdlib 无截图/压缩；上传需新后端端点且撞 1MiB body 限
  - 触发：bsk/桌面捕获链路具备自动截屏能力后再评估
- [ ] 每切片：E2E/合同 + 完整门禁；PROGRESS 追加一行

## 完成证据（2026-09-07）

- 切片 A：面板可拖分栏（palette/props 宽 + bottom dock 高，localStorage `rpa_editor_panel_resize`）
- 切片 B：元素库底部 dock（`#bottom-panels` 横向网格）；方向修正（上拖增高）
- 切片 F：捕获自动刷新（2s 轮询 + visibilitychange 门控 + `state.elementsSeen` 差集）
- 切片 G：运行参数对话框（顶层 inputs 弹窗按类型渲染）+ 运行中 beforeunload 拦截
- 切片 D：selector 字段「从元素库选」下拉 + kind 徽标
- 切片 E：维护者决策留接口（字段密度 1-6，单列即既有形态；字段显著增多再划分）
- 技术栈决策点：继续 vanilla，不迁 React（实测 app.js 1886→~2050 行线性增长、零范式回归；迁移固定成本高）
- 切 C（截图）：后置，触发 = 捕获链路具自动截屏能力
- 证据：full gate 通过；PROGRESS 各行记录；M19 任务单

## 验收（汇总）

- 编辑器三栏 + 底部可拖、布局记忆生效 ✓（A/B）
- 元素库底部 dock、捕获后自动刷新 ✓（B/F）
- 运行参数可配置、运行中离开有警告 ✓（G）
- selector 字段可从元素库选 ✓（D）
- 决策点产出明确结论（继续 vanilla）✓
- 零构建边界全程维持；架构检查、完整门禁通过 ✓
- 切 C 截图后置（待截图源）为已知后续项，非本里程碑阻塞
