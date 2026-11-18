# M79：捕获/校验真机复验（DPI / 遮挡 / 多窗口 / 微信自绘）

Status: `done`

> **2026-11-17 立项**。M74~M78 的契约都用替身证明编排，但真机第一次跑
> `probe_m79_real_verification.py` 得 `3 pass / 5 fail`，暴露了三个**替身看不见**的接线缺陷：
> ① 元素资产里的 locator 是 dict，执行器 `_find` 吃 `DesktopLocator`，漏归一化 ⇒ 每个窗口
> `AttributeError` 被报成 `find-failed`；② `metadata.windowHandle` 没传给桌面 verifier ⇒ 两个
> 相同 locator 的记事本叠放时按纯 Z 序命中了盖子窗口；③ 微信自绘区 UIA `ElementFromPoint`
> 拒绝访问时，显式图像捕获也跟着失败。本任务修这三条并真机复跑。
>
> 约束：不改定位权威、不放宽判据；语义空命中仍禁止静默降级到图像（ADR 0003）；
> 真机探针只在显式 `RPA_DESKTOP_E2E=1` 时抢前台，不进默认门禁。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| 语义校验真命中 | 真记事本 UIA Edit 定位 → `count=1` + 命中窗口 handle + rect + shot |
| 显式图像闭环 | `--image` 产出 `driver=image` proposal（matchedCount=0）→ 运行期模板命中 |
| 遮挡下命中原窗口 | 第二个记事本 TOPMOST 盖住目标，校验仍回**捕获时记下的** windowHandle |
| flash 前置命中窗 | `GetForegroundWindow()` 等于命中窗口，不是盖子窗口 |
| 微信自绘区 | 能移动/可见则真捕获+校验；离屏且系统拒绝移动时显式 SKIP，不许假装 PASS |
| 判据不假绿 | 三处修复逐条注入破坏 → 对应契约精确 `1 failed / 0 error`，md5 逐字节还原 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | `verification._default_desktop_verifier(expected_hwnd)` + `_expected_hwnd()` | 门面把 `metadata.windowHandle` 交给默认桌面 verifier；纯 Z 序被遮挡时仍优先原窗口 | done |
| S2 | `desktop_verification._default_finder` 归一化 dict → `DesktopLocator` | 生产 finder 入口断言收到模型对象，不让 dict 漏进执行器 | done |
| S3 | `desktop_agent.capture_at` 显式图像模式吞 `ElementFromPoint` 异常 | 自绘区 UIA 拒绝访问仍产出 proposal；非图像模式同异常必须照旧抛出 | done |
| S4 | 真机探针补 on-screen/无效产物守卫 + 负向验证 + 全门禁 | 8 pass / 0 fail / 1 skip；修复负向 3/3 + 计时预算负向 2/2；FULL GATE PASSED ×2（897 passed） | done |

## 三、真机证据（2026-11-17，系统 DPI=96 / 缩放 100%）

```text
PASS | DPI 取证 — 系统 DPI=96，缩放≈100%（未切系统档位）
PASS | 真窗口靶子 — notepad hwnd=4655354
PASS | 语义控件取证 — locator={'backend': 'uia', 'controlType': 'Edit', 'automationId': '15'}
PASS | 语义校验 + flash + shot — count=1 / windowHandle=4655354 / rect / shot
PASS | 按图捕获 — driver=image size=[32, 32]
PASS | 图像校验命中 — count=1 / windowHandle=4655354 / rect={left:80,top:80,width:32,height:32}
PASS | 遮挡下跨窗口命中 — cover=6621504，仍命中 4655354
PASS | flash 前置命中窗 — foreground=4655354 target=4655354
SKIP | 微信自绘区 — 窗口在屏外且无法移回主屏: (5, 'SetWindowPos', '拒绝访问。')
M79 REAL CHECK: 8 pass / 0 fail / 1 skip
```

微信这一项**不是 PASS**：本机微信窗口矩形落在屏幕外，系统拒绝把它移回主屏，
因此没有拿到真实模板命中。自绘区「UIA 拒绝访问也能出 proposal」由 S3 的契约判据钉住，
但模板精度/真实控件粒度仍未在本机取证，留在欠账里，不许拿 SKIP 冒充覆盖。

## 四、负向验证

`probe_m79_fix_negative.py`：三处注入均为合法语法的精准破坏，先跑对照（各 1 passed），
再注入并确认对应判据精确 `1 failed / 0 error`，最后按原字节还原并核 md5、查哨兵无残留：

```text
PASS|expected_hwnd 接线|对照={'passed': 1, ...} 注入={'failed': 1, 'error': 0}
PASS|dict → DesktopLocator 归一化|对照={'passed': 1, ...} 注入={'failed': 1, 'error': 0}
PASS|显式图像吞 UIA 拒绝访问|对照={'passed': 1, ...} 注入={'failed': 1, 'error': 0}
M79 FIX NEGATIVE: 3 pass / 0 fail
```

收口时还遇到两个**与捕获无关的环境型假红**：`test_pause` 的 100ms / `test_retry_backoff_cannot_exceed_workflow_deadline`
的 50ms 绝对预算在本机常因启动与事件写入开销先耗尽，`calls == 1` 变成 `calls == 0`。
只放宽绝对预算（0.5s deadline；pause 2s / backoff 2s），保持「deadline < 挂起或退避时长」
的语义比例不变；`probe_timing_budget_negative.py` 2/2 证明放宽后仍能抓住真实回归：

```text
PASS|两层 deadline 都不再管住 attempt|对照 1 passed → 注入 1 failed / 0 error
PASS|retry backoff 被跳过|对照 1 passed → 注入 1 failed / 0 error
TIMING BUDGET NEGATIVE: 2 pass / 0 fail
```

最终门禁用 `TMPDIR/TEMP/TMP=D:\Temp`（本机系统 Temp 不可写，默认会落到仓库根）连续两次
`897 passed, 20 skipped, 2 xfailed` + `FULL GATE PASSED`。

## 五、不在范围

- HLay 式 DLL 注入造控件树、Java hook、AI 语义锚点/远端语义定位。
- 模板版本管理、DPI 变化后的模板自适应重采样。
- 微信真实自绘区的模板命中精度（本机离屏且禁移动，见上）。
- 提交与 push（未授权）。
