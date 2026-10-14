# STATE — 当前状态（开工先读这一份）

> 本项目的**开机入口**。读它就够 —— 不要一上来全文读 `PROGRESS.md`（append-only 时间线，最大件）
> 和 `feature_list.json`（M63-S5 起已压到十几 KB）。需要细节时按 §二 的指针去取。
>
> §一 的 `active_plan` / `active_feature` 与 `.harness/project_state.json` 由门禁
> `.harness/scripts/check_docs.py` **逐值比对，不一致即红**（M62）—— 所以本文件不会悄悄过期。
> 台账字段的长度上限由 `.harness/scripts/check_ledger.py` 盯着（`title ≤ 120` / `notes ≤ 2000` /
> `PROGRESS` cutoff 之后 `evidence ≤ 400`）。

## 一、现在在哪

| 项 | 值 |
|---|---|
| 阶段 `phase` | `command-parity-extension-editor` |
| 当前里程碑 `active_milestone` | **M52** selector-advisor |
| 当前计划 `active_plan` | `.harness/tasks/M52-selector-advisor.md` |
| 当前特性 `active_feature` | `selector-advisor` |
| 下一个 `next_milestone` | M51 selector-supply-expansion |
| 最近收口 `completed_milestone` | M47.12 browser-shot-viewport-offset-from-window |

## 二、开工读法

1. **要「现在在做什么 / 下一步」** → 读完 §一 就到此为止。
2. **要「最近发生了什么」** → `grep '^2026-1' .harness/PROGRESS.md | tail`，**别顺序读全文**。
3. **要「某个里程碑的证据」** → `grep '<Mxx>' .harness/PROGRESS.md`，再读 `tasks/<Mxx>-*.md`。
4. **要「特性清单」** → `.harness/feature_list.json`，只看 `id` / `passes`（每条 `title` 已是一句话）；
5. **要「排期事实源」** → `.harness/project_state.json`；`notes` 现在只有几百字符（只记跨里程碑的决策），可以直接读。
6. **要「当前欠账」** → `.harness/tasks/BACKLOG.md`（里程碑总表）+ 当日 `.workbuddy/memory/`。
7. **要「某条 title / notes 的原文」** → `.harness/feature-titles-archive.md` / `.harness/notes-archive.md`
   （M63-S5 缩短前的全文，**仅存档**）。

## 三、各类文档去哪找

| 我要找… | 看哪里 |
|---|---|
| 门禁怎么跑、踩过什么坑 | `.harness/README.md` |
| 工程铁律（不变量） | `.harness/invariants.md` |
| 依赖方向与分层 | `.harness/architecture.md` |
| 为什么这么设计（决策历史） | `.harness/adr/` |
| 里程碑细则与验收口径 | `.harness/tasks/Mxx-*.md` |
| 某个专题的现状 | `docs/`（**部分文档自带「历史提案」banner，先看头部状态行**） |
| 指令参数 / 优化规划 | `.harness/commands-plan.md`（**≠** `.harness/tasks/BACKLOG.md`） |
| 影刀能力差距 | `.harness/yingdao-gap-matrix.md` |

## 四、别照做的几件事

- **别全文读** `PROGRESS.md`（append-only 时间线，是仓库最大件）——用 §二 的 `grep` 取尾部或按里程碑取。
  `feature_list.json` 与 `project_state.notes` 经 M63-S5 治理后都可以直接读。
- **历史条目永不改写**：`PROGRESS.md`、`adr/**`、已完成任务单里的路径引用，可能指向
  **当时存在、现已删除**的文件（playwright / bsk 时代产物）—— 那是历史记录，不是死链。
- 门禁跑法：`uv run python .harness/scripts/check_all.py`，只认**末行 `FULL GATE PASSED` + 退出码**。
- 只本地提交；`push` 需在当前消息里明确授权。
