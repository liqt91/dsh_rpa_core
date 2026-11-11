# 分支职责拆分（2026-11-11）

拆分点 `ff36ed21e39249375619788a6ccf6531d0167f0f`（`ppx-only` 当时的 HEAD：
`docs(readme): 同步命令目录与里程碑状态，界定安装器排除范围`）。

从这一个提交同时长出两条分支，**拆分点上两棵树逐字节相同**：`workbuddy-suite/`
在此前几笔提交里合进来的历史属于共同基线，不回滚、不追溯清理。
拆分分的是**后续改动的车道**，不是把文件搬走。

本文件在两条分支上逐字节相同，是给人看的说明书；机器事实源是
`.harness/branch-scope.json`，执行者是 `.harness/scripts/check_branch_scope.py`。

## 两条分支各自干什么

| 分支 | 职责 | 独占域（只有它能改） | 不许碰 |
|---|---|---|---|
| `ppx-only` | PPX 桌面客户端：窗口、画布、会话、构建分发 | `.harness/demo/ppx_probe/` | `workbuddy-suite/`、`tests/contract/test_workbuddy_connector.py` |
| `plugin-only` | WorkBuddy 插件套件：connector / skill / extension / 上架资产 | `workbuddy-suite/`、`tests/contract/test_workbuddy_connector.py` | `.harness/demo/ppx_probe/` |

「后续改动仅围绕 ppx 客户端」= `ppx-only` 上不再落插件交付面的改动；
插件相关改动统一先切到 `plugin-only` 再动手。

### 三个运行时例外

PPX 客户端并不是完全不碰 `workbuddy-suite/`：会话服务
`workbuddy-suite/skills/workflow-studio/scripts/studio.py`、会话协议
`references/protocol.md`、npm 入口 `bin/rpa-skill.mjs` 是客户端跑起来的依赖。
这三个文件列在契约的 `shared_runtime`，两条分支都可改 —— 用目录一刀切会切断运行时。

### 共享支撑层

`src/rpa_core/`、`commands/`、`extension/`、`workflows/`、`tests/`、
`.harness/scripts/`、`docs/`、`package.json`、`pyproject.toml` 等不在任何独占域，
两条分支都可改。这是刻意的：能力层是两边共同的依赖，划成独占只会让客户端没法演进。
但改动要意识到它会影响另一条分支；需要同步时用 `git cherry-pick` 显式搬，
别整分支合并（那会把另一条分支的职责一起带过来）。

## 闸门怎么拦

`check_all.py` 里接了 `check_branch_scope.py`，四层判据：

1. **契约自洽**：字段形状、治理文件真存在、两条独占域不互相覆盖；
2. **接线哨兵**：把闸门从 `check_all.py` 摘掉，闸门自己会红；
3. **共同基线**：`split_point` 存在且是 HEAD 祖先，两分支的契约与闸门 blob 相同；
4. **越界**：当前分支相对拆分点的**已提交**改动 + **已暂存**改动里出现对方独占域即红。

不检查未暂存的工作区脏文件 —— 在制品不是提交意图，而且会误伤手测残留。
所以提交前可以先 `git add`，再跑一次闸门看有没有越界：

```bash
uv run python .harness/scripts/check_branch_scope.py
```

单分支 clone 里没有对方 ref 时，第 3 条的跨分支比对会打印 `skipped` 并放行，
其余三层照常生效 —— 不让 CI 因为只 fetch 一条分支而假红。

## 切分支

```bash
git switch plugin-only   # 插件相关改动
git switch ppx-only      # PPX 客户端改动
```

拆分时工作区里已有的 `workbuddy-suite/skills/workflow-studio/SKILL.md`、
`workbuddy-suite/verify/verify_suite.py` 两笔未提交修改是维护者的在制品，
**没有**被并进任何一条分支的提交；按新职责它们应落到 `plugin-only`，
处理前先确认，别在 `ppx-only` 上直接提交。

## 负向验证

`.harness/spike/probe_branch_scope_negative.py` 在隔离的临时 git repo 里注入越界，
证明各类破坏都会红、还原后回绿，并逐字节核回治理文件的 md5。
它不碰主仓的工作区与 index。
