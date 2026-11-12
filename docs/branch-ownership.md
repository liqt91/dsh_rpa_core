# 分支职责拆分（2026-11-11）

拆分点 `ff36ed21e39249375619788a6ccf6531d0167f0f`（`ppx-only` 当时的 HEAD：
`docs(readme): 同步命令目录与里程碑状态，界定安装器排除范围`）。

从这一个提交先长出 `ppx-only` / `plugin-only` 两条分支，同日再从 `ppx-only` 的
`cd74295` 长出第三条 `xhs-workbench`。**拆分点上几棵树逐字节相同**：
`workbuddy-suite/` 在此前几笔提交里合进来的历史属于共同基线，不回滚、不追溯清理。
拆分分的是**后续改动的车道**，不是把文件搬走。

本文件是给人看的说明书；契约把它列在 `branch_local`，允许各分支各写各的
（2026-11-12 起 `xhs-workbench` 分支多记了 R2/R3 的共享编辑面来龙去脉）。
机器事实源是 `.harness/branch-scope.json`，执行者是 `.harness/scripts/check_branch_scope.py`。

## 三条分支各自干什么

| 分支 | 职责 | 独占域（只有它能改） | 不许碰 |
|---|---|---|---|
| `ppx-only` | PPX 桌面客户端：窗口、画布、会话、构建分发 | `app/ppx.toml` 与 `app/package.json` / `pnpm-*` / `ppx.lock` 等客户端打包配置 | `workbuddy-suite/`、`apps/xhs-workbench/` 等 |
| `plugin-only` | WorkBuddy 插件套件：connector / skill / extension / 上架资产 | `workbuddy-suite/`、`tests/contract/test_workbuddy_connector.py` | `apps/xhs-workbench/`、PPX 客户端打包配置 |
| `xhs-workbench` | 小红书专用工作台：调度 / 快照 / 告警 / 本地 Web 台 | `apps/xhs-workbench/` | `workbuddy-suite/`、PPX 客户端打包配置 |

工作台的**采集执行不自己写**：只经 `rpa-core run <workflow.json>` 调用能力层，
不 import `src/rpa_core` 内部模块。因此 README 的 `scope.out`（排除调度器 / 数据库 /
FastAPI / React UI）描述的是运行时核心，工作台作为仓内旁路应用层不违例；
边界记录见 `.harness/adr/0022-xhs-workbench-scope.md`（只在新分支上）。

### 三个运行时例外

PPX 客户端并不是完全不碰 `workbuddy-suite/`：会话服务
`workbuddy-suite/skills/workflow-studio/scripts/studio.py`、会话协议
`references/protocol.md`、npm 入口 `bin/rpa-skill.mjs` 是客户端跑起来的依赖。
这三个文件列在契约的 `shared_runtime`，三条分支都可改 —— 用目录一刀切会切断运行时。

### 原型编辑面整体共享（2026-11-12，R2 → R3，三分支契约同步）

ADR 0022-R1 让工作台在运行时直接装载原型的 `app/api/api.py` 与 `app/gui/src/main.js`
（后者经 `vite build` 产出工作台托管的 `gui/dist`）。拆分点的原型够用时没有矛盾；
一旦工作台要**增强编辑能力**，整目录独占就会把合法改动判成越界，而原型改动只入
`ppx-only` 又会让工作台分支 checkout 后读不到。

R2 的做法是精确放行 `api.py` / `main.js` 两个文件。补拖拽时立刻撞上第二堵墙：
样式只能写进 `style.css`，而它仍归 `ppx-only`，于是被迫把 14 行 CSS 塞进
`main.js` 运行时注入。**逐文件放行是纯噪声**——编辑面本来就是一整棵树。

