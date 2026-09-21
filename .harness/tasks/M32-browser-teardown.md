# M32 浏览器收尾：关标签页与终止浏览器进程

状态：`done`

S1 已完成（2026-09-21）。

关联：M29（`browser.close` 的 `forceKill`/`ignoreUnload` 被删——单通道下没有对
应物，缺口在此立案）、ADR 0013（扩展单通道）、`docs/extension-channel-baseline.md`

## 为什么有这一里程碑

`browser.close` 在 M29 S3 收口时**删掉**了 `forceKill` / `ignoreUnload` 两个参数，
理由写得很明确：「我们不拥有用户的浏览器进程（也没有它的句柄），`chrome.tabs.remove`
本身也不弹 beforeunload 对话框，两个参数在单通道下没有对应物」。同时留了一句
「缺口（关标签页/终止进程）见 BACKLOG，属独立命令」。

本条里程碑就是兑现那句话。两个缺口各自的性质：

1. **关标签页**——用户要的是「跑完把我开的这几个页面关掉」。扩展侧本来就有
   `tabs.close`（单标签），但缺**批量**与**范围选择**；逐个调用会让 N 个标签花 N 次
   通道往返（每一步的往返数是 M28 S4 立的基线，不能无谓抬高）。
2. **终止浏览器进程**——这条要跨出扩展的能力边界。扩展能看见标签页与窗口，但
   `chrome.tabs.remove` 关掉最后一个标签时窗口是否随之关闭由浏览器决定；要**确实
   把浏览器进程结束掉**（例如它卡死了、扩展已经掉线），只能走操作系统的进程接口。

## 关键决策

### D1：关标签页与终止进程是独立命令，不是 `browser.close` 的参数

`browser.close` 是**会话生命周期**命令（`effect.kind = "session"`，语义是「解绑」，
不碰用户的浏览器）；关标签页与终止进程是**对用户浏览器的破坏性操作**
（`effect.kind = "unsafe-write"`）。风险等级、声明面、默认策略三者全都不同——
混进一个命令会让「结束会话」这种无害操作带上关页面的杀伤力。

### D2：`scope` 参数而非两个命令（对齐影刀的形态）

终止浏览器的判据有两种，维护者明确要求「两个都做，类似影刀给一个参数选项」：

| `scope` | 判据 | 杀伤范围 |
| --- | --- | --- |
| `launchedByUs`（**默认**） | 本执行器确实拉起过的实例（`_launched_marks` 启动时间水位） | 只关我们开的 |
| `byProcessName` | 按进程名匹配 | **包含用户自己打开的窗口** |

**默认必须保守**：判据「哪些浏览器是我们拉起的」在没有记录时无法从外部推断
（同一 exe、同一用户目录，与用户自开实例不可区分）。默认全杀会让一次普通流程收尾
把用户手上正在填的表单一起关掉——破坏性行为必须是**显式选择**的结果。

**保守默认 + 无记录时不静默成功**：这是在实现中调过一次的地方。最初的写法是
「无匹配 → 成功 + matchedCount=0」，但那条路径同时覆盖了「本来就没有这个浏览器」
（该成功）与「有浏览器但都不是我们开的」（该报错并指向出口）两种情况。现在分开：
后者报 `EXECUTOR_FAILED` + `reason=no_launched_process` + 提示改设 `byProcessName`。

### D3：`_launched_marks` 是「我们拉起过」的唯一来源

`launch_browser` 此前是 fire-and-forget（`Popen` 完就丢句柄），**没有任何「我们启动过它」
的记录**——这正是这条命令此前无法实现、只能退化成按名全杀的原因。现在在
`_launch_target` 里记水位。

记录点放在**等到插件上线之后**而不是 `Popen` 之后：只有真等到上线才算「拉起成功」；
失败的拉起也记水位，会让一次失败在事后被当成「我们开的」而去杀用户的浏览器。

### D4：批量关闭必须**逐项记账**，不能折叠成一个布尔

`tabs.closeMany` 返回 `closedTabIds` / `failedTabIds` 两个数组。一个天真的实现
（`await Promise.all(...)` 然后无条件返回全部 tabId）能通过所有 Python 侧测试——
因为 Python 桩测的是**接口形状**，不是扩展里的记账逻辑。它会在真机上把
「3 个标签关了 2 个」报成「3 个全关了」。

