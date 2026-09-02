# M11 编辑器交互升级：结构化树形画布

状态：`done`

## 目标

编辑器从线性画布升级为结构化树形画布，交互对齐隔壁项目水平（只借鉴交互设计，clean-room 重写，不引入框架/构建链，ADR 0008 零构建边界不变）。后端 AST（Action/Sequence/If/ForEach/Try/Return 判别联合 + children 嵌套）已完整支持控制流，本里程碑纯前端。

已确认的范式决策（2026-09-01 与维护者确认）：
- **画布 = 结构化树，非连线式 DAG**。分支汇合 = 嵌套位置的隐式语义（if/try 分支走完落到外层下一个兄弟），与 M3 checkpoint 路径键语义一致；goto/任意连线不做。
- **撤销 = 快照式**，50 步上限，恢复选中状态。
- **静态资源拆分**为 allowlist 文件（index.html + app.js + styles.css）。
- **执行顺序硬约束：拖拽先行**——切片 1（树模型 + 树形渲染 + DnD v2）完成后暂停，请维护者实际操作确认交互效果，确认后方可继续切片 2-5。

## 前置（切片 0）

- [x] Harness 交接：本任务单置 active（M10-capture 回 planned，S1 证据保留）、feature_list/project_state/BACKLOG/PROGRESS 同步
- [x] ADR 0008 增补：dev server 静态资源允许多文件——`GET /static/{name}`，server.py 内**硬编码 allowlist**（app.js/styles.css），无目录列举、无路径遍历面，仍零构建
- [x] 命令面板分组数据源：按 manifest id 首段前缀分组（browser/data/desktop/desktop_win32/…）+ "控制流"分组（sequence/if/forEach/try/return）

## 切片 1（先行验证片，完成后暂停等确认）

- [x] 树模型层（app.js）：路径寻址 `find(path)`/`insert(path,node)`/`removeSubtree(path)`/`moveSubtree(src,dstPath,dstIndex)`；**id 全树唯一**（编译器全局查重，新建/粘贴生成 id 时必须查全树，不得只查兄弟层）；子树移动禁止落入自身子树
- [x] 树形渲染：递归渲染缩进色带（每层彩色背景带）、容器行左侧竖线 + 类型徽标 + 参数摘要、return 节点终止样式；现有线性 workflow 向后兼容渲染
- [x] 面板：控制流分组（sequence/if/forEach/try/return 可拖入画布）；点击/拖拽双通道保留
- [x] DnD v2：子树整体拖拽（grip 手柄）；落点 = 行上/下半区（插入线指示）+ **空容器落点指示**；拖拽边缘自动滚动（阈值 48px + rAF）；拖入命令时按 input_schema default 构造 with；拖入容器节点时自动带标准子结构（if→condition+then/else 空数组，try→catch 空数组）
- [x] E2E：拖入容器、嵌套结构保存读回一致
- [x] **暂停点：请维护者操作确认拖拽体验，确认后继续**——2026-09-02 维护者确认"拖拽效果可以接受"

## 切片 2-5（确认后依次执行）

- [x] 控制节点属性表单：if → condition（op 下拉 eq/ne/gt/gte/lt/lte/contains/truthy + left/right 智能字面量输入，${...} 引用保持字符串、其余按 JSON 解析）；forEach → items（JSON/引用）+ item_var；try → error_var；return → value（JSON 文本域）；sequence → 说明提示
- [x] 多选：单击/Ctrl 切换/Shift 范围（同列表锚点范围）；工具栏批量上移/下移/删除（同列表 + 连续性校验，非连续时禁用；删容器提示连带子树）；结构变更后以节点对象身份重寻选中集
- [x] Ctrl+C/V 子树复制粘贴：内部剪贴板 `{version:1, nodes}`；粘贴时全树 id 重映射（原 id 去尾数字作 base）；粘贴到主选中节点之后（无选中则根末尾）；输入框聚焦时快捷键不劫持
- [x] 快照式撤销/重做：Ctrl+Z / Ctrl+Y(+Shift+Z)；每次变更前压栈 `structuredClone(workflow)+selection`，上限 50；新操作清空 redo 栈；文本字段按 focus 入栈（一次编辑一步）、其余按 change 入栈
- [x] E2E 全面更新 + 合同测试 + 完整门禁（93 tests：控制节点表单往返、复制粘贴+撤销重做+输入焦点守卫、多选批量移动/禁用/删除、容器删除确认）

## 验收标准

- [x] 切片 1 完成后维护者确认拖拽体验（2026-09-02 维护者确认"拖拽效果可以接受"）
- [x] 画布可完成：嵌套 if/forEach/try 构建、子树拖拽移动、多选批量操作、复制粘贴、撤销重做全流程（E2E 覆盖）
- [x] 现有线性 workflow 与既有 E2E 向后兼容（test_editor_vertical_slice / nested_containers 保持通过）
- [x] 零构建边界不破（无 npm/框架/CDN）；架构检查与 full gate 通过（93 tests）

## 范围外

- 连线式 DAG 画布 / goto / while/break/continue AST 扩展（需新 ADR）
- 节点 enabled 禁用字段（需模型变更，另议）
- 变量补全下拉、全屏代码编辑器（进阶项，默认不做）
- 运行状态高亮（依赖 run 事件接入，另行评估）

## 待定问题

- ~~styles.css 与 app.js 拆分粒度~~ 已落地（allowlist 三文件）
- ~~容器删除时子树上提还是整树删除~~ 已落地（整树删除 + confirm 提示）

## 完成证据

- `src/rpa_core/devserver/static/app.js`：多选模型、批量操作、复制粘贴（id 重映射）、快照撤销/重做、控制节点表单
- `index.html` 工具栏按钮 + `styles.css` 多选/禁用样式
- `tests/e2e/test_editor.py`：新增 3 个测试（控制节点表单往返、复制粘贴+撤销重做、多选批量操作与禁用规则）
- 维护者切片 1 确认记录（2026-09-02）；full gate 93 tests 通过
