# M71：捕获 Esc 取消 + kit 边界重划（只管捕获/编辑）

Status: `done`

> **2026-11-13 立项**。维护者三句反馈：
> ①「无法按 esc 取消捕获」②「删除元素的时候，提示删不掉的框，也无法退出」
> ③「kit 只管理捕获和编辑，其他逻辑放在网页端，删之前检查哪几个节点里引用了这个
> 逻辑放在网页端就行」。
> ③ 是**产品口径**（已拍板），不是待选方案：删除与引用检查归网页端；kit 保留
> 捕获自己的提示浮窗，不再被业务提示借用。

## 一、取证：为什么按 Esc 没反应

捕获期三条「键盘出口」全都靠不住，只有同时成立时才取消得掉：

| 通道 | 现状 | 缺口 |
| --- | --- | --- |
| 扩展 `content.js` `onKey` | `armed` 时发 `rpa-capture-cancelled` | 必须页面有焦点；捕获时主窗已最小化 |
| 桌面腿 `desktop_agent._hover_capture` | 50ms 轮询 `GetAsyncKeyState(VK_ESCAPE)` | agent **只在 hover 时 spawn**，等用户去网页点元素时根本没起 |
| 捕获浮窗 `CaptureFloat` | 取消按钮（确定性出口） | `WS_EX_NOACTIVATE` + `SW_SHOWNOACTIVATE`，**永不抢焦点，收不到 WM_KEYDOWN**；没写 Esc 分支 |

`M43-capture-esc-cancel.md:159-163` 当年就把「浮窗自己的 Esc / 宿主级快捷键」
登记为残留未做；`engine.gesture_hint` 却一直写着「Esc 取消」，文案与实现不符。

## 二、取证：「删不掉」框为什么退不出去

不是 `MessageBoxW`（全仓无匹配），也不是网页 alert。是
`api.delete_element` → `_notify("删不掉", …)` → `CaptureFloat.show_notice`：

- 浮窗 style `WS_POPUP | WS_BORDER`，**没有 WS_CAPTION / WS_SYSMENU**，没有系统 ×；
- `WS_EX_NOACTIVATE` 不抢焦点，点击命中脆弱；
- `on_close is None` 时连自绘「关闭」按钮都 `SW_HIDE`（`_sync_button`）；
- 业务提示借捕获浮窗说话，等于把「错误路径」押在一个为捕获设计的置顶窗上。

前端 `deleteAsset` 失败时 `catch { return }` / `if (!res.ok) return` —— 面板零提示，
用户除了那个置顶框没有别的出口，也没有别的解释。

## 三、落地

**kit（`capture_kit/float_window.py`）**：新增模块级 `_escape_down()`
（`GetAsyncKeyState(VK_ESCAPE) & 0x8000`，全局异步键状态，不挑焦点），由已有的
40ms 躲鼠标定时器驱动 `_poll_escape()`。只在 `_MODE_CAPTURE` 生效、按下沿去重、
回调丢独立线程（与「取消捕获」按钮同待遇）。编辑态浮窗本就 hide，提示形态交给
自己的关闭按钮/自动收起，都不被 Esc 顺手关掉。回调仍是 `controller.cancel`
（`window.show(..., on_cancel=self.cancel)`），没有新增第二条取消语义。

**网页端（`main.js`）**：
- 新增 `capturePhase`（`pending` / `editing`）。捕获等待期 Esc → `rpa.cancelCapture`
  + `restoreApp`，与桌面轮询幂等；**editing 态不抢** —— 桌面编辑框的 Esc 由
  `IsDialogMessage` 映到 IDCANCEL → `discarded`，抢了会丢掉 committed/discarded。
  终态统一走 `endCapture()` 收 session/phase/timer，避免 7 处散着漏一个。
- `deleteAsset` 删前先 `rpa.elementDetail` 拿 `referencedBy`；有引用 →
  `showDeleteProblem` 复用 `#elemDialog` 的只读告知形态，逐条列出「第 N 步」，
  **不发删除 RPC**；「取消 / 关闭 / Esc」都能退出。无引用才发删除；服务端仍返回
  `ELEMENT_IN_USE` 时（并发改动）同样只在网页端说明。

**api（`api.py`）**：`delete_element` 去掉 `_notify`，docstring 写明边界。
`_element_references` 的**服务端护栏保留**：防的是绕过界面直调 RPC 把被引用的资产
删掉、运行期炸 `ELEMENT_REF_MISSING`——「逻辑放网页端」指检查与反馈的呈现，
不等于拆掉最后一道安全校验。`_notify` 收窄到只服务捕获链路（环境没就绪/离线/失败）。

## 四、判据（都做了负向验证）

| 判据 | 结果 |
| --- | --- |
| `tests/unit/test_capture_kit.py` | 22 测（+4：按下沿去重、形态守卫、定时器→`_wnd_proc`→轮询真链路、on_cancel 真收掉会话） |
| `.harness/demo/ppx_probe/_float_probe.py` | 真机 PASS：真窗 + 真消息泵 + 真 40ms 定时器触发取消；只把「按键物理状态」换替身，**不往系统注入按键** |
| `.harness/scripts/check_element_pick_contract.py` | 18/18（删除 case 改判「回 referencedBy 且不弹浮窗」） |
| `scripts/check_element_library.mjs` | 10/10（+删前检查不发删除/列出节点/Esc 可退，+捕获 Esc/editing 不抢）；DOM shim 补 `keydown` 真实派发 |
| `scripts/check_element_pick_ui.mjs` | 22/22（+kit 边界、+Esc 接线；Esc 判据用**行级断言**，否则函数定义自己就把 `/cancelCaptureByEsc/` 喂成恒绿） |
| `.harness/spike/negative_element_library.py` | 17/17（16 注入 + 元验证）；扩展出第 4 条 gate = kit 单测，四条 gate 全跑全等断言 |
| 完整门禁 | `FULL GATE PASSED`（改了 main.js ⇒ 需 vite build，否则 `test_dist_bundle_is_rebuilt_from_source` 红） |

★ 负向首轮 N11 期望集漏写 `UI_KIT_BOUNDARY`：注入把 `if (refs.length)` 换成
`if (false)`，静态判据里「查到引用要拦下删除」的源码约束也该红。按推理补全，
不是照抄实际红灯。

## 五、欠账 / 边界

- 真机**全局按键注入**未跑（`keybd_event` 会把 Esc 打到用户当前前台窗口）。
  已验证的是「真窗口→真定时器→回调」+ 本机 `GetAsyncKeyState` 可读不抛异常；
  物理按键到异步键状态这一环由 Win32 语义保证，需要时再占机补一次 E2E。
- 扩展那条 Esc 腿仍只在页面有焦点时有效；桌面轮询现在是兜底，不是替换。
- `M68-capture-single-button.md` §三 的「删不掉由浮窗说」已被本条推翻，
  该处加了修订注，历史条目本身不改。