这条语义只能由扩展侧证明，因此新增 `scripts/check_close_ops.mjs`（切出
`executeCommand` 求值 + `chrome` 替身 + 反漂移正则）。

### D5：`all` 必须严格 `=== true`

`{tabIds: []}` 与 `{all: "yes"}` 都不能退化成「关掉当前窗口全部标签」。Python 侧用
`oneOf` + 互斥校验挡住了，但扩展是**独立进程边界**，可能被别的客户端（直接调通道、
手工构造信封）喂到这类输入，因此扩展侧也必须自洽。

## 交付

- `extension/background.js`：`tabs.closeMany`（显式 tabIds / all + 逐项记账）、
  `tabs.listWindows`（窗口清单，只报 id/状态/标签数，不泄露页面内容）。
- `extension/manifest.json`：补 `windows` 权限（`chrome.windows.getAll/getCurrent` 在
  Chrome 88+ 虽可从 tabs 权限使用，但显式声明才是可依赖的契约）；版本 `0.3.3` → `0.4.0`。
- `src/rpa_core/executors/browser_ext.py`：`tabs_close_many` / `tabs_list_windows` 封装。
- `src/rpa_core/executors/browser.py`：
  - 模块级 `_BROWSER_PROCESS_NAMES` / `_CONSOLE_ENCODING` / `_list_browser_processes` /
    `_terminate_process` / `_wait_processes_exit` / `_process_alive`（跨平台，零新增依赖）；
  - `_launched_marks` + `_launch_target` 记水位 + `_scope_processes` 过滤；
  - `_execute_extension` 的 `browser.closeTabs` 分支、`_close_browser` 方法；
  - `browser.closeBrowser` 加进 `_NO_SESSION_COMMANDS`（进程级操作，扩展掉线时最需要它）。
- `commands/browser/closeTabs.json` / `closeBrowser.json`（`x-palette-order` 5/6，
  close 之后、其余编号 +2 顺移）。
- 测试：`tests/contract/test_browser_close_commands.py`（21 项）、
  `test_browser_channel_metrics.py` 补 closeTabs 基线、`scripts/check_close_ops.mjs`（28 项断言）。
- i18n（`src/rpa_core/devserver/static/i18n.js`）两条标签。

## 实现中发现并修掉的真 bug

1. **`tasklist` 输出编码**：中文 Windows 上 `tasklist` 输出 GBK，`subprocess` 默认按
   UTF-8 解会抛 `UnicodeDecodeError`——且异常发生在 subprocess 的**读线程**里，
   表现为 `stdout` 为 `None`、报错在很远的地方（`'NoneType' has no attribute 'splitlines'`）。
   现在显式给 `encoding=_CONSOLE_ENCODING`（Windows = `mbcs`）+ `errors="replace"`。
2. **`os.kill(pid, 0)` 在 Windows 上不可用**：Windows 的 `os.kill` 走
   `TerminateProcess`，信号 0 直接抛 `OSError [WinError 87] 参数错误`（「信号 0 =
   只探测」是 POSIX 语义）。Windows 侧改用 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`
   拿句柄判存活。

## 真机未覆盖面（不拿打桩结论冒充真机结论）

- 进程枚举与终止在本机（Windows）跑过真实 `tasklist`（确认能列出 24 个 msedge 进程），
  但**终止动作未对真实浏览器执行**（会关掉维护者手头的窗口）。`scope=launchedByUs`
  的端到端路径需在真机上按新启浏览器验证一次。
- POSIX 侧（macOS）的 `ps -eo pid=,etimes=,comm=` 解析未在真机跑过——维护者有 mac 环境，
  待验证时一并覆盖。
- `tabs.closeMany` 的「关掉窗口最后一个标签使窗口关闭」是浏览器语义，未在真机确认
  Chrome/Edge 的实际行为差异。

## 剩余缺口

- `browser.closeTabs` 目前作用于**扩展可见的全部标签页**，不区分「我们创建的」与
  「用户自己的」，因此 `all=true` 的杀伤范围与 `scope=byProcessName` 同级别。若要
  「只关我们开的标签页」，需要 `tabs.create` 时记录 tabId（与 `_launched_marks` 同构），
  属后续切片。
- `tabs.listWindows` 已提供窗口清单，但尚未有命令据此「只关一个窗口」——
  `closeTabs` 的 `all` 只作用于当前窗口，`closeBrowser` 只作用于进程。中间粒度（指定
  windowId 关窗）未开放给用户参数。
