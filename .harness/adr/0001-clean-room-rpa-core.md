# ADR 0001: Clean-room executor-oriented RPA core

- Status: accepted
- Date: 2026-08-27

## Context

The sibling `rpa_script` project proves browser and Windows automation behavior but combines workflow control flow, dynamic command registration, runtime routing, browser sessions, database compatibility, and code generation in one system. Incremental replacement would require multiple old and new execution models to coexist.

## Decision

Build a bounded architecture-validation project with:

- native workflow AST;
- command manifests as the single metadata source;
- static compilation to a catalog-pinned plan;
- an orchestrator that exclusively owns workflow state;
- explicit executors returning `CommandResult`;
- self-developed browser extension as the sole browser channel (formerly "Playwright first", superseded by ADR 0013);
- subprocess isolation for Python commands;
- append-only run events and terminal results;
- a lightweight mechanical harness.

The project does not import runtime code from `rpa_script`. It may use legacy data only as inert fixtures.

## Consequences

The first version has less feature coverage but can validate the core contracts independently. UI, API, database, DSH, MCP, scheduling, installation, and multi-platform desktop drivers remain out of scope until the vertical slice passes its decision gate.
