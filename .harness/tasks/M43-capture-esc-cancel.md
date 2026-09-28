# M43 捕获中按 Esc：只收红框，不收会话

状态：`done`

由来：维护者 2026-09-28 报障（M42 收口后）——

> 捕获元素时，在桌面按 esc，只不显示红框，但还是在捕获模式中

这是**会话状态机**缺陷，不是界面缺陷：用户已经表达了「结束」意图，会话却没结束。
沿用本仓纪律：先定性机制（哪条腿收、哪条腿没收、会话为什么不结束）、再改实现、
判据落到**能区分「取消」与「失败」**的行为上，最后逐条负向验证 + 全门禁。

## 0. 一句话结论

1. **`cancelled` 是会话级信号，却被当成单腿失败处理。** 桌面 hover 里按 Esc，
   `desktop_agent._hover_capture` 走 `if escape_now: return {"cancelled": True}`（全仓
   **唯一**的桌面 `cancelled` 产出点），agent 进程随即退出、`finally` 里
   `overlay.destroy()` 把红框收掉。但在 `HybridCaptureSession.pick` 里，这个结果
   **不带 `kind`** ⇒ 被记为 `desktop_failure`（与「agent 崩溃」「非法输出」同一档）。
2. **会话要等两条腿都出局才收场**（`_exhausted`），而扩展腿（端点在线）一直活着
   ⇒ 会话继续等。叠加 **M41 S5 的 `CAPTURE_TIMEOUT_SECONDS = float("inf")`**
   （宿主把 `timeout_seconds=inf` 一路传到 `pick`）⇒ **永不结束**。
   用户侧可见的只有红框消失：主窗仍最小化、扩展仍 arm、`_capture_session` 仍非 None、
   再点「捕获元素」仍报「已有捕获任务进行中」。
3. **对称面同样中招**：网页里按 Esc（`content.js` onKey → `rpa-capture-cancelled` →
   `background.js` 的 `sendCapture({cancelled: true})`）也会被记成 `extension_failure`，
   而此时桌面 agent 正在轮询全局按键、一直活着 ⇒ 同样永不收场。
4. 旧代码里那句「用户按 Esc 取消（扩展腿回 cancelled）优先呈现为取消」**只写在
   `_exhausted_result` 里**——那是「两条腿都出局之后」的兜底映射，**永远轮不到**：
   取消的那条腿出局了，另一条还活着，`_exhausted()` 恒 False。这是一条**够不着的
   取消通道**（本轮已删，见 §2）。

## 1. 证据

### 1.1 现象在代码里闭环（无死角）

| 环节 | 事实 | 出处 |
| --- | --- | --- |
| 红框为什么消失 | agent 退出 → `finally: overlay.destroy()` | `desktop_agent._hover_capture` |
| 退出前给宿主什么 | `return {"cancelled": True}`（唯一产出点） | 同上 L613-614 |
| 宿主怎么解读它 | 不带 `kind` ⇒ `desktop_failure = result` | `hybrid.pick` ② 分支 |
| 会话为什么不停 | `_exhausted()` 要求两条腿都出局；扩展腿在线 | `hybrid._exhausted` |
| 为什么不是「等一会」而是「永久」 | 宿主传 `timeout_seconds=inf` ⇒ `deadline = inf` | `app.py` L371 + L2815 |
| 网页 Esc 同理 | `extension_failure = {cancelled}`，桌面腿还活着 | `hybrid.pick` ① 分支 |
| 旧取消通道够不着 | `_exhausted_result` 只在 ③ 之后调用 | `hybrid.pick` ③ |

### 1.2 四条收场出口的重新清点（写结论前先数出口）

会话能结束的路径共四条：① 拿到有效描述符（任一腿）；② 两条腿都出局（`_exhausted`）；
③ 用户取消（**本轮之前只覆盖宿主侧的 `session.cancel()`**：浮窗按钮 / 再点捕获）；
④ 超时。M41 S5 把 ④ 的预算改成 `inf` 之后，**「腿内 Esc」这条自我取消路径 ③ 就断了**：
它既不是描述符，也不让两条腿都出局，又不依赖 deadline。S5 的判据
（`test_capture_never_gives_up_on_its_own`）只钉了「会话自己不放弃」，**没钉「用户放弃时
会话要跟着收场」**——两条判据不是同一件事，本轮的缺口正落在这里。

### 1.3 本轮**没有**做的验证（如实记）

- **未做真机复现**：桌面 Esc 需要真实 hover 会话 + 真实按键注入，且桌面 E2E 抢前台
  （本仓默认不进 `check_all`，见 `--with-desktop-e2e`）。本轮证据 = **机制闭环**（1.1）
  + **契约判据**（§3）+ **逐条负向验证**（§4，含「挂住」形态）。
