# 原子能力目录（89 条 = 当前 catalog 全量）

> 由 `commands/**/*.json` 生成，catalog digest `9f4f273419c78a21…`。
> **先跑 `rpa-core catalog` 对齐版本**：digest 不一致就说明本机 CLI 与本表不同代，
> 以本机输出为准，不要照表硬调。


## 浏览器（32 条）

网页上的点击、输入、取值、截图、Cookie、下载、多标签。每条命令都要带 `sessionId`。

| 命令 | 中文名 | 副作用 | 必填参数 |
|---|---|---|---|
| `browser.attach` | 获取已打开的网页 | 会话 | `sessionId`、`pattern` |
| `browser.check` | 设置复选框 | 不可重复写 | `sessionId`、`selector` |
| `browser.click` | 点击元素 | 不可重复写 | `selector` |
| `browser.close` | 关闭网页 | 会话 | — |
| `browser.closeBrowser` | 终止浏览器 | 会话 | — |
| `browser.closeTabs` | 关闭标签页 | 不可重复写 | — |
| `browser.cookieGet` | 读取 Cookie | 只读 | `sessionId`、`name` |
| `browser.cookieGetAll` | 获取 Cookie 列表 | 只读 | `sessionId` |
| `browser.cookieRemove` | 删除 Cookie | 不可重复写 | `sessionId` |
| `browser.cookieSet` | 设置 Cookie | 可重复写 | `sessionId`、`cookies` |
| `browser.download` | 下载文件 | 不可重复写 | `saveDir` |
| `browser.drag` | 拖拽元素 | 不可重复写 | `sessionId`、`selector`、`targetSelector` |
| `browser.executeScript` | 执行JS脚本 | 不可重复写 | `script` |
| `browser.getPosition` | 获取元素位置 | 只读 | `selector` |
| `browser.getScrollPosition` | 获取滚动条位置 | 只读 | — |
| `browser.getSelectOptions` | 获取下拉框选项 | 只读 | `selector` |
| `browser.getText` | 读取元素文本 | 只读 | `selector` |
| `browser.handleDialog` | 处理对话框 | 不可重复写 | — |
| `browser.hover` | 鼠标悬停 | 不可重复写 | `selector` |
| `browser.input` | 输入文本 | 不可重复写 | `selector`、`text` |
| `browser.listPages` | 获取网页列表 | 只读 | `sessionId` |
| `browser.navigate` | 打开网页 | 会话 | `browserType` |
| `browser.queryAll` | 抓取列表文本 | 只读 | `selector` |
| `browser.screenshot` | 网页截图 | 可重复写 | `savePath` |
| `browser.scroll` | 滚动网页 | 不可重复写 | `sessionId`、`position` |
| `browser.select` | 设置下拉框 | 不可重复写 | `selector`、`value` |
| `browser.setAttribute` | 设置元素属性 | 不可重复写 | `selector`、`name`、`value` |
| `browser.setValue` | 设置元素值 | 不可重复写 | `selector`、`value` |
| `browser.stopLoading` | 停止网页加载 | 不可重复写 | — |
| `browser.upload` | 上传文件 | 不可重复写 | `selector`、`files` |
| `browser.waitFor` | 等待元素出现 | 只读 | `selector` |
| `browser.waitLoad` | 等待页面加载 | 只读 | `sessionId` |

## 桌面（Win32）（19 条）

按窗口/控件句柄直接操作，适合标准 Win32 控件与被 UIA 挡住的场景。

| 命令 | 中文名 | 副作用 | 必填参数 |
|---|---|---|---|
| `desktop.win32.activateWindow` | 激活窗口（Win32） | 不可重复写 | — |
| `desktop.win32.attachWindow` | 附着窗口（Win32） | 会话 | — |
| `desktop.win32.click` | 点击控件（Win32） | 不可重复写 | `elementId` |
| `desktop.win32.closeSession` | 关闭会话（Win32） | 会话 | — |
| `desktop.win32.drag` | 拖拽元素（Win32） | 不可重复写 | `elementId`、`targetX`、`targetY` |
| `desktop.win32.findElement` | 查找控件（Win32） | 只读 | `locator` |
| `desktop.win32.getSelectedText` | 获取选中文本（Win32） | 只读 | `elementId` |
| `desktop.win32.getText` | 读取控件文本（Win32） | 只读 | `elementId` |
| `desktop.win32.getWindowList` | 获取窗口列表（Win32） | 只读 | — |
| `desktop.win32.getWindowTitle` | 获取窗口信息（Win32） | 只读 | — |
| `desktop.win32.hotkey` | 组合按键（Win32） | 不可重复写 | `keys` |
| `desktop.win32.input` | 控件输入（Win32） | 不可重复写 | `elementId`、`text` |
| `desktop.win32.menuSelect` | 菜单选择（Win32） | 不可重复写 | `menuPath` |
| `desktop.win32.moveWindow` | 移动窗口（Win32） | 不可重复写 | `x`、`y` |
| `desktop.win32.resizeWindow` | 调整窗口大小（Win32） | 不可重复写 | `width`、`height` |
| `desktop.win32.screenshot` | 元素截图（Win32） | 可重复写 | `elementId`、`savePath` |
| `desktop.win32.select` | 设置下拉框（Win32） | 不可重复写 | `elementId`、`value` |
| `desktop.win32.setWindowState` | 设置窗口状态（Win32） | 不可重复写 | — |
| `desktop.win32.setWindowVisible` | 设置窗口显隐（Win32） | 不可重复写 | — |

