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
2026-09-01 | M4.5-data-commands | done | data.writeText（text/lines 二选一，workspace 包含校验，UTF-8）与 data.limit（前 N 条截断，pure/safe）入册，catalog 25 条；百度示例升级为纯文本产物（limit 10 + 按行输出）；65 tests + full gate passed
2026-09-01 | M5-api-decision | done | ADR 0006：进程内 Python API 冻结为 v1（不建 facade）、HTTP 推迟并附重估条件、排除清单结论、三个待定问题全部结案；公开签名契约测试 4 项；工作区分批提交完成（M3+M4 / M2.2 / baidu / harness / M4.5）；69 tests + full gate passed
2026-09-01 | M6-api-usage | done | docs/api-usage.md（四步模式、状态机消费指引、恢复/暂停语义）+ examples/api-usage 可运行示例（run → 读证据 → pause → resume 实测）+ README 链接；70 tests + full gate passed
2026-09-01 | M7-command-surface | done | win32 timeoutMs 对称（findElement 声明未实现一并修复）、data.format（{name} 占位符 fail-fast）入册 catalog 26 条、UIA COM 繁忙轮询容错；S0 实验：Chrome 152 封锁默认 profile CDP 端口 → 捕获传输定为扩展为主/持久 profile 为辅（决策并入 ADR 0007）；72 tests × 3 轮 + full gate passed
2026-09-01 | capture-transport-scheme | added | 调研 Panerelay（chrome.debugger+Native Messaging→CDP 兼容端点）与 Playwright MCP（持久 profile 默认方案印证）；实测 chrome://inspect 授权开关（9222 监听、/json/* 404、需显式 WebSocket URL）；传输方案定稿 docs/capture-transport.md（持久 profile 主 + chrome://inspect/Panerelay/MCP 扩展/自研扩展降级链），S1 验证协议与 M8 计划更新
2026-09-01 | M8-devserver | active | ADR 0007 定稿（devserver 隔离边界/捕获契约/两个待定问题结案）、rpa_core.devserver 骨架（catalog/compile/workflows CRUD + 捕获 501 占位，stdlib 零新依赖）、CLI devserver 子命令、架构检查新增 devserver 隔离断言、11 项合同测试；curl 全流程实测通过；full gate 83 tests 全绿；仅余 S1 验证（需登录态 Chrome 人工协作，不阻塞四条验收标准）
