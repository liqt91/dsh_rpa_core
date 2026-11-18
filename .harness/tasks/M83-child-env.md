# M83：桌面捕获子进程起不来（「微信/桌面没有框选」的真因）

Status: `done`

> **2026-11-17 报障**：维护者打开 PPX 工作台，点「捕获元素」与「按图捕获」，
> **两个按钮都只表现为网页里框选**，微信与桌面完全没有红框、也抓不到东西。
>
> 根因不是捕获逻辑，而是**桌面腿的子进程从来没活过**：PPX 后端用
> `sys.path.insert(0, REPO/"src")` 挂包（`api.py:48`），而 `sys.path` **不出进程**。
> `DesktopCaptureSession` 用 `[sys.executable, "-m", "rpa_core.capture.desktop_agent"]`
> spawn，子进程 `ModuleNotFoundError: No module named 'rpa_core'` 秒退。
> 父进程里 `import rpa_core` 完全正常，所以没有任何异常可以捕获。
>
> 次生问题：桌面腿出局时扩展腿还活着 ⇒ `_exhausted` 不成立、会话继续等，
> 浮窗上**照样写着「桌面：就绪」** —— 用户按 F9 干等到超时，看不到任何原因。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| 子进程能 import | 只改过 `sys.path`、没有 `PYTHONPATH` 的宿主里，agent 能进 `hover` 模式 |
| 单一权威 | 四处 `-m rpa_core.*` spawn 全部走 `procenv.child_env()`，不许各拼一份 |
| 不重复注入 | `PYTHONPATH` 已含导入根时不重复追加（`normcase` 比较） |
| 打包态不污染 | `sys.frozen` 时原样返回，不塞 `PYTHONPATH` |
| 故障可见 | 桌面腿真故障 ⇒ 浮窗写「捕获进程起不来（原因）」且 `desktopOk=False` |
| 不误报 | 用户没按热键的 `timeout` **不算**故障，浮窗仍写「就绪」 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | 新增 `rpa_core/procenv.py`：`import_root()` / `child_env()` | 纯函数；只读 `sys.modules["rpa_core"].__file__` | done |
| S2 | 四处 spawn 接线：捕获 agent、点击钩子、OCR worker、Python worker | 全部 `env=child_env()` | done |
| S3 | `HybridCaptureSession.desktop_failure` + `desktop_status` 说真话 | 只记带 `error` 的结果；文案截断到 40 字 | done |
| S4 | 判据 8 条 + 负向 4 注入 | 期望失败集集合相等；还原后全绿 | done |

## 三、验证结果

- `tests/contract/test_procenv.py`（5）+ `test_hybrid_desktop_failure.py`（3）：**8 passed**
- 负向 `.harness/spike/probe_m83_negative.py`：**4/4 精确红**，逐条 md5 还原
  （拆注入 → 1 红；不记 failure → 2 红；文案不读 failure → 2 红；反向「永远报起不来」→ 1 红）
- 真机 `.harness/spike/probe_m83_child_env.py`（**用 ppx-py 解释器跑**）：
  `available=True`、`pick` 走超时正常返回、agent trace `main_enter.mode=hover`。
  ★ 不能用 `.venv` 验这件事：那是 editable 安装，在哪儿都能 import，跑出来恒绿。
- 全量：**962 tests / 0 failed / 0 errored / 22 skipped**
- 门禁：`FULL GATE PASSED` + rc=0

## 四、遗留（明确未做）

- **鼠标注入在本机不可用**：`SetCursorPos` 返回 0 却不移动、`mouse_event` 只挪十几像素
  （被当前会话的输入限制拦下）。所以「F9 真捕一次微信」这条只能由维护者手点验证，
  我没有伪造它。
- **`_BROWSER_CONTENT_CLASSES` 只有 `Chrome_RenderWidgetHostHWND`**：Chrome/Edge 新版本
  的渲染窗口类名可能不同，届时 hybrid 让位会失效（网页区域被桌面腿抢）。本轮未改。
- 桌面腿没有启动期健康探针：根因修掉后子进程不会因为「导入路径」再死，但**别的**
  启动期崩溃仍要等 `pick` 返回才可见（浮窗文案会给出原因，不是静默）。
