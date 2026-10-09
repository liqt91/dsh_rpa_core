# M51：定位方案的供给面扩容（XPath + 候选生成对齐）

Status: `planned`

> 本轮**不实现**，只留证据与范围（由 M47.10 ②⑤ 派生）。

Plan: M47.10 ②⑤ 派生 / 维护者实测「相比影刀少了 xpath」「从没看到备选定位」

## 为什么两件事合成一片

它们**同一根**：都在问「除了那条主 css，还有别的定位方案可选吗」。

- **⑤ XPath**：换一种**定位语言**（影刀有，我们只有 CSS）；
- **② 候选生成太窄**：同一种语言里，**备选方案供给不足**（`candidatesFor` 只推
  id + 7 属性，而主 css 走 class 路径 ⇒ 现代前端组件候选恒空）。

两者都要动**同样五层**，混在两个提交里会让「界面回归」与「定位语义回归」分不开，
所以合片、单独立项。

## 关键发现（本轮实测，必须留档）

### ⑤ 全链路只认 CSS

- 扩展 `content.js` 的 `page_call` 用 `document.querySelector` / `querySelectorAll`；
  **全仓无 `document.evaluate`**；
- 执行器（`browser.py`）的 `_page_call_with_fallback` + `_element_candidates`
  也按 CSS 反查、按序重试；
- 元素资产「值拷贝 + 按 css 反查」⇒ **改主选择器语言会牵动自愈候选的失效判定**。

⇒ 不是「加一个下拉框」的事：`selector.kind` 得成为契约判别子（`css` | `xpath`），
扩展、契约、执行器、自愈、GUI 五处一起分派。

### ② 候选生成面（**用户观察正确**）

- 生成侧 `extension/content.js::candidatesFor`（第 193-215 行）**只推**：
  ```js
  const CANDIDATE_ATTRS = [
    "data-testid", "data-test", "data-qa", "name",
    "aria-label", "placeholder", "title",
  ];
  if (el.id) push("id", "#" + CSS.escape(el.id));
  for (const name of CANDIDATE_ATTRS) push("attribute", attrSelectorFor(el, name));
  ```
- 主 css 由 `pathEntryFor`（第 141 行）生成：`#id` 或 `tag.class[0]` + `:nth-of-type(n)`；
- 现代前端（React / Vue）组件**普遍既无 id 也无那 7 个属性** ⇒ `candidatesFor`
  返回空 ⇒ 候选列表恒空 ⇒ **运行期自愈（M28）实际永不生效**。

⇒ 修法：让候选生成**对齐主 css 实际用的 class 路径**（推 class 组合、`[class*=]`、
属性组合等），而不是把候选区折叠/删掉。

## 范围（待实现）

- [ ] **A. XPath**
  - [ ] 扩展：新增 `document.evaluate` 查询路径，`capture_verify` / 预览 / 执行都支持；
  - [ ] 契约：`selector.kind`（`css` | `xpath`）进 `ElementDescriptor` 与校验；
  - [ ] 执行器：`browser.py` 按 `kind` 分派查询；`_element_candidates` / 自愈同步；
  - [ ] GUI：编辑器给出选择器语言选择 + 对应校验（`css_problems` → `selector_problems`）。
- [ ] **B. 候选生成对齐**
  - [ ] `candidatesFor` 推 class 组合 / `[class*=]` / 属性组合（仍**全部实测 matchedCount**）；
  - [ ] 契约测试钉住「主 css 走 class 路径时，候选不得为空」（用真实组件形状的 fixture）；
  - [ ] 补一条端到端：捕获 → 候选非空 → 人为改主 css 使其失效 → 自愈按候选回退命中。

## 相关证据（本轮实测）

- `extension/content.js:193` `CANDIDATE_ATTRS`（7 个属性）；
- `extension/content.js:197` `candidatesFor`；
- `extension/content.js:141` `pathEntryFor` / `:159` `:nth-of-type`；
- `extension/content.js:180` `cssSelectorFor`（主 css 来源）；
- 运行期自愈消费面：`browser.py::_page_call_with_fallback` + `_element_candidates`。