R3（维护者 2026-11-12 拍板）改为：契约的 `shared_runtime` 收一条目录前缀
`.harness/demo/ppx_probe/`，整棵原型目录不再归任何分支独占；`owner_of()` 扩展为
支持「以 `/` 结尾的条目按前缀共享」，优先级是**精确声明 > 目录前缀**，
所以 `ppx.toml` 等客户端打包配置仍能用精确的 `exclusive` 条目留在 `ppx-only`。
`check_scope_sanity()` 会检查目录前缀真实存在——路径写错不会报错，只会让本该独占的
路径悄悄变成无人认领。

两个文件都在 `must_match_across_branches` 里，`branch-scope.json` 与
`check_branch_scope.py` 在三条分支各提交一次、blob 逐字节相同。
ADR 0022-R3 记录了范围变化；本轮的编辑器改动因此回到 `index.html` / `style.css`
的正规写法，不再从 `main.js` 注入 DOM 与样式。

### 共享支撑层

`src/rpa_core/`、`commands/`、`extension/`、`workflows/`、`tests/`、
`.harness/scripts/`、`docs/`、`package.json`、`pyproject.toml` 等不在任何独占域，
三条分支都可改。这是刻意的：能力层是三方共同的依赖，划成独占只会让客户端没法演进。
但改动要意识到它会影响其他分支；需要同步时用 `git cherry-pick` 显式搬，
别整分支合并（那会把另一条分支的职责一起带过来）。

`workflows/` 在这里有一条更宽的口径：流程文件**允许各分支各自跟踪**，既不设
独占域，也不要求三分支同步。业务侧流程跟着用它的分支走（小红书采集流程只在
`xhs-workbench` 入库），通用示例流程留在共享层按需取用。要跨分支复用仍用
`cherry-pick`，别整分支合并；契约的 `must_match_across_branches` 也不含此目录。

## 闸门怎么拦

`check_all.py` 里接了 `check_branch_scope.py`，四层判据：

1. **契约自洽**：字段形状、治理文件真存在、`shared_runtime` 的目录前缀真存在、
   各条独占域不互相覆盖；
2. **接线哨兵**：把闸门从 `check_all.py` 摘掉，闸门自己会红；
3. **共同基线**：`split_point` 存在且是 HEAD 祖先，契约与闸门在**所有**分支上
   blob 相同（逐对比对，缺哪条 ref 就只对那条打印 `skipped`）；
4. **越界**：当前分支相对拆分点的**已提交**改动 + **已暂存**改动里出现
   其他分支独占域即红。

不检查未暂存的工作区脏文件 —— 在制品不是提交意图，而且会误伤手测残留。
所以提交前可以先 `git add`，再跑一次闸门看有没有越界：

```bash
uv run python .harness/scripts/check_branch_scope.py
```

单分支 clone 里没有对方 ref 时，第 3 条对缺失的 ref 明确打印 `skipped` 并放行，
其余三层照常生效 —— 不让 CI 因为只 fetch 一条分支而假红。

## 切分支

```bash
git switch plugin-only      # 插件相关改动
git switch ppx-only         # PPX 客户端改动
git switch xhs-workbench    # 小红书工作台改动
```

拆分时留下的 4 笔在制品已按职责收口：`workbuddy-suite/skills/workflow-studio/SKILL.md`
与 `workbuddy-suite/verify/verify_suite.py` 两笔修改**只提交到 `plugin-only`**；
`workflows/test/` 两笔草稿删除在三分支同步落地；`workflows/xiaohongshu/` 只在
`xhs-workbench` 入库。`ppx-only` 工作区里那两笔插件侧差异随后还原成 HEAD 版本 ——
内容已安全落在 `plugin-only`，不要在 `ppx-only` 上重复提交（会触发越界闸门）。

## 负向验证

`.harness/spike/probe_branch_scope_negative.py` 在隔离的临时 git repo 里造出
三条分支并注入越界，证明各类破坏都会红、还原后回绿，并逐字节核回治理文件的 md5。
覆盖两个方向的越界、三条分支各自的契约分叉、摘接线、坏拆分点、前缀覆盖，
以及「共享运行时放行」「缺一条 ref 打印 skipped 仍放行」两条正向对照。
它不碰主仓的工作区与 index。
