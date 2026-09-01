# M9 编辑器 v1：零构建单页

状态：`done`

## 目标

在 dev server 之上交付第一个可用的编辑界面：零构建单页（vanilla HTML/JS/CSS，无框架、无 npm、无 CDN），提供命令面板、线性画布与 schema 驱动属性表单，经 dev server API 完成打开/保存/编译校验闭环。树形嵌套编辑（if/forEach/try）留给 M11。

前置：ADR 0008 放行编辑器 UI（规则 10 的 UI 排除在第一垂直切片通过后由本 ADR 解除；"React UI" 排除维持不变）。

## 任务

- [x] 编写 ADR 0008：编辑器 UI 放行结论（零构建静态单页、由 dev server 托管、无新增依赖、无构建工具链、非 React）
- [x] dev server 托管编辑器页面：`GET /` 返回单页 HTML（仅此一条静态路由，不开放任意文件路径）
- [x] 编辑器 v1 功能：
  - 命令面板：拉取 `/api/catalog`，按 id 过滤，点击追加为 action 节点
  - 线性画布：sequence 根下的 action 节点列表，支持上移/下移/删除/选中
  - 属性表单：按所选项 input_schema 生成字段（string/number/boolean/enum），支持 `${...}` 引用语法
  - 工作流文件：列表/新建/打开（`GET /api/workflows*`）/保存（PUT，未编译通过的草稿可保存）
  - 编译回显：POST `/api/compile`，逐条展示 `errors[]`（path + message），`valid` 状态徽标
- [x] 合同测试：`GET /` 返回 HTML、非 `/` 路径仍按 API 路由处理、静态路由无目录遍历面
- [x] Playwright E2E：真实 Chromium 打开编辑器 → 渲染 catalog → 追加节点 → 编译回显 → 保存读回一致
- [x] 手册补充：`docs/devserver.md` 增加编辑器入口说明
- [x] 完整 harness 门禁通过

## 验收标准

- [x] `uv run python -m rpa_core.cli devserver` 后浏览器打开 `http://127.0.0.1:8765/` 可完成"新建 → 拖入/追加命令 → 填参 → 编译回显 → 保存"全流程
- [x] 编辑器零构建：无 npm/打包器/CDN，页面资产仅由 dev server 提供
- [x] Playwright E2E 与合同测试通过
- [x] 完整门禁通过

## 范围外

- 树形嵌套编辑与元素库（M11）
- 元素捕获实装（M10）
- 认证、多用户、远程访问
- run/pause/resume 操控入口（ADR 0006 推迟不变）

## 完成证据

- ADR 0008（`.harness/adr/0008-editor-ui.md`）已接受：设计期编辑器 UI 放行，零构建硬边界确立。
- 编辑器单页：`src/rpa_core/devserver/static/index.html`（vanilla HTML/CSS/JS 单文件，无外部依赖）；`GET /` 唯一静态路由，其余路径维持 API 404 面（无目录遍历面）。
- 功能实测（Playwright E2E `tests/e2e/test_editor.py`，真实 Chromium）：渲染 26 条 catalog → 追加 browser.launch/browser.navigate → 填非法引用编译回显 `Unknown workflow input reference`（✗ invalid）→ 改合法值编译通过（✓ valid）→ 保存 → API 读回字段一致 → 刷新页面经「打开」下拉重新载入画布两节点。3 连跑稳定。
- 合同测试新增 3 项（`GET /` 返回 HTML、`/static/*` 与 `..%2F` 等 404 无泄漏、`POST /` 405）。
- 修复过程中发现的页面缺陷：编译成功分支漏设 `.ok` class（E2E 首跑即捕获），已修复。
- Full gate：87 tests + ruff + architecture + task check 全绿（2026-09-01）。
