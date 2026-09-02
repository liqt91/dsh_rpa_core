# M13 编辑器元素库

状态：`active`

## 目标

M10 捕获产物（元素描述符）已有落库约定（`GET /api/elements[/name]`），但编辑器还没有消费入口。本里程碑在编辑器内补全捕获闭环：浏览元素库、把元素插入当前节点属性、（评估）画布内一键发起捕获。

前置：M10 元素描述符契约（`model/capture.py`）、`/api/elements` 路由。

## 任务

- [ ] 元素库面板：编辑器右侧或底部新增「元素库」区，列出 `/api/elements`（中文名 = name，副标 = kind + selector 摘要），支持刷新
- [ ] 元素插入：选中元素 → 写入当前选中节点的对应字段（browser → `with.selector`；desktop → `with.locator`），写入后标记未保存
- [ ] **编辑校验三段式**：pick 不带 `saveAs` → 描述符仅返回不落库 → 编辑器草稿编辑 → 回验（`POST /api/elements/verify`：把改过的 locator/selector 拿回活目标再验命中数）→ 保存（`POST /api/elements`）
- [ ] 画布内一键捕获入口（评估后实施）：属性面板「捕获」按钮 → 调 `/api/capture/browser/start(persistent)` 或 `/api/capture/desktop/start`（需窗口句柄时提示用户）→ pick（超时 60s）→ 描述符直接填入字段并入库
- [ ] 元素删除端点（`DELETE /api/elements/{name}`）+ 合同测试
- [ ] E2E：元素库列表渲染 + 插入到节点 + 编辑后回验 + 保存读回
- [ ] 完整门禁

## 验收标准

- [ ] 编辑器内可完成「捕获 → 入库 → 插入节点 → 保存」全流程
- [ ] 元素库契约测试与 E2E 通过
- [ ] 完整门禁通过

## 范围外

- 自研捕获扩展（M10c）
- 元素重命名 / 分组 / 搜索（按需后置）
- 跨 workflow 元素共享

## 待定问题

- 元素插入时是整份 selector 覆盖还是按字段合并？（倾向整份覆盖 + confirm）
- 一键捕获的桌面窗口句柄如何自动获取？（倾向：hotkey 模式让用户把焦点放到目标窗口）

## 完成证据

仅在全部验收标准通过后填写。
