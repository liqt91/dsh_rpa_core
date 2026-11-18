# M86：无障碍树唤醒（微信 4.x 的 QAccessible gate）

Status: `done`

> **2026-11-17 需求**：用户要求把影刀「捕获微信控件」的做法复刻到本仓。
>
> 前置事实链（这轮全部实测，不再从 XML 反推）：
> 1. 微信 4.2.0.13（Qt 5.15.14，`Weixin.dll` 196MB **静态链接** Qt，安装目录 29 个
>    文件、**无 `platforms` 目录**）。冷启动时 UIA 主窗 `Descendants` **恰好 1 个**
>    `MMUIRenderSubWindowHW`；MSAA `OBJID_CLIENT` kids=1 且 `accChild` 全
>    `E_INVALIDARG`、`accLocation` 回 `-32000`（OLEACC 默认窗口 proxy）；
>    `QueryInterface(IAccessible2)` = `0x80004002 E_NOINTERFACE`。
> 2. 影刀官方微信活动源码是**明文**：`%LOCALAPPDATA%\ShadowBot\users\<id>\apps\<app>\
>    xbot_extensions\activity_weixin_v4\wechat.py`。文件头写明微信真正的 UIA 类名是
>    `mmui::MainWindow`，mmui 树「按需暴露」，冷启动未启用读屏时只给 Qt 外壳。
> 3. 它在 `Weixin.dll` 里扫 `QAccessible` 的 active byte（`qt.accessibility.core`
>    引用点同段内、`48 85 C9 0F 84 ?? ?? ?? ?? 80 3D <disp32> 00 0F 84` 形态），
>    热写为 1 → 发 `WM_GETOBJECT` → **当前进程立即返回 mmui provider，无需重启**。
>
> 本机实测（4.2.0.13）：gate RVA = **0xB50ABC8**（`.data`）；**磁盘文件里这个字节
> 是 1，进程内存里是 0**（运行时被关掉）。热写 1 后主窗 `Descendants` **1 → 118**，
> 出现 `mmui::MainWindow` / `mmui::MainView`(aid=MainView) /
> `mmui::MainTabBar`(name=导航, aid=MainView.main_tabbar) /
> `mmui::XTabBarItem`×4(微信/通讯录/收藏/发现) / `mmui::HandoffTabbarView` /
> `mmui::MainTabBarSettingView`(name=更多)。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| 定位不靠硬编码 | 扫描合成 PE：gate 必须落在**可写段**、指令在**可执行段**、与 core 引用点同段且距离 ≤ 0x20000 |
| 不猜 | 没有 `qt.accessibility.core` 或其零引用 ⇒ 返回 `rva=None`，绝不按「第一个像的」写 |
| 只写一字节 | 写 `base + gate_rva`、值为 1、只写一次 |
| 幂等 | gate 已是 1 ⇒ `reason=already_active`、**不写**、但仍补发 `WM_GETOBJECT` |
| 不知道原值就不写 | 读失败 ⇒ `read_failed`、零写入（否则无法还原） |
| 写完要读回 | 读回 ≠ 1 ⇒ `write_failed`，不能当成功（否则用户对着空树等） |
| 只打真目标 | `looks_dormant` 只认 Qt 窗口壳（`Qt*WindowIcon`）且子节点 ≤1；Chromium/原生不算 |
| 失败可见 | 唤醒结果落 trace（`accessibility_wake` / `accessibility_wake_failed` / `accessibility_wake_error`），异常不打断捕获 |
| 文案可枚举 | `WakeResult.REASONS` 封闭集，调用方据此出提示 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | 新增 `capture/accessibility.py`：PE 解析、gate 定位、读写字节、休眠判定、`activate/restore` | 20 条判据绿 | done |
| S2 | `desktop_agent`：`--wake-accessibility`；`_hover_hit` 与 `capture_at` 两处接入；每 pid 只尝试一次 | trace 出现 `accessibility_wake` 且 `rva` 正确 | done |
| S3 | `DesktopCaptureSession(wake_accessibility=True)` 默认下发参数 | 宿主无需改动即生效；可显式关闭 | done |
| S4 | 修 `rip_xrefs_to` 的 REX 重复匹配（同一 `lea` 被按 6/7 字节算两次） | `core_xref_count` 10 → 5 | done |
| S5 | psapi 显式 `argtypes`（64 位句柄不声明 ⇒ `OverflowError`） | 真机点捕获不再抛 | done |
| S6 | 负向探针 `probe_m86_negative.py`（两处注入） | `ALL PASS` | done |

## 三、验证结果

- `tests/contract/test_accessibility_gate.py`：20 条（合成 PE 正/负例、休眠判定 6 组
  参数、`activate` 七个分支、reason 封闭集）
- 负向 `.harness/spike/probe_m86_negative.py`：
  - 注入「去掉 core-xref 早退」⇒ 失败集 = `{test_scan_requires_core_string,
    test_scan_filters_candidates_far_from_core_xref}`（与期望逐项相等）
  - 注入「去掉可写段检查」⇒ 失败集 = `{test_scan_rejects_gate_target_in_executable_section}`
  - 两次均 `1 failed` 级、无 `error`；逐字节还原 `md5=2e2f828d…`；复跑绿 ⇒ `ALL PASS`
- 真实 dll 回归：`gate_for_dll(Weixin.dll)` → `rva=0xB50ABC8`、`candidates=1`、
  `distance=0x925`
- 真机（`.venv` 直调 agent，`--point 600 300 --window-handle 4459320
  --wake-accessibility`）：trace `accessibility_wake ok=true reason=already_active
  rva=189836232(before=1,after=1) cls=Qt51514QWindowIcon`，stdout 正常回描述符、rc=0
- 全量：**992 tests / 0 failed / 0 error / 22 skipped**
- 门禁：`FULL GATE PASSED` + rc=0

## 四、边界（明确未做）

- **不注入 dll、不自建 provider**：只读写目标进程的**一个字节**。写内存默认开启
  （`wake_accessibility=True`），可用参数关闭；写入前的原值保留在 `WakeResult.before`，
  `restore()` 可还原。目标重启后自然回到 0。
- 只对**模块名 `Weixin.dll`** 生效（`activate(dll_name=)` 可传别的）。通用化
  「遍历模块找含 `qt.accessibility.core` 的那个」未做 —— 见 `.harness/tasks/BACKLOG.md`。
- 未验证：Qt 5.15 之外版本、微信 4.1.x/3.x、企业微信、以及「gate 打开后
  `desktop.click` 能否点到 mmui 控件」（本轮只验读树，未验点击）。
- 真实宿主 PPX 需重启 `ppx dev` 才加载新 `desktop.py`（子进程每次现起，但 `api.py`
  进程内模块缓存不刷）。
- `desktop.input` 对这类元素仍不支持（M82 边界不变）。
