# M106 — 影刀筛选器对标收口：属性化候选补齐 + 运行期唯一性

Status: `done`

决策见 `.harness/adr/0023-selector-optimizer.md` §5（2026-11-25 维护者拍板 **B 档 + 运行期唯一性要做**）。
本单是 M105 的后续旁支，M105 保持 `done`，不重写历史任务单。**2026-11-25 本轮只记录、不实施。**

## 背景与取证

复看 `rea-yigndao/影刀网页元素筛选器优化报告.md` §1.1，影刀两条路径必须分清：

- **纯 CSS 路径** `DomUtils.buildCSSPath`：不捕任何属性，只取标签层级链，shadow 插 `xbotShadowRoot`；
- **属性化选择器** `SelectorNode.buildFromElement` → `extractAttributes`：捕属性、按
  `getTagNameOrder` 排序、`getAttrHandler` 特化，最终 `selector2XPath` 统一转 XPath。

M105 已复刻属性化的**机制**，但落在候选层、产 CSS/XPath 混合字符串，且落盘模型与影刀
（`SelectorModel = Path + Xpath`，无独立 Css 字段）不同——这是 ADR 0023 §1.1 的有意取舍，
不在本单推翻。本单只补报告里有、我们确实缺的能力。

当前基线（HEAD `596d81e`）：

| 缺口 | 事实位置 |
|---|---|
| 普通属性只产完整名值，无「只匹配属性名」降级 | `extension/content.js::optimizedCandidatesFor` |
| `index-of-type` 被显式跳过，只有主 css 的 `nth-of-type` 兜底 | `extension/content.js` 候选循环 |
| 勾选态只初始化编辑器显示，不回喂候选生成 | `src/rpa_core/capture_kit/selector_tree.py::levels_from_path` |
| `TAG_ATTR_ORDER` 无 `svg` 特化 | `extension/content.js::attributeOrderFor` |
| 只在捕获期实测 `matchedCount`，运行期无 Stats 回路 | `extension/content.js::countFor`；报告 §8 |

## 范围

| 切片 | 内容 | 验收 |
|---|---|---|
| S1 | `handleDefaultAttr` 降级：名=值过度收窄 → `[attr]` 存在性候选 → 回滚 | JS 切片：覆盖到降级分支；逐条实测 matchedCount |
| S2 | `index-of-type` 合成属性候选（保留主 css 的 `nth-of-type`，不互相替代） | 同类兄弟无 class/属性时候选层非空 |
| S3 | 用户勾选属性优先两轮：勾选态回喂候选生成，失败退回全候选 | 原生框 / Web 框两条产出路径成对；勾选改变结果 |
| S4 | `svg` 属性特化顺序并入 `TAG_ATTR_ORDER` | 与报告 §2 的 `tagOrderMap` 键集合核对 |
| S5 | 运行期唯一性 Stats：命中数 >1 触发加属性/锚点，不是捕获时测一次就完 | 真实命中数变化驱动；负向注入必须红；不改 `page.call` 签名 |

## 硬约束

1. `selector.css` + `selector.path` 仍是编辑器唯一事实源；XPath 仍只做候选，不升主选择器。
2. 不改 `page.call` 签名——多个测试桩按旧签名伪造 session；仍走 `xpath:` / `shadow:` 前缀分派。
3. S3 的勾选态回喂必须收在统一函数里，原生 `ElementEditDialog` 与 Web 对话框都调它；
   禁止在控件里各写一份（M104/M105 已经踩过两次产出路径漂移）。
4. 所有新候选仍走同一个 `push()`：实测命中数、去重、0 命中丢弃。
5. 开发期硬切，不写数据迁移：旧资产缺勾选字段时按现有推断回退。
6. 门禁只看末行 `FULL GATE PASSED` + rc=0；新判据必须负向验证 + 元验证 + 逐字节 md5 还原。

## 门禁与验证

- `node scripts/check_capture_helpers.mjs`（属性降级 / index-of-type / svg 顺序）
- `node scripts/check_selector_query_kinds.mjs`（运行期分派不被 S5 破坏）
- `pytest tests/unit/test_selector_tree.py tests/contract/test_web_candidate_ranking.py`
  `tests/contract/test_verify_element.py`
- `.harness/scripts/check_element_pick_contract.py`、`scripts/check_capture_broadcast.mjs`、
  `scripts/check_verify.mjs`、`scripts/check_element_pick_ui.mjs`
- 新增 S3 成对判据与 S5 运行期判据；负向探针含元验证（摘掉接线必须变绿）
- 全量：`.venv/Scripts/python.exe .harness/scripts/check_all.py`

## 状态（2026-11-25 全部完成）

- [x] S1 属性名降级
- [x] S2 index-of-type 候选
- [x] S3 勾选属性优先两轮（含两条产出路径成对）
- [x] S4 svg 属性特化
- [x] S5 运行期唯一性 Stats
- [x] 负向验证 + 全门禁 + 台账同步

证据：`node scripts/check_capture_helpers.mjs` / `check_selector_query_kinds.mjs` /
`check_element_pick_ui.mjs`（44/44）全绿；pytest 1187 passed / 20 skipped / 2 xfailed；
`.harness/spike/probe_m106_negative.py` 5 注入 + 1 元验证全命中并逐字节 md5 还原；
`check_all.py` 末行 `FULL GATE PASSED` + rc=0。EXT_BUILD 三方 `0.11.0`，用户须重载扩展。
