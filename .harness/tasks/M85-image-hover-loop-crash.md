# M85：图像捕获「鼠标一动就报捕获进程起不来」（hover 循环串槽）

Status: `done`

> **2026-11-17 报障**：「按图捕获，刚打开没有错误提示，鼠标一移动，就提示桌面捕获进程起不来」。
>
> 真因不在环境（用户上次刚被 M83 的「子进程起不来」误导过一轮），而在 M84 新写的 image
> 分支里：它把模板框 `(left, top, right, bottom)` 四元组写进了 `last_rect`。那个槽子是留给
> **UIA RECT 对象**的——下一帧 `inside_last = _rect_contains(last_rect, x, y)` 按 `.left` 读，
> 立刻 `AttributeError: 'tuple' object has no attribute 'left'`。hover 主循环没有兜底，异常
> 打死整个 agent；父端 `DesktopCaptureSession.pick` 只读得到「exited with code 1」，浮窗于是
> 写「桌面：捕获进程起不来（exited with code 1）」，把用户引去查环境。
>
> 为什么 M84 的验证没抓到：那轮验的是 `_image_box_at()` 的算术与 `sourceRect` 相等，探针
> **直接调几何函数**，从没跑过用户实际走的那条 hover 循环。典型「覆盖实现 ≠ 覆盖用户走的路」，
> 且这类串槽 bug 单帧永远测不出来（第一帧赋值、第二帧才炸）。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| 连续多帧不崩 | `_hover_capture(prefer_image=True)` 打桩跑 ≥3 帧，返回 `{"timeout": True}` 而不是抛 |
| 每帧都重画 | 鼠标每帧移动 8px ⇒ `show_rect` 调用次数 = 帧数、无重复矩形 |
| 框尺寸不漂 | 每次画出的框宽高 == 模板边长（32） |
| 崩溃要说原因 | agent 内未处理异常 ⇒ stdout 一行 `{"error": "桌面捕获内部错误：<类型>: <消息>"}`、rc=0、traceback 进 stderr |
| 正常路径不掺假 | 正常返回时 stdout 只有一行结果、stderr 为空 |
| 文案不预设病因 | 桌面腿出局时是「桌面：捕获不可用（<原因>）」，不再硬编码「起不来」 |
| 取证能分辨模式 | `main_enter` trace 带 `image=True/False` |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | 删掉 image 分支的 `last_rect = box`，注释写明该槽只放 UIA RECT | 多帧用例绿、注入即红 | done |
| S2 | `main()` 包 `except Exception`：traceback 打 stderr、结构化 error 打 stdout、rc=0 | 崩溃契约 2 条 | done |
| S3 | `sys.stdout/stderr.reconfigure` 失败不再致命（非标准流） | 使 S2 可测，真实进程行为不变 | done |
| S4 | `desktop_status` 前缀改「捕获不可用」；截断上限 40→60 | 原因不再被砍掉关键信息 | done |
| S5 | `main_enter` trace 补 `image` 字段 | 日志能区分「按图捕获」与「捕获元素」 | done |
| S6 | 判据 3 条 + 负向探针 `probe_m85_negative.py` | `ALL PASS`（对照绿 / 注入红 / md5 还原 / 复跑绿） | done |

## 三、验证结果

- `tests/contract/test_desktop_image_hover_loop.py`：多帧不崩 + 逐帧重画 + 框宽 32
- `tests/contract/test_desktop_agent_crash_report.py`：崩溃结构化回传 + 正常路径对照
- `tests/contract/test_hybrid_desktop_failure.py`：断言改为「不可用 + 原因」
- 负向 `.harness/spike/probe_m85_negative.py`：对照绿 → 注入 `last_rect = box` **精确红**
  （`1 FAILED`、`AttributeError`、无 `error`）→ 逐字节还原 `md5=b0fa60…` → 复跑绿
- 真机进程级（`.venv` 直调，`--hover --hybrid --image --timeout 6`）：
  - 修复前 `rc=1` + `AttributeError: 'tuple' object has no attribute 'left'`（用户报障原样复现）
  - 修复后 `rc=0` + `{"timeout": true}`、stderr 空
- 全量：**950 passed / 20 skipped / 2 xfailed / 0 failed**
- 门禁：`FULL GATE PASSED` + rc=0

## 四、遗留（明确未做）

- 本轮没在 PPX 界面里点一遍（真机进程级已证循环不死）；宿主侧需重启 `ppx dev` 才加载新
  `desktop_agent`（子进程每次现起，但 `api.py` 进程内模块缓存不刷）。
- 「捕获框内所有内容」仍是固定 32×32（M84 边界不变）。
- `desktop.input` 对图像元素仍不支持（M82 边界不变）。
- hover 主循环的**其它**分支仍没有整段兜底：S2 只保证「崩了会说原因」，不保证「不崩」。
