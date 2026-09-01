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
2026-08-31 | M2.1-legacy-element-importer | done | 旧元素静态盘点文档、LegacyElementImporter、provenance/diagnostic 模型与确定性导入测试已补齐；导入器仅处理静态数据，不触及 rpa_script runtime
2026-09-01 | M3-checkpoint-recovery | done | ADR 0004 状态机契约、版本化原子 checkpoint + 路径键完成集合、resume(plan, run_id) 恢复门槛、indeterminate/recovery_required 终态与人工恢复入口（含 CLI resume）、7 项崩溃注入测试；48 tests + full gate passed
2026-09-01 | M4-pause-resume | done | ADR 0005 暂停契约（action 入口 + attempt 前生效、取消>暂停>超时）、RunStatus.PAUSED 驻留状态、pauseRequested/runPaused 事件、resume 复用 M3 五道门、规则 8 修订；7 项边界测试；55 tests + full gate passed
2026-09-01 | M2.2-uia-winforms | done | WinForms 测试应用全链路 UIA E2E（输入/提交/读回 + 对话框双 session，3 连跑一致）、timeoutMs 轮询与 EnumWindows 兜底修复 owned 弹窗枚举盲区、manifest 对称合同测试、docs/desktop_backends.md；60 tests + full gate passed
2026-09-01 | baidu-news-example | added | 真实站点示例 examples/baidu-news-top10（百度搜索"新闻"保存首页结果标题到 run_artifacts/baidu-top10.txt）；browser.launch 扩展 userAgent 参数并移除 automation 标志（headless 被百度安全验证拦截，headed + 正常 UA 放行）；python worker 修复相对 path 按 workspace 解析（补越界拒绝测试）；61 tests + full gate passed
