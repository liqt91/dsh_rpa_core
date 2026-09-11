# ADR 0013：移除 playwright、浏览器执行/捕获收敛到自研扩展单通道

- 状态：已接受
- 日期：2026-09-11
- 关联：ADR 0001（clean-room RPA core / playwright 一度为浏览器驱动）、ADR 0007（设计期捕获契约）、ADR 0008（编辑器 UI）、`docs/extension-execution-plan.md`、`.trae/documents/remove-playwright-single-extension.md`

## 背景

自研 MV3 扩展的执行/捕获通道（`extension_exec` / `browser_ext` / `capture/extension`，自研 HTTP RPC）已完全独立于 playwright，
并覆盖浏览器命令面的主体。playwright 只余其作为「独立自动化浏览器」的次要执行通道与浏览器捕获的 persistent/user-browser 传输，
且 `navigate` 还暴露 `transport`/`channel`（浏览器类型）双轴。维护者决策：**彻底移除 playwright**，浏览器执行与网页捕获统一收敛到**自研扩展单通道**，
扩展离线即显式失败（不回退、不白等）；同时用扩展 content-script/background 补齐原 playwright 才覆盖的命令面，完整保留命令。

## 决策

### 1. 依赖移除

- `pyproject.toml` 删除 `playwright>=1.49,<2`；`uv.lock` 同步移除（`uv sync --all-groups` 已 grep 确认）。运行库不再依赖 playwright/greenlet/pyee。

### 2. 类名/注册键保留为历史命名（不构成 playwright 残留）

- 执行器类 `PlaywrightExecutor` 与注册键 `browser.playwright`、30 条 manifest 的 `executor`/`implementation.handler`、CLI 注册与前端 catalog **保留不动**——
  它们只作扩展单通道宿主的历史命名。仅改行为、不再 `import playwright`。

### 3. `browser.navigate` 参数精简（3.0.0）

- 删除 `transport`、`channel`、`args`、`headless`、`userAgent`、`userDataDir`、`waitUntil`；`x-param-groups` 收敛为「常规(url,action)」+「高级(timeoutMs, collapsed)」。扩展无「启动/切换浏览器」（浏览器＝插件装在哪），`channel` 语义整体消失。

### 4. 浏览器捕获仅扩展单传输

- `capture/__init__.py` 删 `BrowserCaptureSession`；`devserver/app.py` `_BROWSER_TRANSPORTS = frozenset({"extension"})`；`cli._browser_capture_factory` 强制 `transport="extension"`；CLI `--transport` choices 收紧为 `{extension}`。

### 5. 17 条命令由扩展补齐（完整命令面）

- DOM 读取/写入原语依次落地 content-script 注入函数：`queryAll`、`getPosition`、`getScrollPosition`、`getSelectOptions`、`setValue`、`setAttribute`、`drag`；
- background 扩展 API：cookie 四件套（`chrome.cookies`）、`screenshot`（`captureVisibleTab`）、`stopLoading`（`window.stop()`）、`waitLoad`（load 回调）；
- `upload`/`download`/`handleDialog` 成本高，先落「该能力扩展暂未实现」的显式错误，但 manifest 字段与新产出契约保留，不硬失败。

### 6. 得分门禁调整

- 依赖 playwright 作为浏览器驱动的前端 e2e 测试随依赖移除而删除：`test_editor.py`、`test_editor_element_edit.py`、`test_search_and_save.py`、`test_browser_dom_primitives.py`；
  一次性抓取脚本（`scripts/scrape_yingdao_*.py`）与零散 scratch 一并清理。门禁从 316 → 272 条。桌面 e2e（`test_capture.py`/`test_uia_desktop.py`/`test_windows_desktop.py`）保留并改写为扩展契约。

### 7. 前端/文档去 playwright

- `app.js`/`PropsPanel.vue`/`popup.js`/`extension_exec.py` 移除 transport/channel 的渲染与「回退 playwright」文案；README / api-usage / extension-execution-plan / ADR 0007 改写为扩展单通道；删除 `scripts/check_channel_preview.mjs`。

## 后果

- **无独立自动化浏览器**：浏览器命令只作用于用户真实（已登录）浏览器；扩展离线时命令前置预检 <1s 显式失败（`channel_offline`），不再白等整段 `timeoutMs`。
- `channel`/`transport` 双轴、静默回退、内置 Chromium 启动等概念在命令面彻底消失，用户心智更简单。
- 前端浏览器 UI 的 e2e 覆盖随 playwright 移除而删除（不再无真实 Chromium 驱动前端）；自动化浏览器场景若未来重现，须经能力层评估新的浏览器驱动方案而非借回 playwright 依赖。