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

## Commands

```text
Sync:        uv sync --all-groups --extra gui
Test:        uv run pytest
Lint:        uv run ruff check .
Architecture:uv run python .harness/scripts/check_architecture.py
Full gate:   uv run python .harness/scripts/check_all.py
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

## Documentation language

- 面向项目维护者阅读的任务、计划、验收、进度说明和 ADR 正文使用中文。
- 代码标识、命令、错误码、JSON 字段、状态机取值和外部协议名保留英文。
- 机器读取的 JSON key 不翻译，避免破坏工具契约。

## Workflow

- Read `.harness/PROGRESS.md` and `.harness/feature_list.json` at session start.
- Select one incomplete feature.
- Implement the smallest coherent slice.
- Run the full gate before marking it complete.
- Append one concise line to `.harness/PROGRESS.md`.
