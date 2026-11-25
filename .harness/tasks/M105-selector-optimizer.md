# M105 — 网页元素筛选器优化（影刀式全量复刻）

Status: `wip`

决策见 `.harness/adr/0023-selector-optimizer.md`（2026-11-25 维护者拍板「全量复刻」）。
主线仍是 M52（选择器优选），本单是旁支：改生成侧与运行期 dispatch，不改 M52 的排序引擎语义。

## 范围

| 切片 | 内容 | 验收 |
|---|---|---|
| S1 | 属性提取过滤 + 属性优先级 + 贪心唯一化（content.js 纯函数层） | JS 切片用例：style/纯数字/长值/双引号被剔；测试属性优先；逐属性试到 `matchedCount==1` |
| S2 | 主 CSS 稳定化：剔状态 class / 裁哈希后缀 / 不稳定 id 不主锚 / 最能消歧 class / nth 必要才补 | `pathEntryFor` 与 `cssSelectorFor` 同源；旧「首类不足补 nth」期望改为新期望；`usedClasses` 与勾选态成对 |
| S3 | 特化：class 多值累加、文本去动态数字、URL 去 query、冗余层级贪心删除、shadow 分段 | 每条产出实测 `matchedCount`；新 kind 全部在宿主表登记（跨端成对判据） |
| S4 | 运行期 dispatch：xpath / shadow 分段；capture 校验与 penalty 登记；EXT_BUILD bump | 主 css 仍走 CSS；候选按 kind 分派；未知 kind 报错；三方版本一致门禁复活 |

## 硬约束

1. `selector.css` 与 `selector.path` 仍是编辑器的唯一事实源；新算法只能改「怎么生成」，不能
   把主选择器变成不可编辑的 XPath（XPath 仅做候选）。
2. `pathEntryFor` 是片段生成的唯一权威：主 css 与 `selector.path[i].fragment` 必须逐字同源。
3. 所有新候选走同一个 `push()`：实测命中数、去重、命中 0 丢弃；几乎不命中就不该落盘。
4. 不写数据迁移：`usedClasses` 缺席时回退首类（旧资产原样可读），开发期硬切。
5. 门禁只看末行 `FULL GATE PASSED` + rc=0；新判据必须负向验证 + 元验证 + md5 还原。

## 取证基线（动手前）

- 主 CSS：`pathEntryFor` 首类 + 必要 nth；候选只有 id / 7 属性 / 路径截断。
- 运行期：`domOp.query` 一律 `querySelectorAll`；全仓无 XPath、无 shadow DOM。
- M52 明确钉住「首类不足仍补 nth」，本单要改这条既有契约。
- EXT_BUILD `0.9.7`（三方一致），但活跃三方门禁已随 `test_capture_extension.py` 删除而消失。

## 门禁与验证

- `node scripts/check_capture_helpers.mjs`（桩 DOM 扩到 `[attr^=]` / `[attr*=]` / `::shadow` / XPath 不进桩）
- `pytest tests/contract/test_web_candidate_ranking.py tests/contract/test_verify_element.py`
- `.harness/scripts/check_element_pick_contract.py`、`scripts/check_capture_broadcast.mjs`、
  `scripts/check_verify.mjs`、`scripts/check_element_pick_ui.mjs`
- 负向：`probe_m51b_negative.py`、`probe_m52_nth_gating_negative.py` 锚点更新；
  新增 M105 专用探针（含元验证：摘掉新判据接线必须变绿）
- 全量：`.venv/Scripts/python.exe .harness/scripts/check_all.py` → `FULL GATE PASSED`

## 状态（2026-11-25 全部完成）

- [x] S1 生成侧纯函数
- [x] S2 主 CSS 稳定化与编辑器勾选同步
- [x] S3 特化与 shadow 分段
- [x] S4 运行期 dispatch 与契约
- [x] 负向验证 + 全门禁 + 台账同步
