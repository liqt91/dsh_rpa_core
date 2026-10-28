---
name: rpa-automation
display_name: RPA 自动化
display_name_en: RPA Automation
description: 用 rpa-core CLI 把 89 条现成原子能力编排成确定性工作流并运行（浏览器 / Windows 桌面 / 数据 / OCR）。当用户说「自动打开某网站或软件、抓数据、填表单、批量点击、重复操作桌面应用、每天跑一次这个流程、把页面上的东西存下来」时使用。触发词：自动化、跑流程、抓数据、爬取、填表、批量操作、定时跑、模拟点击、桌面自动化、浏览器自动化、RPA、workflow、capture 元素、selector 失效。
description_zh: 不重新实现任何操作，只把已有的 89 条原子能力编排成 workflow.json，校验后运行并交回结果。
description_en: Orchestrate the 89 built-in atomic actions into a validated, deterministic workflow and run it via the rpa-core CLI.
examples_zh:
  - "打开必应搜 RPA，把前 10 条标题存成 JSON"
  - "每天把公司内网报表页的表格抓下来，追加到 CSV"
  - "在记事本里输入一段固定文本并保存"
  - "这个流程昨天还能跑，今天报 ELEMENT_NOT_FOUND，帮我修好"
examples_en:
  - "Search Bing for RPA and save the top 10 titles as JSON"
  - "Grab the table from our intranet report page every day and append it to a CSV"
  - "Type a fixed text into Notepad and save it"
  - "This flow worked yesterday but now fails with ELEMENT_NOT_FOUND — fix it"
version: 0.2.0
author: w
allowed-tools: Bash, Read, Write
---

# RPA 自动化：只编排，不重写

**这条技能的存在意义**：项目里已经有 89 条原子能力（点击、输入、取值、截图、UIA/Win32 控件、
文件、OCR……）和一个确定性运行时。你的工作**不是**再写一遍点击逻辑，而是：

> 从能力目录里挑命令 → 拼成 `workflow.json` → `validate` → `run` → 读 `result.json` 交结果。

**绝对不要**用 Python 脚本 + pyautogui / selenium / requests 重新实现这些动作。那样等于绕开
运行时：没有终态、没有 checkpoint、没有证据文件，失败也无从 resume。

## 第 0 步：对齐版本（每次会话只做一次）

```bash
rpa-core status          # 期望 {"status": "ready", ...}；非 ready 先解决环境
rpa-core catalog         # 拿到 digest 与**本机真实**命令清单
```

把 `catalog` 的 `digest` 和 `references/catalog.json` 里的 `digest` 比一下：

- **一致** → 下面所有文档都可信，直接干活。
- **不一致** → 本机 CLI 与本技能文档不同代。**以 `rpa-core catalog` 的实际输出为准**：
  文档里写了但本机没有的命令，不要调用，直接告诉用户「装到的版本比文档旧，缺 X，需要升级」。
  绝不允许凭文档猜一条不存在的命令去跑。

命令数量对不上同理——**不要**用文档里的数字去反驳机器的输出。

## 能力地图（先看这个，再看细则）

| 要做的事 | 用哪组 | 入口 |
|---|---|---|
| 网页点击/输入/取值/截图/Cookie/下载/多标签 | `browser.*`（32 条） | `references/commands-index.md` |
| 现代桌面应用（记事本、Office、浏览器窗口本身） | `desktop.*`（UIA，17 条） | 同上 |
| 标准 Win32 控件、UIA 抓不到的窗口 | `desktop.win32.*`（19 条） | 同上 |
| 不碰界面的纯数据：读文件/写文件/格式化/时间 | `data.*`（17 条） | 同上 |
| 截图识字、滑块/点选验证码 | `ocr.*`（3 条） | 同上 |
| 等待、子流程、重试 | `workflow.*`（1 条） | 同上 |

- **一行一条的目录**：`references/commands-index.md`（89 条，带副作用标记）——先扫它选命令。
- **参数怎么传**：`references/commands-reference.md`（每条：参数/类型/必填/默认/取值/输出/失败码）。
- **JSON AST 长什么样**：`references/workflow-format.md`。
- **报错怎么恢复**：`references/error-codes.md`。

选组的铁律：**能走浏览器就别走桌面**（浏览器会话可控、可无头、可复现）；桌面里**能用 UIA 就别用
Win32 坐标**（语义定位比坐标耐改版）。

## 参数怎么传（最容易错的三件事）

1. **会话必须显式串起来**：`browser.navigate` / `desktop.attachWindow` 返回 `sessionId`，
   之后每条命令都写 `"sessionId": "${steps.<节点id>.outputs.sessionId}"`。session 不跨进程存活，
   换一次 run 就要重新开。
