# 指令清单对比（rpa_core vs 隔壁 rpa_script）

> 生成日期：2026-09-07。对比参考隔壁仿影刀项目的指令库（`commands-new.json`）。
> 我们的指令含输入/输出参数说明（来自 manifest `input_schema`/`output_schema`）。

## 一、我们的指令（26 条）

| 指令 | 中文名 | kind | effect | 输入参数（* = 必填） | 输出 |
|---|---|---|---|---|---|
| `browser.click` | 点击元素 | action | unsafe-write | sessionId: string*, selector: string*, timeoutMs: integer | matchedCount, sessionId |
| `browser.close` | 关闭浏览器 | lifecycle | session | sessionId: string* | - |
| `browser.getText` | 读取元素文本 | query | read | sessionId: string*, selector: string*, timeoutMs: integer | value |
| `browser.input` | 输入文本 | action | unsafe-write | sessionId: string*, selector: string*, text: string*, timeoutMs: integer | matchedCount, sessionId |
| `browser.launch` | 启动浏览器 | lifecycle | session | headless: boolean, userAgent: string, userDataDir: string, transport: string, browserInstanceId: string, keepOpen: boolean | sessionId |
| `browser.navigate` | 打开网页 | action | unsafe-write | sessionId: string*, url: string*, timeoutMs: integer | url, sessionId |
| `browser.queryAll` | 抓取列表文本 | query | read | sessionId: string*, selector: string* | items, count |
| `browser.waitFor` | 等待元素出现 | query | read | sessionId: string*, selector: string*, timeoutMs: integer | matchedCount |
| `data.format` | 格式化文本 | transform | pure | template: string*, values: object* | text |
| `data.limit` | 截取前 N 条 | transform | pure | items: array<?>*, count: integer* | items, count |
| `data.writeJson` | 写入 JSON 文件 | action | idempotent-write | workspace: string*, path: string*, data: any* | path |
| `data.writeText` | 写入文本文件 | action | idempotent-write | workspace: string*, path: string*, text: string, lines: array<string> | path |
| `desktop.attachWindow` | 附着窗口（UIA） | lifecycle | session | title: string*, processId: integer, timeoutMs: integer | sessionId, processId, workWindowId |
| `desktop.click` | 点击控件（UIA） | action | unsafe-write | sessionId: string*, elementId: string*, timeoutMs: integer | - |
| `desktop.closeSession` | 关闭会话（UIA） | lifecycle | session | sessionId: string* | - |
| `desktop.findElement` | 查找控件（UIA） | query | read | sessionId: string*, locator: object*, timeoutMs: integer | elementId, matchedCount |
| `desktop.getText` | 读取控件文本（UIA） | query | read | sessionId: string*, elementId: string*, timeoutMs: integer | value |
| `desktop.input` | 控件输入（UIA） | action | unsafe-write | sessionId: string*, elementId: string*, text: string*, timeoutMs: integer | - |
| `desktop.win32.attachWindow` | 附着窗口（Win32） | lifecycle | session | title: string, className: string, handle: integer, processId: integer, timeoutMs: integer | sessionId, processId, workWindowId |
| `desktop.win32.click` | 点击控件（Win32） | action | unsafe-write | sessionId: string*, elementId: string*, timeoutMs: integer | - |
| `desktop.win32.closeSession` | 关闭会话（Win32） | lifecycle | session | sessionId: string* | - |
| `desktop.win32.findElement` | 查找控件（Win32） | query | read | sessionId: string*, locator: object*, timeoutMs: integer | elementId, matchedCount |
| `desktop.win32.getText` | 读取控件文本（Win32） | query | read | sessionId: string*, elementId: string*, timeoutMs: integer | value |
| `desktop.win32.hotkey` | 组合按键（Win32） | action | unsafe-write | sessionId: string*, keys: string*, timeoutMs: integer | - |
| `desktop.win32.input` | 控件输入（Win32） | action | unsafe-write | sessionId: string*, elementId: string*, text: string*, timeoutMs: integer | - |
| `desktop.win32.menuSelect` | 菜单选择（Win32） | action | unsafe-write | sessionId: string*, menuPath: array<string>*, timeoutMs: integer | - |

