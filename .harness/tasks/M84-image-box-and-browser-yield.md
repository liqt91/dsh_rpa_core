# M84：红框与模板不同源 + Electron 应用被当成网页（微信粒度、WorkBuddy 认不出）

Status: `done`

> **2026-11-17 报障（M83 修完子进程之后的第一轮真机）**：
> 1. 「按图捕获」能抓桌面元素、能认出微信窗口，但**没有更细的粒度**，抓不到微信界面里的元素；
> 2. **WorkBuddy（本对话所在的 Electron 应用）连窗口都识别不出来**；
> 3. 纠正前一轮的误判：微信不在屏幕外，是开发机接了上下拼接的第二块屏（虚拟屏 `(0,-1080) 1920×2160`）。
>
> 两个根因都不在捕获算法里：
> - **红框与模板不同源**：`--image` 真正截的是「光标周围 32×32」，但 hover 高亮画的是
>   **语义元素矩形**。自绘控件（微信 Qt）没有控件树 ⇒ 语义命中只能退化到整窗 ⇒ 用户看到
>   框住整个窗口、确认框里也写着「桌面控件」，完全看不出到底截了多大一块，也没法把光标
>   对准某个按钮。另外确认框文案对图像定位直接落回 `桌面控件`，等于没有反馈。
> - **让位判据只看类名**：`Chrome_RenderWidgetHostHWND` 不只浏览器有，Electron 定制客户端
>   （WorkBuddy / 咚咚 / VS Code）的渲染子窗同样是这个类。旧判据把它们整片当成「网页内容区」：
>   高亮被隐藏、捕获手势被忽略 ⇒ **悬停毫无反应**。而 `foreground_window.py` 里早就写明
>   「必须按进程名判定，不能类名是 Chromium 系就认」，agent 与点击钩子两处都绕过了它。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| 红框 = 模板 | `_image_box_at()` 的四边换算成 `[x,y,w,h]` 与 `make_image_proposal` 的 `sourceRect` **逐值相等**（真机负坐标也验） |
| 单一几何权威 | 两者都调 `image_proposal.clip_template_rect()`，不许各写一份 min/max |
| 贴边不越界 | 光标在窗口边缘时模板裁到窗口内（不截到隔壁窗口） |
| 预览说真话 | 图像主定位时 `metadata.rect` 是模板框；非图像模式仍是元素矩形 |
| 让位看进程 | 同类名下 `workbuddy.exe` / `咚咚.exe` / `code.exe` ⇒ 不让位；`chrome.exe`/`msedge.exe` ⇒ 让位 |
| 认不出不猜 | 取不到进程名 ⇒ 不让位（宁可走桌面腿，也不把捕获让给一个不确定的扩展） |
| 不白跑 | 图像模式 `capture_with_retry` 只尝试 1 轮；语义模式仍 4 轮（反向钉住） |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | `image_proposal.py` 新增 `DEFAULT_RADIUS` + `clip_template_rect()`；`make_image_proposal` 改用它 | `sourceRect` 与原实现逐值一致 | done |
| S2 | `foreground_window.py` 新增 `BROWSER_CONTENT_CLASSES` / `is_browser_content()` / `browser_of_window()` / `process_name()` | 类名 + 进程名两个条件缺一不可 | done |
| S3 | agent：图像模式只画模板框（win32 取根窗口，不查 UIA）；不再对浏览器内容让位 | 红框跟手，自绘控件也能对准 | done |
| S4 | agent / click hook 的让位判据改走同一份权威（删掉各自私藏的类名集合） | `desktop_click_hook.is_browser_content is foreground_window.is_browser_content` | done |
| S5 | 图像主定位的 `metadata.rect` 改为模板框（`_describe_info` + `capture_at`） | 等值断言，非图像模式不受影响 | done |
| S6 | PPX `_selector_text`：图像定位显示 `图像 32×32 @ (x, y)` | 不再落回「桌面控件」 | done |
| S7 | 判据 13 条 + 负向 5 注入 | 期望失败集集合相等；还原后全绿 | done |

## 三、验证结果

- `tests/contract/test_browser_content_yield.py`（3）+ `test_image_capture_proposal.py`（10，含新增 4）：**13 passed**
- 负向 `.harness/spike/probe_m84_negative.py`：**5/5 精确红**，逐条 md5 还原、还原后全绿
  （退回只看类名 / 不覆盖 preview 矩形 / 点捕获不带模板矩形 / 模板框不裁边界 / 恢复 4 轮重试）
- 真机（本机负坐标屏，`.venv` 直调）：
  - 微信窗口 `hwnd=10159846` 光标 `(250,-600)` ⇒ 红框 `(234,-616,266,-584)`、`sourceRect [234,-616,32,32]` **一致**
  - WorkBuddy `hwnd=15009276` 光标 `(1400,-600)` ⇒ 红框 `(1384,-616,1416,-584)`、`sourceRect` **一致**
  - WorkBuddy 渲染子窗实测类名 `Chrome_RenderWidgetHostHWND`、进程 `WorkBuddy.exe` ⇒ `is_browser_content=False`（修复前为 True，即「整窗无反应」）
- 全量：**969 tests / 0 failed / 0 errored / 22 skipped**
- 门禁：`FULL GATE PASSED` + rc=0

## 四、遗留（明确未做）

- **模板尺寸不可调**：固定 `radius=16`（32×32）。文字按钮若字形分散，32×32 可能不够独特；
  可调尺寸（滚轮改大小 / 拖框选矩形）是下一步，不在本轮。
- **`desktop.input` 对图像元素**仍未支持（M82 的边界不变）。
- **真 125%/150% DPI 与 per-monitor 混合 DPI** 未验（M80 边界不变）。本轮只证负坐标拼接屏。
- 只修了「让位」这一条判据；`_BROWSER_CONTENT_CLASSES` 的类名集合仍是单个
  `Chrome_RenderWidgetHostHWND`——Chrome/Edge 若换类名，需要同步。
