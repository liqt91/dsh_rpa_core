# M69：影刀式元素编辑器（层级 + 属性 + XPath）

Status: `wip`

> **2026-11-13 立项**。维护者给影刀元素编辑器截图并拍板「这是我想要的元素编辑器」：
> 层级树（逐级勾选）+ 每级属性（勾选 / 匹配方式 / 可改值）+ XPath 定位。
> 前置：M68（编辑框收进 capture_kit，win32 原生）、`selector.path` 祖先链
> （`content.js::pathFor`，每级 `{tag, id, classes, nthOfType, fragment}`，主 css 就是
> fragment 以 `" > "` 连接——重组规则的事实源）。
> 不占 active 位（`M52-selector-advisor` 仍是主线）。

## 切片

- **S1 层级 + 属性的 css 编辑（本切片）**：`capture_kit/selector_tree.py`（纯函数：
  层级模型 + 勾选重组 + 转义）+ `edit_dialog` v3（browser 形态：左层级勾选、
  右属性行（勾选 / 精确·包含 / 值可改）、底部实时预览；desktop 形态维持 JSON 编辑）。
  重组规则：相邻勾选级 `" > "`、跳级 `" "`（后代），目标级锁定勾选。
- **S2 XPath**：编辑器加「默认选择器 / XPath」切换 + 从层级生成 xpath + **运行时
  `document.evaluate` 执行**（extension + executors + 契约）。**不许先上 toggle 再补
  执行**——那是假开关。
- **S3 校验元素 / 锚点 / 捕获相似元素**（影刀右上的能力）：需要活着的扩展会话，
  捕获结束后通道已收，得重开一个轻量会话。先记 BACKLOG。

## 判据

- `selector_tree` 纯函数：逐规则用例（id 截断、class 精确/包含、nth、跳级后代、
  转义、目标级锁定、全不勾回退 `*`）。
- `_edit_probe`：层级 checkbox / 属性行控件齐，勾选变化 ⇒ 预览文本变化；
  按钮不越客户区（沿 v2 判据）。
- 契约：保存的 css 来自重组、非法组合不落盘；旧资产无 path 时回退 css 编辑形态。

## S1 完成记录（2026-11-13 当日）

- `capture_kit/selector_tree.py`：`levels_from_path`（初始勾选与捕获主 css 逐字一致）、
  `compose`（id 截断 / class 精确·包含 / nth / **相邻 `" > "`、跳级 `" "`** /
  目标级锁定 / 全不勾回退目标级）、`css_escape`/`attr_escape`（与 content.js 同口径）、
  `summary`（层级文案）。
- `edit_dialog` 双形态：`show(levels=...)` 有层级 → 影刀式（左层级勾选 254→280px、
  右属性行：勾选 + 属性名 + 精确/包含下拉 + 值可改，底部实时预览，目标级 checkbox
  `EnableWindow(False)`）；否则维持简式。`on_save` 收到的 css 一律来自
  `compose`（树形态没有「手写选择器」这条缝）。
- api：`_capture_edit_model` 有 path 给 `levels` 否则退 `selector` 文本；`edit_element`
  详情同款。旧资产（无 path）自动回简式。
- 判据：`test_selector_tree` 9 测、contract 18/18（+层级接线/退化）、`_edit_probe`
  层级段（初始预览、勾选重组、目标级禁勾、保存拿重组结果）PASS、负向 10/10、
  FULL GATE PASSED。真机截图 `_dialog_tree.png` 目视确认。

S2（XPath）、S3（校验元素/锚点/捕获相似元素）未动，见 BACKLOG。

## S1 补：完整祖先链（2026-11-13，维护者「层级信息太少了」）

维护者要「目标 → 页面根」的**所有**层级（原来 `pathFor` 到 id / 6 级就停）：

- `content.js`：`pathFor` 改全链（`MAX_TREE_DEPTH=40`），主 css 的短链由
  `cssChainFor` 单独取（带 id 即停 / ≤6 级，行为逐字不变）；`path` 每层新增
  **`inCss`** 标记它是否组成主 css——全链与主 css 共享同一条链、同一个
  `pathEntryFor`，初始勾选 = inCss 的层。candidates 仍从短链截。
- `selector_tree.levels_from_path`：初始 `included = inCss`（旧资产无 inCss
  按全参与兼容）。
- 编辑框：层级超一屏（>10 层）给窗口原生 `WS_VSCROLL`，checkbox 按偏移重排
  （屏外 SW_HIDE）。★ pywin32 的 `SetScrollInfo` 的 SCROLLINFO 是**按位元组**
  `(mask,min,max,page,pos)`，min/max 必须成对——传 dict 或 2 元组都炸。
- EXT_BUILD 三方（background/content/manifest）升 0.9.3 ⇒ **Load unpacked 需点
  「重新加载」**（扩展是载入即快照，不重载跑的仍是 0.9.2 的短链）。
- 判据：`test_selector_tree` 10 测（+inCss 全链用例：初始勾选、往上勾一层
  相邻/跳级）、`_edit_probe` 全链段（>10 层出滚动条、屏外隐藏、滚到底目标级
  可见、初始预览只含 inCss）PASS、contract 18/18、负向 10/10。
