# M93 — 元素库对齐影刀 P2：贴影刀单树 + 右键管理

Status: `done`

## 背景

M92（P1）只落了数据地基：分组存 `element_groups.json`、元素顶层显式 `group` 字段、
8 个管理 RPC。用户拍板形态是「贴影刀单树」，但第一版界面没接，面板仍是按 `kind`
硬分三桶（网页/桌面/其它），建了分组也看不见。

M93 把 P1 的 RPC 接成用户能看到、能操作的树形库。

## 取证结论（影刀 4 份逆向报告 + 我方现状）

- 影刀 `SelectorView` 核心是 `ListTreeView`：分组与元素同树、可折叠、右键菜单、多选。
- 静态按钮白名单钉死面板只有 3 个按钮（捕获×2 + 全部校验），资产行恰好 3 个动作
  （详情/校验/删除）。因此管理入口只能走**动态 DOM**（右键菜单 / 批量条），不碰
  `index.html` 静态区；`.elemGroup/.elemFilter/.elemRefs` 样式契约保留。
- P1 的 `listElements` 已返回 `group/groupName/framework/processName/winTitle/screenshot/
  lastVerifiedAt/lastVerifyError`，`listElementGroups` 返回分组+元素数，`findUnusedElements`
  返回零引用资产名——树的数据源齐备，前端不需自己算引用/校验。

## 实现

- `main.js`
  - `refreshElements` 并行取 `listElementGroups` / `findUnusedElements`（缺 RPC 时按空
    降级，不拖垮清单）；`elementTreeBuckets` 纯函数按服务端顺序分桶 + 未分组兜底。
  - 资产段渲染成「分组头行 + 缩进资产行」：分组可折叠（`setElementGroupCollapsed`
    落盘，失败回滚）、有 `⋯`/右键菜单；资产行加多选复选框与来源副行、引用徽标。
  - 右键菜单（动态 `#elemLibLayer`）：分组（新建元素/新建分组/重命名/折叠/删除，
    删组不删元素）、资产（详情/复制/移动/查引用/删除，删除复用行内护栏链）。
  - 多选批量条：批量移动/校验/删除/取消选择；「只看未使用」筛选。所有写操作走 RPC，
    前端只收参数与刷新，不自己改盘。
- `api.py`：新增 `rpa.setElementGroupCollapsed(path, group_id, collapsed)`，只改 groups.json。
- `index.html` / `style.css`：新增动态菜单宿主 `#elemLibLayer` 与树/菜单/批量条样式。

## 判据与负向验证

- `check_element_library.mjs` 26/26（新增 10 条 P2 行为用例，含真右键事件、折叠落盘、
  多选批量、只读来源副行、未使用过滤）；`check_element_pick_ui.mjs` 31/31；
  `check_editor_bottom_dock.mjs` 11/11；`check_element_pick_contract.py` 24/24（新增
  P2 接线用例）；RPC wire 契约 45 方法/38 处字面量调用绿。
- Python：元素库测试 30 条绿（新增折叠落盘持久化 + 错误码用例）。
- 负向探针 `probe_m93_negative.py`：5 注入失败集逐项相等，md5 逐字节还原，复跑全绿。
  其中「资产右键不接线」注入暴露原用例只直调 `openLibMenu` 的假绿灯 ⇒ 补一条真触发
  `contextmenu` 事件的用例，注入才精确红。
- 全量 `check_all.py`：`FULL GATE PASSED` + `rc=0`。

## 红线（未动）

ref-only 接线不变；删除仍走引用护栏（右键删除复用行内链）；不恢复 `writeElement`/
`pickPlan`；分组文件不进 `elements/`；旧资产缺字段不迁移。
