# M95 — 任务栏元素校验 count=0：放行桌面壳窗口

Status: `done`

## 背景

用户保存「元素2」（开始图标右侧的搜索按钮，`Shell_TrayWnd` 下的 `TrayButton`），
点校验后报没命中。元素文档里的 locator 是
`backend=uia / controlType=Button / automationId=4103 / name=搜索`，
捕获时记下的 `windowTitle` 是空字符串。

## 取证

- `.harness/spike/probe_m95_taskbar.py`：跨窗口链 `visible_top_windows()` 只有 8 项，
  捕获时的 HWND 不在链里；`verify_element(preview)` 返回 `count=0`，耗时 0.18s
  ⇒ 不是慢，是根本没探测到宿主窗口。
- `_is_user_window()` 对空标题和 `_SHELL_WINDOW_CLASSES` 一律返回 False。
  任务栏窗口标题恒为空 ⇒ 无论 `preferred_hwnd` 插队还是 Z 序兜底都够不到它。
  这条过滤是 M74 S2 为「最近激活的浏览器」设计的，桌面校验直接复用了它。
- `.harness/spike/probe_m95_taskbar_uia.py`：取证进程所在会话看不到 `Shell_TrayWnd`，
  所以没有拿它当 UIA 属性漂移的证据；根因以纯函数 + 元素文档的空标题为准。

## 改动

- `foreground_window.py`
  - 新增 `_DESKTOP_SHELL_CLASSES`（仅 `Shell_TrayWnd` / `Shell_SecondaryTrayWnd`）。
  - `visible_top_windows(..., include_desktop_shell=False)`：开关打开时按原 Z 序位置
    放回可见任务栏窗口（空标题也放行）；默认链逐字不变，浏览器目标选择不受影响。
  - `find_window_for_element(..., include_desktop_shell=False)` 透传；不改动
    「preferred 不在链里就忽略」的既有契约。
  - 枚举阶段 `WS_EX_TOOLWINDOW` 过滤对壳窗口放行，否则业务层开关救不回被丢弃的行。
- `desktop_verification.py`：跨窗口探测传 `include_desktop_shell=True`。
- 红线：`writeElement`/`pickPlan` 未恢复，ref-only 与删除护栏未动；
  locator 仍用同一份 `DesktopExecutor._find`，没有新建第二套定位权威。

## 判据与验证

- 单测新增 2 条：默认链滤任务栏 / 开关后按原 Z 序探测并命中即停。
- 桌面校验契约新增 1 条：替身 API 里 `Shell_TrayWnd`（空标题）能被探测并返回其 hwnd。
- 元素拾取契约新增 1 组（25/25）：钉住「枚举保留壳窗 → 链放行 → verifier 真传开关」三处接线。
- `probe_m95_negative.py`：4 个注入失败集逐项相等、md5 逐字节还原、复跑全绿。
- 全量 `check_all.py`：`FULL GATE PASSED` + `rc=0`。

## 待用户真机确认

本探针进程所在会话看不到任务栏，无法代跑用户桌面的校验。用户重启工作台加载新
`foreground_window.py` / `desktop_verification.py` 后重新点元素2的校验，预期命中。
若仍不命中，下一步取证方向是 `automationId=4103` 是否漂移（候选回退），
而不是继续怀疑窗口链。
