# Architecture

## Dependency direction

```text
model
  ^
  |-- catalog
  |-- executors
  |-- compiler <- catalog
  |-- runtime  <- compiler + catalog + executors
  `-- cli      <- runtime + compiler + catalog + executors

workers are subprocess entry points and may depend on model only.
```

Allowed package dependencies:

| Package | May import |
|---|---|
| `model` | standard library, Pydantic |
| `catalog` | `model` |
| `compiler` | `model`, `catalog` |
| `executors` | `model` |
| `runtime` | `model`, `catalog`, `compiler`, `executors` |
| `workers` | `model` |
| `cli` | all public packages |

## Runtime flow

```text
workflow.json
  -> Workflow model
  -> Compiler validates commands, ids, references, capabilities
  -> ExecutionPlan pins a catalog version
  -> Orchestrator creates RunContext
  -> Executor receives CommandInvocation
  -> Executor returns CommandResult
  -> Orchestrator writes events and step outputs
```

## Ownership

- Model owns protocol types only.
- Catalog owns manifest loading and immutable snapshots.
- Compiler owns static validation and execution-plan creation.
- Runtime owns orchestration, scopes, cancellation, timeout, and event persistence.
- Executors own external-system interaction but not workflow state.
- Workers isolate high-risk or user-provided execution from the orchestrator.

New top-level packages or reversed dependencies require an ADR.
