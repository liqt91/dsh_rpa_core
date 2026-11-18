# M87：无障碍唤醒要能**反复唤醒**（「能捕获几秒又只剩外框」）

Status: `done`

> **2026-11-17 真机报障**：M86 落地后，用户「有几秒钟可以捕获微信控件了，但是之后又
> 失效了，只能捕获外框」。
>
> 取证（`%TEMP%\rpa-capture-trace.log`）：18:06 那次 `accessibility_wake ok=true
> reason=already_active before=1`；18:20 那次 `reason=activated before=0` ——
> **gate 曾被写成功过，之后又被目标自己关回 0**。
>
> 影刀对照（`activity_weixin_v4/wechat.py`）：`_hot_activate_accessibility` 是**幂等**
> 的（读到 1 就只 return，不写），而 `ensure_window()` **每次业务操作前都会调用**——
> 它没有「每进程只试一次」这种一次性设计。我们的 M86 实现有，且还叠了两个别的问题。

## 一、根因（两个独立缺陷）

| # | 缺陷 | 现象 |
|---|---|---|
| 1 | `_WAKE_ATTEMPTED` 是**每 pid 只写一次** | 目标把 gate 关回去后再也不重试，树永久失效 |
| 2 | 唤醒写在 `_hover_hit` 的 `try` 里、`_element_from_point` **之后** | 树休眠时 `ElementFromPoint` 抛 `E_ACCESSDENIED` ⇒ 跳进 `except: pass` ⇒ 唤醒永远轮不到，「休眠 → 命中失败 → 不唤醒」死锁 |
| 3 | hover 的命中测试只在 `moved` 时跑 | 鼠标一停，树即便被救活也不会重画，红框卡在整窗外框上 |

## 二、改法

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | `_WAKE_ATTEMPTED` → `_WAKE_LAST` 节流字典（`_WAKE_INTERVAL=1.0s`）+ `_WAKE_HOPELESS`（`no_dll`/`no_gate` 才终局）+ `_WAKE_STATE`（状态翻转才记 trace） | 节流窗口内不重复开句柄、窗口过后必须再试 | done |
| S2 | `_maybe_wake_accessibility(force=)`：`capture_at` 用 `force=True`（按下捕获键那一刻必须真实） | force 不受节流限制 | done |
| S3 | 唤醒移到 `_hover_hit` **入口**、hit-test 之前（`probe_root = _win32_root_at`），后续复用该 root | `ElementFromPoint` 抛异常时唤醒仍执行 | done |
| S4 | `_hover_capture` 加**与鼠标移动无关**的保活块（每 `_WAKE_INTERVAL` 兜一次）；唤醒返回 True（gate 刚被从 0 写成 1）⇒ `force_hit` 立刻重做命中测试 | 鼠标静止时树被救活也会重画 | done |
| S5 | 唤醒函数返回 `bool`（仅 `reason=="activated"` 为 True） | `already_active` 返回 False | done |
| S6 | 负向探针 `probe_m87_negative.py`（八个注入）+ 真机周期探针 `probe_m87_gate_flap.py` | `ALL PASS` | done |
| S7 | 稳态不打扰目标：`activate(poke=False)`（只在真写过字节时 poke），避免每秒让对方重建 provider 树 | 稳态零 poke；写过必 poke | done |
| S8 | 按下捕获键先 `force` 确认；若刚救活则**丢弃缓存 leaf** 重新命中 | 救活走全量、未救活走快路径 | done |

## 三、验证结果

- `tests/contract/test_accessibility_wake_retry.py`：**13 条**
  - 节流：窗口内不重复、窗口后必再试、`force` 绕过节流
  - 终局：`no_gate` 之后不再试（避免每秒刷日志）
  - trace：`already_active` 只在翻转时记一条；`activated` 每次都记（带 `before=0`）
  - 时序：`ElementFromPoint` 抛异常时唤醒仍先执行
  - 重画：鼠标完全静止时，树被救活也会触发第二次 hit-test
  - poke：稳态零 poke、写过必 poke、agent 首次 True 之后 False
  - 捕获键：刚救活时**不复用**陈旧 leaf；未救活时**保留**快路径
- 负向 `.harness/spike/probe_m87_negative.py`：八个注入，失败集**逐项相等**、无 `error`、
  两个文件逐字节还原（`md5` 一致）、还原后复跑绿 ⇒ `ALL PASS`
  1. 节流改回「只一次」⇒ `{test_rewakes_after_gate_closed_by_target, test_trace_does_not_flood}`
  2. 删掉 hit-test 之前的唤醒 ⇒ `{test_wake_runs_even_when_hit_test_raises}`
  3. 删掉终局标记 ⇒ `{test_hopeless_pid_is_not_retried}`
  4. 删掉「救活后强制重画」⇒ `{test_hover_repaints_when_tree_revived_while_mouse_still}`
  5. 返回值恒 True ⇒ `{test_returns_true_only_when_gate_was_written}`
  6. `already_active` 也 poke ⇒ `{test_activate_skips_poke_in_steady_state}`
  7. agent 恒传 `poke=True` ⇒ `{test_steady_state_passes_poke_false}`
  8. 救活后仍复用 leaf ⇒ `{test_capture_key_drops_stale_leaf_when_tree_revived}`
- 全量：**1005 tests / 0 failed / 0 error / 22 skipped**
- 门禁：`FULL GATE PASSED` + rc=0

## 四、边界（明确未做）

- **没有做「设系统读屏标志 + 重启微信」这条持久路径**（`SPI_SETSCREENREADER`）。
  它是影刀对「微信未启动」场景的推荐做法，但需要重启目标；本轮改为运行时保活。
  若目标关闭 gate 的周期短于 1 秒，保活会变成来回抖动 —— 那时才需要考虑持久路径。
- 保活是**写第三方进程内存**（每秒最多一次单字节读，必要时一次单字节写）。写内存仍
  默认开启（M86 决定），`restore()` 可还原。
- 未验证：其它 Qt 应用（只认模块名 `Weixin.dll`）、企业微信、`desktop.click` 点到
  mmui 控件（M86 边界不变）。
