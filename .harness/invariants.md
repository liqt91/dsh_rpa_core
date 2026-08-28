# Invariants

These rules must be mechanically enforced where practical.

1. No `eval()` or `exec()` in `src/`.
2. No imports from the sibling `rpa_script` repository.
3. `model` does not import other `rpa_core` packages.
4. Control-flow node names are not valid command manifest ids.
5. Command manifest ids are globally unique and match their file stem.
6. Every manifest declares version, executor, kind, risk, stability, input schema, output schema, errors, and implementation handler.
7. Executors return `CommandResult`; no executor receives an Orchestrator instance.
8. Browser commands other than `browser.launch` require a `sessionId` input.
9. Python command execution uses a subprocess worker.
10. A compiled plan records the command catalog digest.
11. Every terminal run writes `result.json`; every state change appends an event to `events.jsonl`.
12. A stable command requires catalog, contract, and runtime integration tests.
13. Variable references replace complete `${scope.path}` tokens only; embedded string templates and list indexes are unsupported until separately specified.
14. Reference lookup fails fast on missing or non-mapping path segments; it never silently returns `None`.
