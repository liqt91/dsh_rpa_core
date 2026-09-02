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
2026-09-02 | M11-editor-tree | done | 维护者确认切片 1 拖拽体验后完成切片 2-5：控制节点属性表单（if/forEach/try/return）、多选+批量移动/删除（同列表连续性校验、容器连带确认）、Ctrl+C/V 子树复制粘贴（全树 id 重映射）、快照式撤销/重做（50 步上限）、选中集以节点身份重寻；93 tests + full gate passed
2026-09-01 | M8-devserver | done | ADR 0007 定稿（devserver 隔离边界/捕获契约/两个待定问题结案）、rpa_core.devserver 骨架（catalog/compile/workflows CRUD + 捕获 501 占位，stdlib 零新依赖）、CLI devserver 子命令、架构检查新增 devserver 隔离断言、11 项合同测试；curl 全流程实测通过；full gate 83 tests 全绿；S1 验证移入 M10 计划单（M10 前置，需登录态 Chrome 人工协作）
2026-09-01 | M9-editor | done | ADR 0008 放行编辑器 UI（零构建硬边界）、devserver/static/index.html 单页（命令面板 + 线性画布 + schema 表单 + 编译回显 + 打开/保存闭环）、GET / 唯一静态路由；Playwright Chromium E2E 全流程 3 连跑稳定 + 3 项合同测试；E2E 首跑即捕获并修复编译成功分支漏设 .ok class 缺陷；87 tests + full gate 全绿
2026-09-01 | S1-verification | done | 用户日常 Edge 152 实测：DevToolsActivePort 文件读 WS URL（免 UI 交互）、/json/* 404 防扫描确认、connect_over_cdp 枚举真实标签页 + 新标签页操作（example.com picker 注入回验 + 百度搜索 URL 直达）+ 原生 CDP session；user-browser 子类型定案 chrome-inspect-ws 补录 ADR 0007 §4；遗留：开关持久性待重启确认、登录态断言顺延 M10b
2026-09-01 | capture-extension-research | added | 复核调研 chrome-relay（Native Messaging 免弹窗、Python host 可行）与 Playwright MCP --extension（核实默认每连接批准 + profile 唯一 token 绕过 + 标签组隔离）；修正"扩展=零弹窗"误判；降级链定稿：M10a 持久 profile → M10b chrome-inspect-ws（S1 通过）→ M10c 自研扩展（token 配对，立项不实装）；Panerelay/MCP 传输降记录备查，capture-transport.md §2.3-§2.5/§3 与 ADR 0007 §4 已更新
2026-09-01 | M9.1-editor-dnd | done | 编辑器拖拽：命令面板拖入画布任意位置插入（上/下半区落点指示）+ 画布节点拖拽排序，HTML5 DnD 零依赖；E2E 扩展拖入插入与重排断言 3 连跑稳定；87 tests + full gate 全绿
2026-09-01 | M11-editor-tree | active | 结构化树形画布立项：范式决策（树形非 DAG、快照撤销 50 步、静态拆分 allowlist）、拖拽先行验证片为显式确认门；实现由独立 agent 按 M11 任务单执行
2026-09-02 | M11-editor-tree | slice0+1 done | 切片 0（/static/{name} 硬编码 allowlist 路由 + 面板分组数据源）与切片 1（路径寻址树模型/全树唯一 id/禁止移入自身子树、递归缩进色带渲染、控制流分组、DnD v2 子树拖拽+空容器落点+48px 边缘自动滚动、schema default 构造）完成；静态文件拆分 index.html/app.js/styles.css；嵌套拖入保存读回 E2E ×3 稳定 + /static 合同测试 2 项；90 tests + full gate 全绿；**暂停等待维护者确认拖拽体验**
