# M100 — 元素校验：查之前先唤醒 + 命中 0 给可行动诊断

Status: `done`

## 背景

M98 定性了「微信关窗口再打开后校验不到」：窗口销毁时 UIA 树被扔掉，重开的
新窗口不会重建，写 gate / poke / 置前 / `SetWindowPos` / `TransformPattern`
逐项实测全无效。当时的结论是「UIA 侧救不了」，于是代码一行没改，只留了两条待办。

用户追问「那校验这个问题修了吗」之后重新对齐了两件事：

1. 微信的树是**进程首次被 UIA 查询时**按 gate 决定建不建的。捕获路径带
   `--wake-accessibility`，先写 gate 再做第一次查询 ⇒ 树活；校验路径从不唤醒，
   它自己就是那个「第一次查询」——一旦由它来查，进程就被定死在「无树」状态。
   M98 那次之所以「写 gate 也无效」，是因为探针在此之前已经查过了。
2. 因此能修的不是「救回已定死的进程」，而是**别让校验把进程定死**：把唤醒提到
   第一次 UIA 查询之前，和捕获走同一套 `accessibility.activate`。

另有两条 M98/M99 记下的欠账一并处理：`poke_provider` 不看返回值（日志恒 ok）、
校验命中 0 与「选择器写错」不可区分。

## 改动

### 1. 唤醒（`capture/accessibility.py`）

- `poke_provider()` 返回 `bool`（`SendMessageTimeoutW` 是否送达），并声明
  `argtypes/restype`。M98 实测微信返回 0（err 5 拒绝访问），以前日志照写 `ok=true`。
- `WakeResult.poked`：`None` = 没试（`poke=False`），`True/False` = 送达/未送达。
  **与 `ok` 分开**：gate 写成 1 是既成事实，poke 失败只说明 provider 没被叫醒。
- `activate()` 把 poke 结果写进返回值；`desktop_agent` 的 trace 增 `poked` 字段。
- 新增 `window_class_name(hwnd)` 与 `wake_if_dormant(hwnd, *, dll_name, poke)`：
  只对类名像 `Qt*WindowIcon` 的窗口动手，其余返回 `None`（一个字节都不碰）。
  给「捕获以外的一次性入口」用；捕获侧的节流/状态缓存留在 `desktop_agent`。

### 2. 顺序（`capture/foreground_window.py`）

`find_window_for_element(..., before_probe=)`：在该窗口**第一次 probe 之前**调用，
每个窗口至多一次，异常吞掉只 trace。顺序是本函数的既有核心契约，扩展点必须挂在这里，
不能让调用方自己再枚举一遍 Z 序。

### 3. 接线与诊断（`capture/desktop_verification.py`）

- 构造参数新增 `waker` / `tree_count`（都可注入）。
- `_wake_before_query()` 作为 `before_probe` 传进去；只记「真试过且不顺利」的情形。
- `_find_across_windows()` 多回一个 `checked`（真正 probe 过的窗口）。
- `_tree_missing()`：命中 0 时，对**类名像 Qt 壳**的窗口数一次后代；`<= 1` 即判定
  对方没有树。原生窗口（记事本/Chromium）本来就可能只有一层，不参与判定。
- `_run()` 仅 `mode == "flash"` 做诊断（preview 边打字边跑，不值当多一轮 UIA），
  命中 0 且有树问题 → `{"count": 0, "hint": "tree-missing"}`。

### 4. 透传到前端

- `verification.VerificationResult.to_dict()` 透传 `hint`（与 `shot`/`usedScale` 同位置）。
- `.harness/demo/ppx_probe/app/gui/src/main.js`：`count === 0` 且 `hint === 'tree-missing'`
  时显示「没命中：该窗口没有无障碍树，退出应用重开后再试」，否则仍是「这次没命中」。

## 判据

`tests/contract/test_desktop_verification.py` 新增 8 条（共 21）：

| 用例 | 守什么 |
|---|---|
| `test_wake_happens_before_each_window_is_queried` | ★ 顺序：每窗先 wake 后 find，且只一次 |
| `test_wake_is_not_repeated_when_first_window_hits` | 命中即停，后面窗口不查也不唤醒 |
| `test_wake_exception_does_not_break_verification` | 唤醒异常不打断校验 |
| `test_count_zero_reports_tree_missing_for_dormant_qt_shell` | Qt 壳 + 后代 1 → `hint` |
| `test_count_zero_has_no_hint_when_tree_is_alive` | 树有 12 个后代 → 不报 hint |
| `test_native_window_never_gets_tree_hint` | 原生窗口不参与判定 |
| `test_preview_skips_tree_diagnosis` | preview 不做额外 UIA 查询（`tree_count` 零调用） |
| `test_hint_survives_unified_result_shape` | 穿过统一门面后 `to_dict()` 仍带 hint |

`tests/contract/test_accessibility_gate.py` 新增 6 条（共 27）：poke 送达/拒绝/未尝试
三态、`wake_if_dormant` 不动原生窗口 / 动 Qt 壳 / hwnd=0。

`scripts/check_element_library.mjs` 新增 2 条（28/28）：hint 走专门文案、无 hint 时
不误导用户「重开应用」。

## 负向验证

`.harness/spike/probe_m100_negative.py`，9 个注入、失败集逐项相等、md5 还原、复跑绿：

| 注入 | 失败集 |
|---|---|
| `wake-after-probe` | 顺序 1 条 |
| `wake-not-called` | 2 条（顺序 + 命中即停） |
| `hint-always-when-zero` | 3 条 |
| `tree-hint-for-native` | 1 条 |
| `tree-count-ignored` | 1 条 |
| `hint-not-forwarded` | 1 条 |
| `poke-result-dropped` | 2 条 |
| `wake-touches-native` | 1 条 |
| `js-hint-branch-removed` | 前端 1 条 |

第一版 `wake-after-probe` 写成 `if probe(window) and before_probe(...)`，把 finder
调了两次，连带压红 4 条无关用例——这是**注入写坏**，不是判据松。改成「两处替换：
关掉 before_probe、在 finder 之后补唤醒」，语义只动顺序。

## 门禁

`check_all.py` → `FULL GATE PASSED` + `rc=0`（1046 passed / 20 skipped / 2 xfailed）。

## 边界（本单不承诺的）

- **已经定死的进程救不回来**：微信窗口关掉再打开（进程没换），新窗口仍无树。
  本单只保证「校验不再自己把进程定死」——微信刚启动、校验是第一个查询者时，
  唤醒能生效。这条只有下次真机复验能证实。
- 校验路径唤醒对**每个 Qt 候选窗口**各试一次，没有 agent 侧的 hopeless 缓存；
  一次点击多几十毫秒，换来的是不必等用户先捕获一次。

## 真机待复验

把微信彻底退出（不是关窗口）后重新启动，**不要先捕获**，直接对已保存的搜索框
点「校验」：

- 命中 ⇒ 唤醒生效；
- 仍 0 且行内出现「该窗口没有无障碍树」⇒ 校验确实没能抢到首次查询（或该版本
  gate 已定死），此时文案至少把「改选择器没用」说清楚了。
