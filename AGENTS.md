# rpa_core agent notes

## Product boundary

This repository is a clean-room RPA core experiment. Do not import runtime code from the sibling `rpa_script` project. Legacy files may only be copied into `tests/fixtures/` as inert test data.

## Commands

```text
Sync:        uv sync --all-groups
Test:        uv run pytest
Lint:        uv run ruff check .
Architecture:uv run python .harness/scripts/check_architecture.py
Full gate:   uv run python .harness/scripts/check_all.py
CLI:         uv run python -m rpa_core.cli
```

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