## 二、隔壁指令（按类别）

类别：`浏览器操作、浏览器元素操作、变量及日志、循环、条件判断、异常处理、桌面操作、vision`

### 循环
| 中文名 | cmd | 参数 |
|---|---|---|
| 遍历元素列表 | `forEachElement` | 目标元素列表」element-list*, 当前项变量」string, 索引变量」string, 匹配范围」select |
| 遍历列表 | `forList` | 列表变量」string*, 当前项变量」string, 索引变量」string |
| 循环次数 | `forRange` | 起始值」number, 结束值」number*, 步长」number, 当前值变量」string |
| 条件循环 | `whileCondition` | 条件类型」select*, 元素」element, URL 包含」string, 变量名」string, 预期值」string, 表达式」string, 最大迭代次数」number |
| 跳出循环 | `break` | - |
| 继续下次循环 | `continue` | - |
| 结束循环 | `endLoop` | - |

### 异常处理
| 中文名 | cmd | 参数 |
|---|---|---|
| 尝试执行 | `try` | - |
| 捕获异常 | `catch` | 错误信息保存到」str-var |
| 结束异常处理 | `endTry` | - |

### 桌面操作
| 中文名 | cmd | 参数 |
|---|---|---|
| 打开软件 | `openAppWin32` | 选择软件 (Win32)」select, 自定义程序路径 (Win32)」string, 启动参数 (Win32)」string, 窗口句柄存入变量 (Win32)」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 等待窗口出现 | `waitWindowWin32` | 窗口标题」string*, 窗口类名（可选）」string, 超时(秒)」number, 结果存入变量(HWND)」str-var, 执行失败时」select, 重试次数」number, 步骤说明」text |
| 查找窗口 | `findWindowAuto` | 窗口标题」string*, 搜索模式」select, 实现方式」select, 查找后激活窗口」boolean, 结果存入变量」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 查找窗口 (Win32) | `findWindowWin32` | 搜索模式」select, 窗口标题/类名 (Win32)」string*, 查找后激活窗口 (Win32)」boolean, 结果存入变量(HWND) (Win32)」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 查找窗口 (UIA) | `findWindowUia` | 窗口标题」string*, 搜索模式」select, 结果存入变量(UIA)」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 查找父控件 | `findParentWin32` | 子窗口 (HWND变量) (Win32)」str-var*, 结果存入变量 (Win32)」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 查找兄弟控件 | `findSiblingWin32` | 参考控件 (HWND变量) (Win32)」str-var*, 查找方向 (Win32)」select, 目标类名 (Win32)」select, 跳过几个 (Win32)」number, 结果存入变量 (Win32)」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 查找子控件 | `findChildWin32` | 父窗口 (HWND变量) (Win32)」str-var*, 控件类名 (Win32)」select, 标题筛选 (Win32)」string, 匹配第几个 (Win32)」number, 结果存入变量 (Win32)」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 从元素库取控件 | `pickElementAuto` | 桌面元素」string*, 层级序号」number, 结果存入变量」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 从元素库取控件 (Win32) | `pickFromPathWin32` | 桌面元素 (Win32)」string*, 层级序号 (Win32)」number, 结果存入变量(HWND) (Win32)」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 从元素库取控件 (UIA) | `pickElementUia` | 桌面元素」string*, 层级序号」number, 结果存入变量(UIA)」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 点击控件 | `clickControlAuto` | 目标控件」str-var*, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 点击控件 (Win32) | `clickControlWin32` | 目标控件句柄 (HWND变量) (Win32)」str-var*, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 点击控件 (UIA) | `clickElementUia` | 目标控件 (UIA变量)」str-var*, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 输入文字 | `inputControlAuto` | 目标控件」str-var*, 输入内容」string*, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 输入文字 (Win32) | `inputControlWin32` | 目标控件句柄 (HWND变量) (Win32)」str-var*, 输入内容 (Win32)」string*, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 输入文字 (UIA) | `inputElementUia` | 目标控件 (UIA变量)」str-var*, 输入内容」string*, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 发送按键 | `sendKeyWin32` | 按键 (Win32)」select*, 自定义按键 (Win32)」string, 修饰键 (Win32)」string, 重复次数 (Win32)」number, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 鼠标点击 | `mouseClickWin32` | X 坐标」number*, Y 坐标」number*, 相对窗口 (HWND变量，可选)」str-var, 点击类型」select, 结果存入变量(坐标)」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 点击菜单 | `clickMenuWin32` | 父窗口 (HWND变量) (Win32)」str-var*, 菜单路径 (Win32)」string*, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 等待 | `waitWin32` | 等待模式 (Win32)」select, 等待时间(秒) (Win32)」number, 最少(秒) (Win32)」number, 最多(秒) (Win32)」number, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 窗口截图 | `screenshotWindowWin32` | 窗口 (HWND变量)」str-var*, 保存路径」string*, 结果存入变量(路径)」str-var, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 关闭窗口 | `closeWindowWin32` | 窗口 (HWND变量) (Win32)」str-var*, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |

### 浏览器元素操作
| 中文名 | cmd | 参数 |
|---|---|---|
| 等待元素出现 | `waitForElement` | 目标元素」element, 超时时间（秒）」number, 可见性」select, 执行失败时」select, 重试次数」number, 步骤说明」text |
| 点击元素 | `clickElement` | 元素」element*, 匹配范围」select, 锚点元素」string, 元素可见性」select, 真实鼠标点击」boolean, 点击方式」select, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 输入文本 | `inputElement` | 元素」element*, 输入内容」string*, 模拟键盘输入」boolean, 输入后按回车」boolean, 匹配范围」select, 锚点元素」string, 元素可见性」select, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 获取文本 | `getText` | 目标元素」element, 匹配范围」select, 保存到变量」string, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 获取超链接 | `getElementLink` | 目标元素」element*, 匹配范围」select, 保存到变量」string, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 鼠标悬停 | `hover` | 目标元素」element, 匹配范围」select, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 滚动到元素 | `scrollIntoView` | 目标元素」element, 匹配范围」select, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 按键 | `pressKey` | 按键」select*, 修饰键」string, 操作系统真实按键」boolean, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 截图 | `takeScreenshot` | 目标元素(可选)」element, 保存到变量(base64)」string, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |

### vision
| 中文名 | cmd | 参数 |
|---|---|---|
| 图像查找 | `findImage` | 参考图元素」element*, 匹配范围」select, 相似度阈值」number, 超时(秒)」number, 执行失败时」select, 重试次数」number, 步骤说明」text |
| 图像点击 | `clickImage` | 参考图元素」element*, 匹配范围」select, 相似度阈值」number, 超时(秒)」number, 执行失败时」select, 重试次数」number, 步骤说明」text |

### 浏览器操作
| 中文名 | cmd | 参数 |
|---|---|---|
| 打开浏览器 | `launchBrowser` | 浏览器」select, 启动后打开网址」string, 窗口前置」boolean, 窗口状态」select, 保存窗口对象到」string, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 页面跳转 | `navigate` | 浏览器窗口」string, 目标网址」string, 等待页面加载完成」boolean, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 新建标签页 | `newTab` | 浏览器窗口」string, 打开网址」string, 激活新标签页」boolean, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 切换标签页 | `switchTab` | 浏览器窗口」string, URL 匹配」string, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 关闭标签页 | `closeTab` | 浏览器窗口」string, 标签页序号(可选)」number, URL 匹配」string, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 关闭浏览器 | `closeBrowser` | 窗口变量」string, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |

