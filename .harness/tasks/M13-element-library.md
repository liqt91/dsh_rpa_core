# M13 编辑器元素库

状态：`done`

## 目标

M10 捕获产物（元素描述符）已有落库约定（`GET /api/elements[/name]`），但编辑器还没有消费入口。本里程碑在编辑器内补全捕获闭环：浏览元素库、把元素插入当前节点属性、（评估）画布内一键发起捕获。

前置：M10 元素描述符契约（`model/capture.py`）、`/api/elements` 路由。

## 任务

- [x] 元素库面板：编辑器右侧「元素库」区，列出 `/api/elements`（name 标题 + kind/selector 摘要副标），支持刷新
- [x] 元素插入：选中元素 → 写入当前选中节点的对应字段（browser → `with.selector`；desktop → `with.locator`），类型不匹配时提示；写入后标记未保存
- [x] 编辑校验三段式：pick 不带 `saveAs` → 描述符仅返回不落库 → `POST /api/elements` 保存编辑后描述符 → `POST /api/elements/{name}/verify` 结构校验（ElementDescriptor 模型 + selector 语义 + DesktopLocator 校验；活体验证需捕获会话内完成，已明确 note）
- [x] 画布内一键捕获入口：属性面板 `selector` 字段旁「捕获」按钮 → persistent 浏览器捕获 → pick → 回填字段 + 可选 saveAs 入库
- [x] 元素删除端点（`DELETE /api/elements/{name}`）+ 合同测试（含 `do_DELETE` handler 补齐）
- [x] E2E：元素库列表渲染 + 插入到节点 + 保存读回、元素删除
- [x] 完整门禁

## 验收标准

- [x] 编辑器内可完成「捕获 → 入库 → 插入节点 → 保存」全流程（E2E 覆盖插入与删除）
- [x] 元素库契约测试与 E2E 通过
- [x] 完整门禁通过（110 tests）

## 范围外

- 自研捕获扩展（M14）
- 元素重命名 / 分组 / 搜索（按需后置）
- 跨 workflow 元素共享
- 活体验证（需捕获会话存活，结构校验先行）

## 待定问题

- ~~元素插入时是整份 selector 覆盖还是按字段合并？~~ 已实现：整份覆盖 + 类型匹配提示
- ~~一键捕获的桌面窗口句柄如何自动获取？~~ 浏览器捕获入口已做（selector 字段）；桌面捕获入口依赖 M14 扩展/hotkey 模式，后置

## 完成证据

- `src/rpa_core/devserver/app.py`：`put_element` / `delete_element` / `verify_element` + `_validate_element_document` / `_selector_errors`
- `src/rpa_core/devserver/store.py`：`delete`
- `src/rpa_core/devserver/server.py`：`do_DELETE` + 元素路由（POST/DELETE/verify）
- `static/`：元素库面板、插入/删除/回验、selector 捕获按钮
- 合同测试 +3、E2E +2；110 tests 全门禁
