---
name: rpa-core-cli
display_name: RPACoreCLI
display_name_en: RPACoreCLI
description: rpa-core CLI 连接器的安装配件，只负责说明本机 CLI 装在哪、怎么查状态与升级、能力清单怎么现取；不生成也不运行工作流。当用户说「rpa-core 装好了吗」「检查 rpa-core 状态」「升级 rpa-core」「连接器连没连上」「看看有哪些原子能力」时使用；要编排或运行自动化流程请改用 workflow-studio 技能。触发词：rpa-core 状态、检查 rpa-core、升级 rpa-core、连接器状态、能力清单、catalog 命令。
description_zh: 连接器的必带配件：确认 rpa-core CLI 可用、查状态、看能力目录；工作流编排统一由 workflow-studio 技能负责。
description_en: Connector companion skill: verify the rpa-core CLI, check status and list atomic actions. Workflow orchestration lives in the workflow-studio skill.
examples_zh:
  - "rpa-core 装好了吗？帮我看下状态"
  - "把本机 rpa-core 的能力清单列出来"
examples_en:
  - "Is rpa-core installed? Check its status"
  - "List the atomic actions available on this machine"
version: 0.3.0
author: w
agent_created: true
---

# rpa-core CLI（连接器配件）

这份 SKILL.md 是 WorkBuddy「CLI + Skill」连接器的**必带配件**，不是第二套编排技能。
连接器负责把 `rpa-core` CLI 装到本机并让宿主看到连接状态；本文件只回答三件事：

1. **CLI 在不在**：`rpa-core status`，期望 `{"status":"ready", ...}`；非 ready 先解决环境。
   宿主的 `statusMatch` 也看这条输出，格式变了连接状态会跟着变。
2. **版本与能力是否同代**：`rpa-core catalog` 拿 digest 与本机真实命令清单。
   包内附的 commands-index / commands-reference / catalog.json 是**快照**，
   一律以 `catalog` 的现场输出为准；文档里有而本机没有的命令不要调用。
3. **怎么升级**：连接器 `cli.json` 的 `init` 是 `python -m pip install -U rpa-core-runtime`，
   重新执行即可；宿主会在重启时重新查 `status`。

## 什么时候不要用它

用户说「帮我做个网页自动化」「写个 workflow」「抓数据」「拾取这个按钮」
「元素定位失效了」「把这个流程跑一遍」时，**不要**在这里拼流程、也不要写
`workflow.json`：那些是 `workflow-studio` 技能的职责。WorkBuddy 里请装
技能包 `workflow-studio`（或让 Agent 直接调用它），本配件不抢这些触发词，
也不复制它的生成 / 校验 / 人在环 / 运行步骤，免得两份文档各自漂移。

之所以还留着这一份，是开放平台规定连接器包里必须有 `skills/{name}/SKILL.md`；
它是接线说明，不是产品主体。

## 边界

- 不声明任何常驻服务、不等用户操作、不打开 GUI 窗口：连接器的 `status` 必须
  10 秒内无副作用返回，会话服务与编辑窗口由 `workflow-studio` 按需拉起。
- 不写死命令数量、不抄能力表：能力的事实源是 `commands/` 与
  `rpa-core catalog`，包内文档只是它的可读快照。
- 不装第二个 CLI：本连接器与技能包共用同一个 `rpa-core`，重复安装只会互相覆盖。
