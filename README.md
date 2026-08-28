# rpa_core

A clean-room RPA runtime experiment focused on explicit workflow semantics, isolated executors, typed results, and reproducible runs.

## Scope

The first milestone proves one vertical slice:

```text
Workflow AST -> validation/compiler -> execution plan -> orchestrator
-> Playwright browser executor / isolated Python worker
-> typed results + events.jsonl + result.json
```

Included initially:

- Native workflow AST: sequence, action, if, forEach, try, return.
- Versioned command manifests loaded into an immutable catalog snapshot.
- Explicit executors for browser and Python commands.
- Strongly typed command results and stable error codes.
- Run cancellation, step timeouts, event logs, and terminal run results.
- A deterministic local test site and CLI-driven end-to-end example.

Explicitly excluded from the first milestone:

- FastAPI, database persistence, React UI, DSH plugin, MCP, scheduler, installer.
- Online command/source editing and runtime module hot replacement.
- Arbitrary Python execution in the orchestrator process.
- macOS/Linux desktop drivers before the Windows driver contract is proven.

## Development

```powershell
uv sync --all-groups
uv run python -m playwright install chromium
uv run python -m rpa_core.cli validate examples/search-and-save/workflow.json
uv run python -m rpa_core.cli run examples/search-and-save/workflow.json
uv run pytest
uv run ruff check .
uv run python .harness/scripts/check_architecture.py
```

Run the complete local gate:

```powershell
uv run python .harness/scripts/check_all.py
```

从 `.harness/tasks/BACKLOG.md` 和 `.harness/project_state.json` 指向的当前计划开始工作。面向维护者的任务与计划文档使用中文；代码标识、错误码和机器协议字段保留英文。修改核心协议前先阅读 `.harness/architecture.md`、`.harness/invariants.md` 和 `.harness/feature_list.json`。
