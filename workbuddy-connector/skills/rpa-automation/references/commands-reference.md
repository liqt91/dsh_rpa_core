# 命令参考（26 条）

完整契约以 `rpa-core catalog` 为准（含 input_schema/output_schema JSON Schema）。下表是摘要：`*` = 必填。

## 浏览器（browser.playwright）

| 命令 | 输入 | 输出 |
|---|---|---|
| `browser.navigate` | url*, timeoutMs, headless, userAgent, userDataDir, transport, browserInstanceId, keepOpen | sessionId, url, resourceType |
| `browser.click` | sessionId*, selector*, timeoutMs | matchedCount, sessionId |
| `browser.input` | sessionId*, selector*, text*, timeoutMs | matchedCount, sessionId |
| `browser.waitFor` | sessionId*, selector*, timeoutMs | matchedCount |
| `browser.getText` | sessionId*, selector*, timeoutMs | value |
| `browser.queryAll` | sessionId*, selector* | items, count |
| `browser.close` | sessionId* | — |

`transport`：`playwright`（默认，独立自动化浏览器）/ `bsk`（复用用户真实已登录浏览器，仅主 frame + CSS）。

## 数据（python.worker，隔离子进程）

| 命令 | 输入 | 输出 |
|---|---|---|
| `data.format` | template*, values* | text |
| `data.limit` | items*, count* | items, count |
| `data.writeJson` | workspace*, path*, data* | path |
| `data.writeText` | workspace*, path*, text, lines | path |

`workspace` 是文件写根目录（越界写入拒绝）。

## 桌面 UIA（desktop.uia，Windows）

| 命令 | 输入 | 输出 |
|---|---|---|
| `desktop.attachWindow` | title*, processId, timeoutMs | sessionId, processId, workWindowId |
| `desktop.findElement` | sessionId*, locator*, timeoutMs | elementId, matchedCount |
| `desktop.click` | sessionId*, elementId*, timeoutMs | — |
| `desktop.input` | sessionId*, elementId*, text*, timeoutMs | — |
| `desktop.getText` | sessionId*, elementId*, timeoutMs | value |
| `desktop.closeSession` | sessionId* | — |

`locator` 是对象，常用键：`backend: "uia"`、`controlType`、`automationId`、`name`。用 `rpa-core capture desktop` 捕获生成。

## 桌面 Win32（desktop.win32，Windows）

同上，另有：

| 命令 | 输入 | 输出 |
|---|---|---|
| `desktop.win32.hotkey` | sessionId*, keys*, timeoutMs | — |
| `desktop.win32.menuSelect` | sessionId*, menuPath*, timeoutMs | — |

win32 的 attachWindow 支持 title / className / handle / processId。