## 桌面（UIA）（17 条）

按 UI Automation 语义操作，适合现代桌面应用（记事本、Office、浏览器窗口本身）。

| 命令 | 中文名 | 副作用 | 必填参数 |
|---|---|---|---|
| `desktop.activateWindow` | 激活窗口（UIA） | 不可重复写 | — |
| `desktop.attachWindow` | 附着窗口（UIA） | 会话 | — |
| `desktop.click` | 点击控件（UIA） | 不可重复写 | `elementId` |
| `desktop.closeSession` | 关闭会话（UIA） | 会话 | — |
| `desktop.drag` | 拖拽元素（UIA） | 不可重复写 | `elementId`、`targetX`、`targetY` |
| `desktop.findElement` | 查找控件（UIA） | 只读 | `locator` |
| `desktop.getSelectedText` | 获取选中文本（UIA） | 只读 | `elementId` |
| `desktop.getText` | 读取控件文本（UIA） | 只读 | `elementId` |
| `desktop.getWindowList` | 获取窗口列表（UIA） | 只读 | — |
| `desktop.getWindowTitle` | 获取窗口信息（UIA） | 只读 | — |
| `desktop.input` | 控件输入（UIA） | 不可重复写 | `elementId`、`text` |
| `desktop.moveWindow` | 移动窗口（UIA） | 不可重复写 | `x`、`y` |
| `desktop.resizeWindow` | 调整窗口大小（UIA） | 不可重复写 | `width`、`height` |
| `desktop.screenshot` | 元素截图（UIA） | 可重复写 | `elementId`、`savePath` |
| `desktop.select` | 设置下拉框（UIA） | 不可重复写 | `elementId`、`value` |
| `desktop.setWindowState` | 设置窗口状态（UIA） | 不可重复写 | — |
| `desktop.setWindowVisible` | 设置窗口显隐（UIA） | 不可重复写 | — |

## 数据 / 文件（17 条）

不碰界面的纯数据步骤：读写文件、格式化、时间、路径、JSON/CSV。

| 命令 | 中文名 | 副作用 | 必填参数 |
|---|---|---|---|
| `data.appendText` | 追加写入文本 | 不可重复写 | `workspace`、`path` |
| `data.datetimeNow` | 获取当前时间 | 纯函数 | — |
| `data.deletePath` | 删除路径/文件 | 不可重复写 | `workspace`、`path` |
| `data.fileExists` | 判断文件是否存在 | 只读 | `workspace`、`path` |
| `data.format` | 格式化文本 | 纯函数 | `template`、`values` |
| `data.limit` | 截取前 N 条 | 纯函数 | `items`、`count` |
| `data.log` | 打印日志 | 纯函数 | `message` |
| `data.readText` | 读取文本文件 | 只读 | `workspace`、`path` |
| `data.setVar` | 设置变量 | 纯函数 | `varName`、`value` |
| `data.table.appendRow` | 追加表格行 | 不可重复写 | `row` |
| `data.table.clear` | 清空数据表格 | 可重复写 | — |
| `data.table.deleteRow` | 删除表格行 | 不可重复写 | `row` |
| `data.table.exportCsv` | 导出表格 CSV | 可重复写 | — |
| `data.table.getCell` | 读取表格单元 | 只读 | `row`、`column`、`varName` |
| `data.table.setCell` | 写入表格单元 | 可重复写 | `row`、`column`、`value` |
| `data.writeJson` | 写入 JSON 文件 | 可重复写 | `workspace`、`path`、`data` |
| `data.writeText` | 写入文本文件 | 可重复写 | `workspace`、`path` |

## OCR / 验证码（3 条）

截图识别、滑块/点选类验证码。慢且依赖外部引擎，能不用就不用。

| 命令 | 中文名 | 副作用 | 必填参数 |
|---|---|---|---|
| `ocr.detect` | 识别验证码目标位置 | 纯函数 | — |
| `ocr.recognize` | 识别验证码 | 纯函数 | — |
| `ocr.slideMatch` | 识别滑块缺口位置 | 纯函数 | — |

## 工作流控制（1 条）

子流程、重试、等待、断言这类编排原语。

| 命令 | 中文名 | 副作用 | 必填参数 |
|---|---|---|---|
| `workflow.sleep` | 延时等待 | 纯函数 | `seconds` |