- 未改 `desktop_agent` 的 Esc 检测本身（它工作正常，问题在宿主侧的解读）。

## 2. 改动

### S1 `src/rpa_core/capture/hybrid.py`：取消升级为会话级

- 新增 `_is_cancelled(payload)`：只认 `cancelled is True`——与 `_is_capture_result`
  并列的第二条判别函数，**明确区分「用户取消」与「通道失败」**（两者都不带 `kind`，
  旧代码用同一个否定分支处理，语义就此糊掉）。
- `pick` 的两条腿分支各加一步：报 `cancelled` ⇒ 立即 `_finish_cancelled(thread)`。
- 新增 `_finish_cancelled(thread)`：`_pending = False` + `_extension.close()`（撤防扩展，
  让网页里的红框随 `capture_disarm` 下线）+ `_cancel_desktop(thread)`（回收还在轮询
  全局按键的 agent 子进程），返回 `{"cancelled": True}`。与「桌面先赢」分支同款收场，
  顺序一致。
- **删掉 `_exhausted_result` 里那段够不着的取消分支**（原「用户按 Esc 取消…优先呈现为
  取消」）：取消已在两条腿分支被拦下，走不到这里。留着就是一段永不执行的守卫，后来人
  会为它写一条永远打不中的用例；docstring 写清「回归由 `test_hybrid_esc_*` 钉住」。
- 模块 docstring 补第 4 段，把这条从「腿失败」清单里摘出来（失败形态只剩
  `unavailable` / `error` / `timeout` / 崩溃）。

**没改宿主侧**：`app.py` 的 `cancelled` 分支（「已取消捕获」+ `showNormal()` +
`present_window`）本来就正确，只是以前收不到这个结果；devserver 的
`/api/capture/desktop/pick` 与 Web 端 `showCompileMessage("已取消捕获")` 亦然。

### S2 `tests/contract/test_capture_hybrid.py`：三条判据 + 夹具两处

- **夹具 `FakeDesktopSession.pick`** 的延迟改为**可中断**（`cancel()` 能提前结束它）：
  真 agent 被 `cancel()` 时是**进程被终止**、`pick` 立即返回；假实现里睡死的 `sleep`
  会让 `_cancel_desktop` 的 `join(timeout=3)` 白等满 3 秒（用例变慢，且掩盖「收场时机」
  这个待测语义）。语义只是把上界变成可缩短的。
- **夹具 `FakeBridge`** 补 `disarmed` 计数与 `cancel_result` 开关（模拟
  `sendCapture({cancelled:true})` 的真实回包），`capture_disarm` 不再被忽略。
- 三条新用例：
  - `test_hybrid_desktop_esc_cancels_whole_session`：扩展腿在线且永不回结果（另一条腿
    活着才是常态），桌面腿回 `cancelled` ⇒ 立即返回 `{"cancelled": True}`、
    **实耗 < 1s**（旧实现在这里永不返回）、桌面腿被回收、扩展腿被撤防。
  - `test_hybrid_extension_esc_cancels_whole_session`：对称面（桌面腿 `pick_delay=5`
    还在跑，扩展腿回 `cancelled`）⇒ 实耗 < 2s、桌面 agent 被回收。
  - `test_hybrid_leg_failure_is_not_a_cancel`：**防过度泛化**的对照——扩展腿产出
    「无 `kind` 无 `cancelled`」的失败结果时，桌面腿仍必须胜出。

## 3. 判据设计要点

- 三条用例都传 `timeout_seconds=float("inf")`（**生产值**，照抄
  `CAPTURE_TIMEOUT_SECONDS`）：这正是「永不结束」的成因，用有限超时会把缺口测小
  （旧行为下会变成「等满 20 秒后报超时」，看起来像「只是慢」）。
- 断言分成三层，各自独立可红：**会话级结果**（`== {"cancelled": True}`）·
  **收场时机**（`elapsed < 1`）· **两条腿都被回收**（`cancelled` / `disarmed`）。
  三者由三处不同的实现行承载，负向验证逐个注入（§4）确认互不顶替。
- `disarmed` 用**轮询等待**（≤3s，与既有 `bridge.armed` 同款）：`disarm` 是「发出去」
  即返回，假端点要经自己的读线程才记数——直接断言是竞态，会给出偶发红。

## 4. 负向验证

探针 `.harness/spike/probe_m43_negative.py`，注入 `hybrid.py`，报
`_m43_negative_report.txt`。每处都要求：**对照绿 → 红名单恰好等于预期 →
失败类型必须是「N failed」（出现 error = 收集/夹具坏掉，是假红）→ 逐字节还原核 md5**。
**5/5 命中**（`NEGATIVE VERIFICATION PASSED`，收尾 md5 与 clean 一致）：