2. **元素优先用元素引用，不要手抄 selector**：
   ```json
   { "type": "action", "id": "clickLogin", "command": "browser.click",
     "elementRefs": { "selector": "登录按钮" },
     "with": {} }
   ```
   `elementRefs` 是「参数键 → `workflows/<流程>/elements/` 里的元素名」。被引用的参数**不要**
   再写进 `with`（两者同时出现会被模型直接拒绝）；元素取不到是 `ELEMENT_REF_MISSING` 硬失败，
   不会静默回落到旧快照。没有元素库时才写裸 `selector`，并且要在回复里说明「这个定位是手写的」。
3. **引用语法只有三种**：`${inputs.<key>}`、`${steps.<id>.outputs.<key>}`、`${loop.item}`。
   字符串拼接写不了复杂表达式——需要加工就用 `data.format`，或先把值落到 inputs。

## 三条工作流，按用户说的话选

### A. 「把这个网页上的东西抓下来」

```json
{ "schema_version": "1.0", "id": "grab", "name": "grab",
  "inputs": { "workspace": "." },
  "root": { "type": "sequence", "id": "root", "children": [
    { "type": "action", "id": "open", "command": "browser.navigate",
      "with": { "url": "https://www.bing.com", "headless": true } },
    { "type": "action", "id": "read", "command": "browser.getText",
      "with": { "sessionId": "${steps.open.outputs.sessionId}", "selector": "h1" } },
    { "type": "action", "id": "save", "command": "data.writeText",
      "with": { "workspace": "${inputs.workspace}", "path": "out.txt",
                "text": "${steps.read.outputs.value}" } },
    { "type": "action", "id": "close", "command": "browser.close",
      "with": { "sessionId": "${steps.open.outputs.sessionId}" } }
  ]}}
```

要「多条」就把 `browser.queryAll` 的输出塞进 `forEach`；要登录态就用 `browser.navigate` 的
`transport: "bsk"` 复用用户真实浏览器（先读这条命令的参数说明，别默认开）。

### B. 「帮我操作这个桌面软件」

`desktop.getWindowList` 找窗口 → `desktop.attachWindow` 拿 `sessionId` → `desktop.findElement`
拿控件 → `desktop.click` / `desktop.input` → `desktop.closeSession`。
**先探测再操作**：不确定控件长什么样时，先跑一条只读命令（`getWindowList` / `findElement`）
把结果给用户看，再往下写。

### C. 「元素定位失效了」

`ELEMENT_NOT_FOUND` **不要**靠改 timeout 蒙过去。正确顺序：

1. `rpa-core capture browser --flow <流程>`（或 `desktop`）让用户重新点一次目标；
2. 覆盖元素库里那一条（`--save-as <同名> --flow <流程>`）；
3. `rpa-core elements verify --flow <流程>` 确认能命中；
4. 再 `run`。

## 安全与确认（不可省）

这些命令会改外部世界，**执行前必须把「对谁、做什么、能不能撤回」说清楚并拿到明确同意**：

| 类别 | 例子 | 规矩 |
|---|---|---|
| 不可重复写 | `browser.click`、`desktop.input`、`browser.upload` | 同一步骤不要重跑；失败先判断是否已经生效 |
| 删除/覆盖 | `data.deletePath`、`browser.cookieRemove`、覆盖已有文件 | 必须先说清目标路径，再执行 |
| 对外发送/提交 | 表单提交、下载到共享目录 | 先确认目标与内容，避免重复提交 |
| 花时间/花钱 | OCR、长等待 | 说明大概耗时，别让用户干等 |

判据不是「我觉得安全」，而是**能力目录里的「副作用」列**：`只读` / `纯函数` 可以直接跑；
`可重复写` 可以重试；`不可重复写` 重跑前必须确认。

## 失败怎么处理

`run` 的 stdout 是 `RunResult`。**先看 `status` 和 `error.code`，再看 `error.message`**：

- `failed` + 明确错误码 → 修 workflow / 重新捕获元素后 `resume`（已完成步骤不会重跑）。
- `indeterminate` → 外部结果未知。**必须**让用户去目标系统核实，确认后才
  `resume --run-id <id> --allow-indeterminate`；不要自己加这个开关。
- `paused` / `cancelled` → 可以直接 resume。
- 每次都把 `run_artifacts/<run_id>/result.json` 与 `events.jsonl` 的路径告诉用户，那是证据。

## 不要做的事（写死）

1. **不重写原子能力**：没有 selenium、没有 pyautogui、没有「我自己写个 requests 抓一下」。
2. **不在 orchestrator 里跑用户 Python**：需要自定义逻辑就走 `python.worker` 命令（本机 catalog 有才用）。
3. **不猜 selector**：拿不到就 `capture`，或者退化成「按文案定位」并明确说明风险。
4. **不伪造成功**：`result.json` 没落盘就不算成功；没跑就说没跑。
5. **不乱放产物**：写到 workflow 的 `inputs.workspace` 下，路径先问清楚。
