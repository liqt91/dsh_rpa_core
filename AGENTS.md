# rpa_core agent notes

## Product boundary

This repository is a clean-room RPA core experiment. Do not import runtime code from the sibling `rpa_script` project. Legacy files may only be copied into `tests/fixtures/` as inert test data.

## Product direction（未来技术路线，ADR 0016）

- **GUI（`rpa-core gui`）是唯一主力形态**：新增能力优先且默认落 GUI。
- **Web 编辑器（`rpa-core devserver`）退为可选形态**：不为它补齐 GUI 已有能力；仅在后端
  能力层复用或成本极低时顺带维护（`devserver/static/` 冻结演进，不删除）。
- GUI 独立运行**不需要任何 web 服务器**：编辑器能力进程内复用 `DevServerApp` 等库，运行走
  `rpa-core run` 子进程，扩展通道走 Native Messaging（ADR 0015）。
- 后端能力层（model/catalog/compiler/runtime/executors/workers/extension_exec/
  local_transport）与宿主形态无关，是唯一事实来源。

## Host experiment（PPX 壳，ADR 0019）

- **Qt 源码冻结**：`src/rpa_core/gui/` 留着不动、也不改 —— 不改功能、不重构、不删文件、
  不为原型做适配。判据是机械的：`.harness/gui-freeze.json` 记录逐文件 sha256，
  `.harness/scripts/check_host_boundary.py` 在门禁里比对，任何改动都红。
- 确需解冻时，**先问维护者**，再跑 `check_host_boundary.py --update-freeze` 重建基线，
  并在提交信息里写明原因（那一步的基线 diff 就是签字）。
- PPX 原型只待在 `.harness/demo/ppx_probe/`：不进 `src/`、不进主 `pyproject.toml`、
  不往主 venv 装 PPX。它只能通过 RPC 薄层消费能力层，不得 import `PySide6` 或 gui 的
  UI 模块（唯一豁免 `rpa_core.gui.i18n`）；能力层也不得反向依赖 `ppx_py`。
- 迁移讨论的门槛是 ADR 0019 §2.3 的三条实证（覆盖层 / 打包分发 / 桌面集成）。
  三条没过之前，不讨论 14.7k 行 Qt 的处置。

## Commands

```text
Sync:        uv sync --all-groups --extra gui
Test:        uv run pytest                       # 全量（提交前必跑）
Test (fast): uv run pytest -m "not slow"         # 日常：跳过 6 项「等真实时间」用例，省约 50s
Lint:        uv run ruff check .
Architecture:uv run python .harness/scripts/check_architecture.py
Full gate:   uv run python .harness/scripts/check_all.py
             （默认不含真实桌面 E2E——弹窗抢前台；需要时加 --with-desktop-e2e）
             （门禁里的 pytest **不带** `-m`，永远是全集）
CLI:         uv run python -m rpa_core.cli
```

> `--all-groups` 只覆盖 dependency-groups（dev），**不含** optional-dependencies；
> GUI 是主力形态，`gui` extra（PySide6/QDarkStyle）必须显式带上，否则 `rpa-core gui`
> 会报 `GUI_EXTRA_MISSING`。用 `uv sync --all-groups`（不带 `--extra gui`）会把已装的
> gui extra **清掉**。

## Non-negotiable rules

1. Control flow is workflow AST, never a command plugin.
2. A command has one manifest; executors contain implementation only.
3. Handlers return `CommandResult`; they never mutate orchestrator counters, variables, or logs.
4. Browser operations require an explicit session id after launch.
5. User Python never runs in the orchestrator process.
6. No `eval`, `exec`, dynamic module hot replacement, or online source editing.
7. One run uses one immutable command catalog snapshot.
8. Every run reaches a terminal state (succeeded, failed, cancelled, or abandoned) or the persisted `paused` state; a paused run must later be resumed or explicitly abandoned.
9. Cross-platform support means stable contracts plus capability-aware drivers, not identical desktop behavior on every OS.
10. Do not add FastAPI, a database, UI, MCP, scheduling, or installation concerns before the first vertical slice passes its acceptance tests.
11. Executors must propagate task cancellation and release attempt-owned resources before `execute()` exits.
12. A run is successful only after both `runFinished` and `result.json` persist successfully.
13. Commit locally only; never `git push` unless the user explicitly asks for it in the current message.
14. `src/rpa_core/gui/` is frozen (ADR 0019): do not edit, refactor, or delete files there while the
    host experiment runs. Unfreezing is an explicit act — update the freeze baseline and say why.

## Documentation language

- 面向项目维护者阅读的任务、计划、验收、进度说明和 ADR 正文使用中文。
- 代码标识、命令、错误码、JSON 字段、状态机取值和外部协议名保留英文。
- 机器读取的 JSON key 不翻译，避免破坏工具契约。

## Workflow

- Read `.harness/STATE.md` at session start — it is the single entry point (current milestone,
  active plan, next, and how to find everything else).
- Read `.harness/PROGRESS.md` / `.harness/feature_list.json` only when details are needed;
  they are ~640 KB combined and mostly history. Do not read the whole `project_state.json`
  `notes` field either.
- Select one incomplete feature.
- Implement the smallest coherent slice.
- Run the full gate before marking it complete.
- Append one concise line to `.harness/PROGRESS.md`.