| key | 注入 | 实际红名单 | 结论 |
| --- | --- | --- | --- |
| x1 | 桌面腿的取消退回「腿失败」 | **挂住**（30s 被终止） | 旧行为的现场就是永不返回 |
| x2 | 扩展腿的取消退回「腿失败」 | 只有网页 Esc 那条 | 两条取消判据各自精确 |
| x3 | 过度泛化（任何非描述符结果都当取消） | **只有**防泛化那条 | 与「腿失败≠取消」互不顶替 |
| x4 | `_finish_cancelled` 不撤防扩展 | 只有「扩展仍 arm」那条 | 撤防这一行承重 |
| x5 | `_finish_cancelled` 不回收桌面 agent | 两条取消用例 | 回收这一行承重 |

### 4.1 我的判据**曾经是假绿灯**（本轮最有价值的记录）

第一轮跑探针时 **x4 与 x5 都报「没红」**（`3 passed`），而对照是绿的。查下去发现原因
不在实现、在**用例自己**：两条取消用例把 `session.close()` 放在 `finally` 里、**断言写在
`finally` 之后**，而 `close()` → `cancel()` 自己就会撤防扩展、回收桌面腿——于是
「收场时该做而没做」的缺口被**收尾兜底**补上了，断言看到的是兜底后的状态：

```
try:
    result = session.pick(...)      # 被测行为
finally:
    session.close()                 # ← 这里替被测行为把两件事都做了
assert fake.disarmed >= 1           # 恒真：撤防是 close() 干的
assert session._desktop.cancelled   # 恒真：回收是 close() 干的
```

修法：把**全部断言搬进 `try`（`close()` 之前）**，并在两条用例的 docstring 里写明
「先关再断言等于用收尾兜底掩盖缺口」。改完 x4 → 精确红在「扩展腿必须被撤防」、
x5 → 两条取消用例都红在「必须被回收」。

这条正好是本 skill 记的典型形态之一（「收尾兜底掩盖」），只是这次**不是**被测代码的
兜底、而是**判据自己的**兜底。教训：**断言与清理的先后顺序本身是判据的一部分**——
判据里任何「收尾还会再做一次」的动作，都必须排在断言之后。

另：x1 的形态也值得单独记——摘掉修复后判据不是变红，而是**永不返回**（被探针的
`timeout=30` 终止）。按仓规「挂住 ≠ 红」，探针把它**单独报出**并说明它就是维护者
报障的现场（`timeout=inf` 下会话真的永不结束），而不是含糊算作命中；这也顺带证明
这条判据测的是「收场」而不是「返回值」。

## 5. 残留（登记不修）

- **非 Windows 上桌面 Esc 无人接**：macOS 没有桌面腿（`available=False`），桌面上的
  Esc 既到不了扩展（不在页面里）也到不了宿主（主窗已最小化、无全局键盘钩子）⇒
  仍停在捕获态，出路是浮窗的取消按钮。与「桌面捕获仅支持 Windows」同因，登记待办。
- **`CaptureFloatWindow` 自己的 Esc**：本轮没给它加 Esc 出口（它的取消按钮已可用）。
  若维护者要「任何地方按 Esc 都能取消」，那是**宿主级快捷键**的立项，不是本条的补丁。
- 桌面腿的 `--timeout`（agent 自身预算，默认 60s）与宿主 `inf` 并存：agent 到点会自己
  退出并报 `timeout`，此时若扩展腿仍在线，会话继续等——这是 M41 S5 的既有设计（会话只
  在腿都出局时收场），本轮未动。

## 6. 验收

- `pytest tests/contract/test_capture_hybrid.py`：**15 passed**（新增 3 条）。
- 负向验证：**5/5 命中**（含 1 处「挂住」形态，已单独说明），逐字节还原核 md5
  （`8f460ba5da7e49a9d2f4d9509e731813` 与 clean 一致）。
- **`FULL GATE PASSED` / exit 0 / 177.60s**（报告 `_m43_gate.txt`）：pytest
  **1209 passed / 21 skipped / 2 xfailed in 177.60s**（`1209 = 1206 + 3`；那两条会开合的
  既有 QProcess 用例本轮也过了）+ ruff `All checks passed!` + 架构 66 python files /
  86 manifests + 任务 **63 features / 0 active task** + 参数消费与错误契约各
  `77 checked / 3 exempt / 0 skipped` + 命令矩阵 86 条/死参数 0/实现缺口 0/l2 109 +
  **11 个 `.mjs` 全 PASS**。
