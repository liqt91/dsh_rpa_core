---
name: rpa-automation
description: 用 rpa-core CLI 编写并运行确定性 RPA 工作流（桌面与浏览器自动化）。当用户要求"自动打开某网站/应用、抓数据、填表单、重复操作桌面软件"时使用。
description_zh: 通过 rpa-core CLI 编写、校验并运行桌面与浏览器 RPA 自动化流程。
description_en: Author, validate, and run deterministic desktop and browser RPA workflows with the rpa-core CLI.
version: 1.0.0
author: rpa-core
---

# RPA Core 自动化（rpa-core CLI）

通过 rpa-core CLI 构建并运行类型安全的 RPA 工作流。核心流程：**编写 workflow.json → 编译校验（validate）→ 运行（run）→ 读回结果（result.json）**。

## 前置条件

- CLI 已安装（WorkBuddy 连接器自动安装）：`rpa-core status` 应输出 `{"status": "ready"}`。
- 浏览器自动化需要 Playwright Chromium：`python -m playwright install chromium`（首次）。
- 桌面自动化仅 Windows（UIA / Win32 双后端）。

## 核心命令

| 命令 | 用途 |
|---|---|
| `rpa-core validate <workflow.json>` | 编译校验（dry-run，不执行；返回 `{"valid": true}` 或错误列表） |
| `rpa-core run <workflow.json>` | 运行工作流，输出 JSON 结果到 stdout |
| `rpa-core resume <workflow.json> --run-id <id>` | 恢复中断的运行（失败/暂停后续跑） |
| `rpa-core catalog` | 列出全部可用命令（26 条）的 id/版本/输入输出契约 |
| `rpa-core capture browser|desktop` | 交互捕获元素（生成可回验的 selector/locator） |
| `rpa-core elements list\|show\|verify --flow <名>` | 管理某流程的元素资产 |
| `rpa-core devserver [--port 8765]` | 启动可视化编辑器（人类编辑工作流用） |

## 工作流编写

workflow.json 是 JSON AST。结构见 [references/workflow-format.md](references/workflow-format.md)；全部命令的输入输出契约见 [references/commands-reference.md](references/commands-reference.md)；错误码与恢复见 [references/error-codes.md](references/error-codes.md)。

最小示例（data 类，无需浏览器/桌面）：

```json
{
  "schema_version": "1.0",
  "id": "hello",
  "name": "hello",
  "inputs": { "workspace": "." },
  "root": {
    "type": "sequence",
    "id": "root",
    "children": [
      { "type": "action", "id": "fmt", "command": "data.format",
        "with": { "template": "hello {name}", "values": {"name": "world"} } },
      { "type": "action", "id": "save", "command": "data.writeText",
        "with": { "workspace": "${inputs.workspace}", "path": "out.txt",
                  "text": "${steps.fmt.outputs.text}" } },
      { "type": "return", "id": "ret", "value": "${steps.fmt.outputs.text}" }
    ]
  }
}
```

运行：`rpa-core run hello.json` → stdout 打印 `RunResult`（`status: succeeded` 即成功，`return_value` 为返回值）。

## 浏览器流程骨架

```json
{ "type": "action", "id": "launch", "command": "browser.launch", "with": { "headless": true } }
{ "type": "action", "id": "nav", "command": "browser.navigate",
  "with": { "sessionId": "${steps.launch.outputs.sessionId}", "url": "https://example.com" } }
{ "type": "action", "id": "read", "command": "browser.getText",
  "with": { "sessionId": "${steps.launch.outputs.sessionId}", "selector": "h1" } }
{ "type": "action", "id": "close", "command": "browser.close",
  "with": { "sessionId": "${steps.launch.outputs.sessionId}" } }
```

**session 规则**：`browser.launch` 返回 `sessionId`，后续每个 browser.* 命令都必须带它；结束时 `browser.close`。登录态页面用 `transport: "bsk"`（复用用户真实浏览器）。

## 错误恢复

- 运行失败先读 stdout JSON 的 `error.code`；`ELEMENT_NOT_FOUND` 说明 selector 失效，用 `rpa-core capture` 重捕获。
- `indeterminate`（失败且外部结果未知）必须人工核查后才能 resume：`rpa-core resume wf.json --run-id <id> --allow-indeterminate`。
- 每次运行证据落 `run_artifacts/<run_id>/`（`result.json` / `events.jsonl`）。
