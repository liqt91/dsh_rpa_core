# M81：微信自绘真机复验（Qt 窗口 + 跨 DPI 重采样）

Status: `done`

> **2026-11-17 立项**。M79 的微信项是 `SKIP`（当时判成「没有可见窗口」），这条欠账不能一直挂着。
> 真正的原因是**探针识别方式错了**：微信 4.x 是 Qt 自绘窗口（类名 `Qt51514QWindowIcon`），
> 可执行文件叫 `Weixin.exe`；探针只认 `WeChatMainWndForPC` / 标题含「微信」，
> 于是一个开着的主窗口被判成「不存在」。
>
> 约束：只取左侧导航栏的图标像素，**不点击、不输入、不读聊天内容**；探针只在
> `RPA_DESKTOP_E2E=1` 时前置窗口；语义空命中仍禁止自动降级到图像（ADR 0003）。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| 认得微信 4.x | 按进程路径（`weixin.exe` / `wechat.exe` / `wxwork.exe`）识别，不靠类名 |
| 证明必须走图像 | Qt 窗口 UIA descendants 的 `controlType` 为空、无交互控件 |
| 自绘控件可入库 | `--image` 从真实像素产出 V2 proposal（`matchedCount=0`） |
| 真机命中 | `count=1`，矩形与 `sourceRect` 逐像素一致，`usedScale=1.0` |
| 跨 DPI 命中 | 1.25 倍模板 → `usedScale=0.8`，矩形仍与 100% 一致 |
| 对照成立 | 关掉重采样（40×40 直配）→ `count=0`，证明 0.8 真起作用 |
| 模板非退化 | 模板灰度标准差 ≥ 3，避免拿纯色块做「匹配到哪都成立」的假验证 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | `probe_m81_wechat_real.py`：按进程路径认窗口 | 识别出 `class=Qt51514QWindowIcon` / `exe=weixin.exe` | done |
| S2 | 真实像素捕获 + V2 字段校验 | `schema=2` / `dpi=96` / `scale=1.0` / `sourceRect` / `matchedCount=0` | done |
| S3 | 100% 活体命中 + `--shot` 同源 | `count=1`，box = rect − windowOrigin，`usedScale=1.0` | done |
| S4 | 跨 DPI 重采样 + 对照 | 1.25 模板 → `usedScale=0.8`；关重采样 → `count=0` | done |
| S5 | 修 M79 探针的同一处识别 bug | M79 也改按进程路径判微信，避免下次再判成「没有窗口」 | done |

## 三、真机证据（2026-11-17，微信 4.x / Qt / 系统 DPI=96）

```text
PASS | 微信窗口识别 — hwnd=10159846 class=Qt51514QWindowIcon exe=weixin.exe
PASS | 窗口 DPI 取证 — dpi=96 缩放≈100%
PASS | 微信前置 — foreground=True rect=(879, 6, 1919, 878)
PASS | UIA 自绘取证 — descendants controlType=[] 交互控件=无
PASS | V2 provenance 字段 — schema=2 dpi=96 scale=1.0 size=[32, 32] sourceRect=[904, 138, 32, 32]
PASS | proposal 不冒充实测 — image 候选 matchedCount=[0]（应为 0）
PASS | 模板非退化自查 — 灰度标准差=31.44
PASS | 100% 活体命中 — count=1 rect={904,138,32,32}
PASS | --shot 回执同源 — box={x:25, y:132, 32x32} windowOrigin=[879, 6] usedScale=1.0
PASS | 100% 尺度证据 — usedScale=1.0
PASS | 125% 模板重采样命中 — count=1 rect={904,138,32,32} usedScale=0.8
PASS | 对照：不重采样应失配 — count=0
M81 WECHAT REAL: 12 pass / 0 fail / 0 skip
```

`UIA 自绘取证` 是本任务最重要的一条：Qt 窗口的 UIA 树**完全是空的**（`descendants`
的 `controlType` 集合为空、没有一个 Button/Edit/ListItem）。这就是「语义定位在微信上
不可能成立、只能走显式图像」的现场证据，也是 ADR 0003 允许走第 4 层的前提。

捕获目标只是左侧导航栏的「通讯录」人形图标（约 41,148 相对窗口），纯灰矢量 UI 图标，
不含任何文字、联系人名或聊天内容。

## 四、口径边界（不许含糊）

- 本机系统 DPI 是 96（100%），**没有真的切到 125%**。跨 DPI 那一项是「真机像素 + 模拟
  `captureScale=1.25`」：模板由真机 32×32 放大到 40×40，provenance 改成 125% 捕获。
  能证明重采样链路在真实截图上成立，但**不能**说「真机 125% 环境已验证」。
- `usedScale=0.8` 不是看日志猜的：对照组把 `captureScale` 改回 1.0（40×40 直配）
  得到 `count=0`，说明不重采样就真失配。
- 微信模板精度目前只覆盖「导航栏图标」这一类高对比矢量图；聊天气泡、头像、小尺寸
  文字类控件未验证。

## 五、顺带修掉的 bug

`probe_m79_real_verification.py` 里同样的识别条件（只认类名/标题）已改为按进程路径判，
否则下次占机复验还会把开着的微信判成「没有可见窗口」。同一处错误在两个探针里出现，
是「纠错要 grep 全仓所有记录处」的又一次实例。

## 六、不在范围

- 真的切系统缩放（125%/150%/200%）复验，需要改系统设置并注销。
- 微信聊天区内部控件（气泡、输入框、表情面板）的模板精度。
- HLay 式控件树注入、Java hook：微信 4.x 是 Qt 渲染层，这两条路与它无关。
