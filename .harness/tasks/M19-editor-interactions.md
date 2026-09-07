# M19 编辑器交互补强（可拖分栏 / 元素库底部 Tab / 表单与捕获体验）

状态：`active`
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
- [ ] **决策点**（A/B/F/G 已完成，现触发）：评估 app.js 行数/回归 → 决定切片 C/D/E 走 vanilla 还是立 ADR 迁 React
- [ ] **切片 C：元素截图灯箱 + 上传 + 缩略图**（依赖决策点）
- [ ] **切片 D：属性表单主元素选择器下拉 + kind 徽标 + 锚点提示**（依赖决策点）
- [ ] **切片 E：多 tab 属性表单（参数/元素/高级）**（依赖决策点）
- [ ] 每切片：E2E/合同 + 完整门禁；PROGRESS 追加一行

## 验收（汇总）

- 编辑器三栏 + 底部可拖、布局记忆生效
- 元素库在底部 Tab 区、分组可折叠且记忆
- 捕获后自动刷新（激活可见时才轮询）
- 运行参数可配置、运行中离开有警告
- 决策点产出明确结论（继续 vanilla 或 ADR 迁 React），C/D/E 按其结论推进
- 零构建边界维持至决策点；架构检查、完整门禁通过
