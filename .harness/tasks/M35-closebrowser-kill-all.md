# M35 closeBrowser 语义收敛：移除 scope，直接杀指定浏览器的全部进程

状态：`done`

关联：M32（browser.closeBrowser 初版，scope 两档）、`commands/browser/closeBrowser.json`、
`executors/browser.py`、维护者定案 2026-09-21

## 0. 决定（维护者定案）

维护者原话：「没有 scope 的问题，是直接杀某个浏览器的所有进程」。

M32 把 `browser.closeBrowser` 做成影刀形态的 `scope` 两档：
`launchedByUs`（默认，只杀本执行器拉起过的实例）/ `byProcessName`（按名全杀）。
评估结论：**两档都是过度设计**——

1. 授权已经由「执行这条命令」这个动作本身给出。要不要连用户自己开的窗口一起关，
   是写流程的人在画布上做的一次显式选择，不需要运行时再用第二个参数问一遍。
2. 「我们拉起过」的判据依赖**本进程内存里的启动时间水位**（`_launched_marks`）。
   流程跨进程执行（GUI 重启、resume 换进程）后水位必然丢失，保守默认随即把
   「明明该关」的浏览器报成 `no_launched_process`——保护没兑现几次，误报先来了。
3. 真正想「只结束本流程的会话、不碰浏览器进程」的场景，正确工具是
   `browser.close`（会话解绑），本来就不在 closeBrowser 的职责里。

## 1. 变更面

- `commands/browser/closeBrowser.json`：删除 `scope` 参数；`browserType` 说明
  写明「终止该类型的全部进程，包含用户自己打开的窗口」。
- `executors/browser.py`：
  - 删 `_launched_marks` 水位（`__init__` 与 `_launch_target` 的记录点）；
  - 删 `_scope_processes`；
  - `_close_browser` 重写为「枚举 → 逐个终止 → 记账 → 等退出」的直线；
  - `_list_browser_processes` 删 `startedAt`（唯一消费者是水位筛选；Windows 的
    tasklist 本来就拿不到启动时间、恒记 0，POSIX 的 etimes 换算随之删除，
    `ps -eo pid=,etimes=,comm=` 简化为 `pid=,comm=`）。
- `tests/contract/test_browser_close_commands.py`：删 2 个 scope 专测
  （默认保守 / 水位筛选），其余去掉 `scope=` 参数；保留全部记账/等待/
  枚举外 browserType 拒绝/离线可用断言。
- feature `browser-teardown` 标题同步（scope 措辞 → 全杀语义）。

## 2. 保留不变（M32 的有效部分）

- `force` 决定终止方式；`timeoutMs` 只约束「等真正退出」的窗口；
- 逐进程记账 `killedProcessIds`/`failedProcessIds`，`terminated = not failed`；
- 无匹配进程 → 成功 + matchedCount=0（幂等收尾）；
- 扩展通道离线时仍可用（进程级命令不走扩展通道）；
- Windows `tasklist` GBK 编码修复与 `OpenProcess` 判活；
- `_terminate_process` 的 `taskkill /T` 整树终止（浏览器进程是树）。

## 3. 验证

- [x] 契约测试全绿（`test_browser_close_commands.py` 18 项）
- [x] FULL GATE PASSED（check_all.py 退出码 0）。计数口径：收集实测 1083 项
      （`pytest --collect-only`），与 M34 已提交门禁的 1086 项相差恰为删除的
      3 项（2 个 scope 专测 + 1 个参数化用例），故 1069 passed / 2 xfailed /
      12 skipped。注：本会话沙箱的批量删除保护拦截了 pytest 退出时的临时目录
      清理，完整套件的汇总行被吞，退出码与收集数是两次独立实测交叉核对的结果。

## 4. 未覆盖 / 备注

- `errors` 里的 `EXECUTOR_FAILED` 声明保留：删掉 `no_launched_process` 后该码
  在本命令已无返回点，但**防御性声明合法**（声明比实现多是设计允许的，
  `check_error_contract` 单向门禁不反向卡）。
- 真机未对真实浏览器执行终止动作（与 M32 同一约束）；打桩测试不冒充真机结论。
- `closeTabs` 的 `all=true` 不区分「我们创建的」与「用户自己的」标签页——
  scope 删除后这两条命令的杀伤口径**天然一致**（都是全杀），M32 登记的
  「同构水位（tabs.create 记 tabId）」缺口随之降级为「暂无需求」。
