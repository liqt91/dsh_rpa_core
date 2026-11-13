# M70：小红书工作台主流程重写（七步采集 + 逐帖详情/评论）

Status: `done`

> **2026-11-13 立项**。维护者给出七步口径，要求重写
> `apps/xhs-workbench/workflows/xhs_product_search.json`：
> ①打开小红书定位搜索框 ②点搜索框输入关键词并回车 ③等搜索结果 ④若出现点点 AI
> 搜索结果框则点关闭 ⑤点筛选，依次点「最新」「一天内」 ⑥等结果重新载入
> ⑦逐个点开帖子，抓链接/标题/正文/点赞/收藏/评论数，滚动评论区逐条抓评论，
> 结果写回工作台。
> 前置：M68/M69（元素编辑器与全链层级，本次不改）；工作台硬编码消费
> `outputs.grabItems.outputs.result`（`snapshots.py` / `runs.py` / `verify_live.py`）。

## 关键取舍（都是只读取证后定的）

- **主路径按回车**（用户明确要求），但真机踩过「合成 Enter 不触发小红书搜索、
  URL 停在 /explore」⇒ 加 `probeSearch` + `if needFallback`：只在 URL 没进结果页时
  才用 `encodeURIComponent` 拼关键词兜底。
- **兜底导航与逐帖打开都不许用 `browser.navigate`**：navigate 顶层 schema 不收
  `sessionId`，中途再调会**新开标签页**，之后所有 sessionId 指错 tab。统一走
  `browser.executeScript` 的 `location.href`（MAIN world，同 tab），全程只一个
  session。契约 `test_single_tab_session_is_used_and_closed` 钉死这一点。
- **逐帖累积用 `sessionStorage`**：`pageEval` 注入 MAIN world 且是**同步函数体**
  （返回 Promise 不会被等），所以「滚动评论区 → 等加载 → 再滚」拆成三组
  `scrollComments* + sleepComments*`，最后 `grabComments` 一次性收，`appendPost`
  push 进 `sessionStorage`，`grabItems` 汇总返回 `JSON.stringify(数组)`。
- **工作台字段扩展**：`Product` 增 `likes/collects/comments/commentList`，全部带默认值，
  老快照（只有 href/title/text）读出来补空值，不做数据迁移（invariants #15）。
  前端 `renderProducts` 加「互动数据」列与评论 `<details>` 展开。
- **按钮按可见文本查找**（`筛选`/`最新`/`一天内`，AI 框按「点点」文本 + 关闭按钮），
  不吃死易变的 class。真机首次跑需按实际 DOM 校准，见 BACKLOG。

## 判据（都做过负向验证）

- `test_workflow_contract.py` 重写为 13 条：inputs 精确集合、分支体内引用顺序
  （`walk` 必须下钻 then/else/catch，否则整条分支是判据盲区）、全部 18 个
  executeScript 的函数体形状、单 tab session、七步顺序、回车+兜底、AI 框条件、
  筛选顺序、等待/截图、逐帖滚动与汇总、落盘链路。
- `negative_xhs_workflow.py` **14/14**（13 注入 + META 元验证：放宽 N3 命中的断言后
  同一注入变绿 ⇒ 证明红是它报的）。期望失败集按推理补全：N8/N10 首轮漏报，
  补齐的是「IIFE 也让 grabComments 丢选择器」「拼接引用让 `${inputs.shotPath}`
  从流程消失」两条真实契约，不是照抄实际红灯。
- `check_xhs_products_view.mjs` 进默认门禁：切出 `renderProducts` 用 DOM shim 真跑
  （互动数据/逐条评论/老数据/XSS 转义）+ Python `to_json` 键 ↔ 前端 `it.<key>`
  **双向对账** + 元验证。`negative_xhs_products_view.py` **3/3**（读侧 2 + 写侧 1）。
  ★ V1 注入暴露元验证原本拿「已漂移的 fnSrc」自测会恒绿，已改为先断言基线干净。
- 应用层 189 测、`ruff`、`rpa-core validate` (`valid: true`)、`FULL GATE PASSED`。

## 欠账

- 真机首次运行校准 DOM 选择器（帖子详情标题/正文/赞藏评、评论区容器、点点 AI 框、
  筛选弹层）；这些是通用候选 + 文本兜底，没在真机验过。
- `verify_live.py` 的 `search_effective` 读 `waitResults.url`；若真机上回车直接生效，
  该节点是 `browser.waitLoad` 取当前 URL，行为已对齐，无需改。
