# 影刀指令差距矩阵

> 面向维护者。目标：对齐影刀指令集，按使用频度排优先级，作为 `BACKLOG.md` Phase 6 及后续批次的输入。
> 口径：当前已实现 77 个 manifest（browser 30 / desktop 17 / desktop_win32 19 / data 11，其中 data.table 6 个）。
> 通道约束：浏览器执行通道仅扩展；桌面分 UIA（desktop）与 Win32（desktop_win32）双驱动；纯本地数据处理走 `python.worker`。
> Playwright 已移除，任何 browser.* 新增均须走背景页 `background.js` ↔ orchestrator 扩展协议。

---

## 图例

- 频度：P0 高（几乎必用）→ P1 中 → P2 低
- 状态：`✅现有` / `🚫无对应`（缺失）/ `➖不适用`
- 难度与依赖用于分批决策；风控高指依赖外部程序或浏览器端验证。

---

## 一、数据处理（data，重点补齐）

> `data` 当前仅 11 个。这是影刀覆盖最宽、本项目最薄弱的命名空间，也是 RPA 落地最高频的一类。

| 影刀指令（中文） | 建议 command id | 状态 | 频度 | 难度 | 依赖/通道 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 读取文本文件 | `data.readText` | 🚫 | P0 | 低 | worker | 读文件返回 string；需校验在允许目录内 |
| 追加写文本 | `data.appendText` | 🚫 | P0 | 低 | worker | 现有 `writeText` 是覆盖式，追加是常见诉求 |
| 文件是否存在 | `data.fileExists` | 🚫 | P0 | 低 | worker | 输出 boolean |
| 删除文件/目录 | `data.deletePath` | 🚫 | P0 | 低 | worker | 原子删除 + effect 证据 |
| 复制/移动文件 | `data.copyFile` / `data.moveFile` | 🚫 | P1 | 低 | worker | 目录内白名单校验 |
| 读取 CSV | `data.csv.read` | 🚫 | P1 | 中 | worker | 解析为行数组 {列}，复用 `rows_to_csv` 的反向 |
| 写 CSV（从行数组） | `data.csv.write` | 🚫 | P1 | 中 | worker | 与 data.table.exportCsv 互补（后者固定流程表） |
| 列出目录文件 | `data.listFiles` | 🚫 | P2 | 低 | worker | 输出文件名数组 |
| 判断路径类型 | `data.pathType` | 🚫 | P2 | 低 | worker | file/dir/missing |
| 字符串包含/替换 | `data.string.replace` | 🚫 | P1 | 低 | worker | contains 可用 Condition，替换缺失 |
| 正则提取 | `data.regex.extract` | 🚫 | P1 | 中 | worker | 支持 capture group |
| 日期当前时间/格式化 | `data.datetime.now` | 🚫 | P0 | 低 | worker | 输出 ISO 与自定义格式 |
| 日期加减 | `data.datetime.add` | 🚫 | P2 | 低 | worker | ± 天/时/分 |
| 数组长度/取值/追加 | `data.array.*` | 🚫 | P1 | 低 | worker | 长度、按索引取、追加 |
| JSON 解析/序列化 | `data.json.parse` / `data.json.stringify` | 🚫 | P1 | 中 | worker | 反序列化/序列化 |
| 随机数/取整 | `data.math.random` / `data.math.round` | 🚫 | P2 | 低 | worker | 纯函数 |

## 二、流程控制（工作流 AST 层，非 manifest）

| 影刀指令 | 现状 | 频度 | 备注 |
| --- | --- | --- | --- |
| 如果/否则 | ✅ `if` 节点 | P0 | AST 已支持 Condition（eq/ne/gt/gte/lt/lte/contains/truthy） |
| 遍历 | ✅ `forEach` 节点 | P0 | AST 已支持 |
| 异常处理 | ✅ `try` 节点 | P0 | AST 已支持，error_var 词法作用域 |
| 返回/提前结束 | ✅ `return` 节点 | P1 | AST 已支持 |
| 延时等待（流程级） | 🚫 | P0 | 目前仅 browser.waitFor；缺独立的 `sleep`/延时指令，建议补 `workflow.sleep`（P0） |

## 三、浏览器（browser，已较全，深度增强参考）

| 影刀指令 | 建议 command id | 状态 | 频度 | 难度 | 依赖/通道 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 打开网页 / 关闭 / 刷新 | `navigate`/`close`/`stopLoading` | ✅现有 | P0 | – | – | |
| 点击 / 输入 / 复选框 / 等待 | `click`/`input`/`check`/`waitFor`/`waitLoad` | ✅现有 | P0 | – | – | |
| 执行 JS / 截图 / 下拉 / 上传下载 等 | `executeScript`/`screenshot`/`select`/`upload`/`download` 等 | ✅现有 | P0~P2 | – | – | BACKLOG Phase 2–4 已落地 |
| 组合键（Ctrl+C 等） | `browser.hotkey` | 🚫 | P0 | 中 | 扩展协议 | desktop_win32 已有 `hotkey`，browser 缺；走扩展 `sendKeys` |
| 剪贴板读取/写入 | `browser.clipboard.*` | 🚫 | P2 | 中 | 扩展协议 | 或用系统剪贴板 |
| 页面断言（条件等待直到） | `browser.waitUntil` | 🚫 | P1 | 中 | 扩展协议 | 按 selector+条件轮询等待 |

## 四、桌面 / Win（desktop / desktop_win32，已较全）

| 影刀指令 | 现状 | 频度 | 备注 |
| --- | --- | --- | --- |
| 激活窗口 / 移动 / 缩放 / 状态 | `activateWindow`/`moveWindow`/`resizeWindow`/`setWindowState`… | ✅现有 | BACKLOG 已落地 |
| 点击 / 输入 / 选中文本 / 拖拽 | `click`/`input`/`getSelectedText`/`drag`… | ✅现有 | |
| 组合键 | `desktop_win32.hotkey` | ✅现有 | UIA 侧（desktop）缺，后续可补 `desktop.hotkey` |
| 获取窗口列表 / 窗口信息 | `getWindowList`/`getWindowTitle` | ✅现有 | |

## 五、辅助/高级（P2，观望）

| 影刀指令 | 建议 command id | 状态 | 频度 | 难度 | 依赖 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 网络请求 | `net.request` | 🚫 | P1 | 中 | python（urllib/requests） | get/post，返回 body |
| 剪贴板（系统级） | `sys.clipboard.get/set` | 🚫 | P2 | 中 | | 依赖系统/pywin32 |
| OCR 识别 | `vision.ocr` | 🚫 | P2 | 高 | 外部引擎 | 暂无合适纯 python 方案，观望 |

---

## 建议推进结论（供第一批决策）

**第一批（频度 P0 + 低难度，纯 worker、零外部依赖、可单测全覆盖）**：

1. `data.readText` — 读文件
2. `data.appendText` — 追加写
3. `data.fileExists` — 判断存在
4. `data.deletePath` — 删除路径
5. `workflow.sleep` — 流程级延时
6. `data.datetime.now` — 当前时间/格式化

**第二批（P0~P1，仍纯 worker）**：

7. `browser.hotkey` — 组合键（走扩展协议，需浏览器端验证）
8. `data.csv.read` — 读 CSV
9. `data.string.replace` / `data.regex.extract`
10. `data.array.*` 子集

**其余**（copyFile/move/listFiles/json/math/date.add/net.request 等）按其 P1~P2 频度后续排布。

> 说明：以上为按频度与落地成本的初始排序。请维护者批注优先级或替换第一批范围后，再落成可执行的 `BACKLOG.md Phase 6` 任务切片。