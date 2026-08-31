# Progress

Format: `YYYY-MM-DD | feature | status | evidence`

2026-08-28 | project-harness | done | architecture/invariant docs + full mechanical gate
2026-08-28 | protocol-models | done | typed workflow, command, result, error, event, and run models
2026-08-28 | catalog-compiler | done | immutable 9-command catalog with digest and static reference/capability checks
2026-08-28 | orchestrator | done | timeout/retry/cancel/event/terminal-result ownership verified
2026-08-28 | browser-playwright | done | explicit UUID browser session and real Chromium contract verified
2026-08-28 | python-worker | done | subprocess JSON worker with workspace containment and UTF-8 path regression
2026-08-28 | vertical-slice | done | deterministic search-and-save CLI and Chromium E2E passed
2026-08-28 | orchestrator-hardening | done | lexical catch scope, async run evidence I/O, bounded foreach, explicit result guards, workflow timeout; 11 tests + full gate passed
2026-08-28 | resolver-contract | done | whole-token references, fail-fast malformed references, mapping-only paths, condition tests, and nested compiler scope propagation; 15 tests + full gate passed
2026-08-28 | runtime-correctness | done | stable errors/context, timeout and subprocess cleanup, bounded retry/deadline, persistence failure semantics; 24 tests + full gate passed
2026-08-28 | effect-contract | done | typed effect/replay/idempotency policy, unsafe retry rejection, committed and unknown evidence, browser/worker E2E; 30 tests + full gate passed
2026-08-31 | M2-desktop | active | 新增 desktop.win32 独立后端与记事本 Win32 垂直切片，桌面 locator 扩展 backend/className/controlId/foundIndex/menuPath，Win32 记事本 E2E 与合同测试已通过；UIA 主线保留待后续 WinForms 验证
2026-08-31 | M2.1-legacy-element-importer | active | 旧元素静态盘点文档、LegacyElementImporter、provenance/diagnostic 模型与确定性导入测试已补齐；导入器仅处理静态数据，不触及 rpa_script runtime