### 条件判断
| 中文名 | cmd | 参数 |
|---|---|---|
| 如果元素可见 | `ifElementVisible` | 元素」element*, 判断条件」select, 匹配范围」select |
| 判断元素存在 | `ifElementExists` | 元素」element*, 判断条件」select, 匹配范围」select |
| 如果变量相等 | `ifVarEquals` | 变量名」str-var*, 比较值」string* |
| 如果文本相等 | `ifTextEquals` | 文本A」string*, 文本B」string* |
| 如果列表包含 | `ifListContains` | 列表变量」str-var*, 查找值」string* |
| 如果字典包含键 | `ifDictContains` | 字典变量」str-var*, 键名」string* |
| 如果文本包含 | `ifTextContains` | 源文本」string*, 包含文本」string* |
| 判断URL包含 | `ifUrlContains` | URL 包含」string* |
| 如果变量包含 | `ifVarContains` | 变量名」str-var*, 包含文本」string* |
| 结束条件 | `endIf` | - |

### 变量及日志
| 中文名 | cmd | 参数 |
|---|---|---|
| 设置变量 | `setVar` | 变量名」string*, 值」string, 值类型」select, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |
| 输出日志 | `log` | 日志内容」text, 日志级别」select, 执行失败时」select, 重试次数」number, 超时(秒)」number, 步骤说明」text |

## 三、对比与推荐新增

### 3.1 概览

- 我们：**26** 条（browser/data/desktop 三线）。
- 隔壁：**62** 条，覆盖 浏览器操作/元素操作/条件判断/循环/异常处理/变量与数据/输出与日志/桌面操作 等类别。
- 我们明显少的：**条件判断、循环、异常处理**（仅 if/forEach/try 三个控制流容器），以及浏览器**标签页管理、截图、读取页面信息**、桌面**截图/系统信息/等待** 等。

### 3.2 推荐新增（适合 rpa_core 零依赖取向的能力）

| 建议 | 类别 | 理由 | 复杂度 |
|---|---|---|---|
| `browser.queryText` / 读取多元素 | 元素操作 | 常见抓取，queryAll 已有，可加 getText 数组化 | 低 |
| `browser.takeScreenshot`（截图存盘） | 页面 | RPA 验收/留证刚需，Playwright 原生支持 | 中 |
| `browser.newTab/switchTab/closeTab`（标签页管理） | 页面 | 多标签页真实场景，隔壁已有 navigate/newTab/switchTab | 中 |
| `browser.getPageInfo`（URL/标题/元信息） | 页面 | 调试与断言用 | 低 |
| `browser.hover`（悬停元素） | 元素操作 | 菜单/hover 场景，Playwright hover 原生 | 低 |
| `data.setVar`（变量赋值） | 变量与数据 | 流程传参/中间结果，RPA 核心，隔壁已有 setVar | 低 |
| `data.log`（输出日志） | 输出与日志 | 过程可视化，隔壁已有 log | 低 |
| `data.getCurrentDateTime`（日期时间） | 数据 | 常用，标准库 | 低 |
| 控制流：`while`、`break`、`continue` | 条件循环 | 循环提前退出/条件循环，隔壁已有 whileCondition/break/continue | 中 |
| `desktop.takeScreenshot`（桌面截图存盘） | 桌面 | 桌面验收留证；需零依赖截图实现（深挖 stdlib/GDI 成本高） | 高 |
| `desktop.getMousePos` / `sendKey` / 系统信息 | 桌面 | 常见桌面操控，Win32 通道可做 | 中 |
| `excelRead` / `writeJsonFile`（文件/表格） | 数据 | 业务场景，注意需第三方库时归为「背靠 stdlib 或明确依赖」 | 高 |

### 3.3 建议优先级

1. **先补控制流与变量**：`data.setVar`/`data.log`/`while`/`break`/`continue`——是流程表达能力的地基，零依赖、成本低。
2. **再补浏览器实用**：`takeScreenshot`/`newTab|switchTab|closeTab`/`hover`/`getPageInfo`。
3. **桌面与业务**：按真实用例逐条立项（注意第三方库边界）。