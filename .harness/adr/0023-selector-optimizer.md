# ADR 0023：网页元素筛选器优化（影刀式贪心最小化 + 动态值清洗）

状态：**定案（2026-11-25 维护者拍板「全量复刻」）**

日期：2026-11-25
关联：ADR 0018（桌面 locator 契约）、ADR 0022（工作台旁路应用层）、
M51-B（候选供给扩容）、M52（选择器优选）、M69（完整祖先链）、M104（勾选态持久化）、
`docs/yingdao-gap-catchup.md`（影刀对标缺口清单）

信源：`D:/Users/Administrator/Documents/代码/rea-yigndao/影刀网页元素筛选器优化报告.md`
（2026-11-25 版，17 节，基于明文 `reworkx_bundle.js` + IL 交叉印证）。

## 0. 拍板与问题

维护者看完影刀报告后拍板：**「全量复刻」**——不是只加几条候选，而是把报告 §9 的十项
机制完整落到我们的捕获器与运行期。

先取证，不靠印象：

| 现状 | 证据 |
|---|---|
| 主 CSS：带 id 即终止；否则最多 6 级，每级只取**首类** + 必要时 `:nth-of-type` | `extension/content.js::pathEntryFor` |
| 候选：id + 7 个固定属性 + 祖先链截断（含去 nth 变体） | `candidatesFor` / `CANDIDATE_ATTRS` / `pathSuffixSelectorsFor` |
| 排序：唯一性硬门 + penalty（M52 S2），但**生成器不会主动凑唯一** | `model/selector_ranking.py::rank_web_candidates` |
| 运行期只认 CSS：`document.querySelectorAll`，**无 XPath、无 shadow DOM** | `extension/background.js::domOp.query`；全仓 `xpath` 零命中 |
| M52 已把「首类不足时仍补 nth」作为**已知取舍写进门禁** | `.harness/tasks/M52-selector-advisor.md` 补丁段；`scripts/check_capture_helpers.mjs` L226-232 |

因此本轮同时触碰「已有设计取向」，按规矩先取证、给选项，维护者选了第三档（全量）。

## 1. 决策

### 1.1 两层产出，职责分开

- **主 CSS（可编辑层）**：仍是一条 CSS 路径，继续由 `selector.path` 的层级勾选模型表达，
  保证编辑器、`capture_kit/selector_tree.py`、原生 `ElementEditDialog` 不用重写。
  但片段生成从「首类 + 必要 nth」升级为影刀式稳定化：
  - 属性/类名过滤：`style`、纯数字、超长值、同时含两种引号的值不采；
  - class 候选剔除状态类（`hover` / `active` / `xxx-on` / `xxx-open`）与构建哈希后缀
    （`-({2,})[a-zA-Z\d]{5,}$` / `_{2,}` 同规则）；
  - **按「最能消歧」选 class**（可多个），不再固定首类；nth 仍只在片段仍不唯一时补；
  - 不稳定 id（`isStableValue`：按 `-`/`_` 切分后每段须全字母）不当主锚点，
    与「临时摘掉不稳定 id 再构建」等价，但**不真的改 DOM**，避免破坏页面与其他脚本。
  - `selector.path` 每级新增 `usedClasses` 字段（进 fragment 的 class），
    `levels_from_path()` 优先用它初始化勾选；缺字段的旧资产回退首类，开发期硬切不做迁移。

- **候选层（自愈层）**：新增影刀算法产出的写法，全部实测 `matchedCount` 后交给既有
  `rank_web_candidates` 择优；主 CSS 命中时它们不参与，主 CSS 失配时按 `(count, penalty)` 逐个 try：
  - `attribute`：按 `getTagNameOrder` 优先级逐属性试到唯一（测试属性 > aria-label > name > id > title > class）；
  - `class`：剔状态类/哈希后逐个 class 累加，命中唯一即停；
  - `text` → `kind="xpath"`：`innerText` 去首尾动态数字，用 `contains`/`starts-with`/`ends-with`；
  - `link`：`href`/`src` 去 query string 后按路径 contains 匹配（CSS `[href*="…"]`）；
  - `pruned`：唯一化后贪心删中间层级，命中集不变就留下（语义标签白名单不删）；
  - `shadow`：`::shadow` 分段拼接，运行期逐 shadowRoot 穿透查询。

### 1.2 运行期必须补的能力

`background.js::domOp.query` 从「一律 `querySelectorAll`」升级为按 `kind` 分派：

- CSS（含 `::shadow` 分段）：`selector` 按 `::shadow` 切段，首段在主文档查，后续段在前一段结果的
  `shadowRoot` 里查；
- `xpath`：`document.evaluate(selector, document, null, ORDERED_NODE_SNAPSHOT_TYPE, null)`；
- 未知 kind 报明确错误，不静默当 CSS。

`model/capture.py` 校验放宽到新 kind；`selector_ranking.py` 登记新 kind 的 penalty
（**不登记会落到 `WEB_UNKNOWN_KIND_PENALTY`，新来源永远排不上**）；
`executors/browser.py::_page_call_with_fallback` 把 kind 透传给 `domOp`，
主 selector 仍按 CSS 处理（主 CSS 可编辑模型不变）。

### 1.3 不做的部分（如实记账）

- 不引入 XPath 作为**主选择器**：编辑器层级勾选模型只表达 CSS 路径，XPath 只做候选；
- 不生成影刀式 `SelectorNode` 链与 `selector2XPath` 的完整 AST：我们直接产 CSS/XPath 字符串，
  不落中间对象；
- 不改 iframe 跨文档查询（当前宿主只查主文档，属另一条 ADR）；
- 不动桌面腿 locator（它有自己的 penalty 表与负向验证）。

## 2. 后果

- `extension/content.js` 增一个可单测的纯函数区（属性过滤、优先级表、贪心唯一化、
  文本/链接处理、冗余删除、shadow 分段），由 `scripts/check_capture_helpers.mjs` 切片求值；
  主 css 与路径树**仍出自同一个片段生成处**，不允许两处各拼一遍（M69 的契约）。
- `EXT_BUILD` 三方（content.js / background.js / manifest.json）bump；用户必须**重新加载扩展**，
  Load unpacked 是快照，不重载等于没改。
- M52 钉住的「首类不足仍补 nth」现状判据改为新期望；`probe_m52_nth_gating_negative.py`
  与 `probe_m51b_negative.py` 的注入锚点同步更新（锚点从常量取，不硬编码 build 号）。
- 全量门禁仍为唯一验收：`check_all.py` 末行 `FULL GATE PASSED` + rc=0；
  新判据必须做**负向验证 + 元验证**（摘掉接线必须变绿），逐字节还原核 md5。

## 3. 取证过程中发现的既有缺口（本轮一并补）

1. 全仓无 XPath 能力（只有 `docs/yingdao-gap-catchup.md` 把它列在未做项 E3）；
2. 全仓无 shadow DOM 穿透查询；
3. 历史 `tests/contract/test_capture_extension.py` 已删，EXT_BUILD 三方一致**当前无活跃门禁**，
   两个旧负向探针里的 I6/I4 已失效（对照跑会因测试文件不存在而失败）——本轮补回成对判据。

## 4. 分支治理

本改动落在 `extension/`（共享运行面），不独占：`xhs-workbench` / `ppx-only` / `plugin-only`
三分支都消费扩展。`branch-scope.json` 不改；另两分支没有各自的扩展改动，
本轮只在本分支落地，是否同步由维护者另拍（同 ADR 0021 的迁移教训）。
