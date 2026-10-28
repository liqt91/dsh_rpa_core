---
name: workflow-studio
display_name: WorkBuddyStudio
display_name_en: WorkBuddyStudio
description: 用 WorkBuddy Studio 的轻量编辑窗口打开文件、让用户在窗口里改并确认，或在浏览器页面上让用户点选元素拿回稳定选择器。当用户说「打开编辑器」「在窗口里改这个文件」「让我看看这份 JSON」「你改完我确认一下」「帮我把这个文件打开我手动调」「拾取元素」「选一下页面上的按钮/输入框」「要这个元素的 selector」「登录框定位不到」时使用。触发词：打开编辑器、编辑窗口、Studio、让我确认、人工确认、审阅文件、改完保存、拾取元素、选元素、元素定位、selector、选择器、抓元素、页面上点一下。
description_zh: 让 AI 与人共用同一个文件与同一个页面：文件用轻量编辑窗口改、页面元素用浏览器插件点选，结果都回到对话里。
description_en: A human-in-the-loop bridge for WorkBuddy. Opens files in a lightweight PPX editor window and captures page elements via a browser extension, returning structured results to the agent.
examples_zh:
  - "把这个 workflow.json 打开，让我自己改一下参数"
  - "我改完了，你看下保存后的内容对不对"
  - "帮我在登录页上拾取用户名输入框，要能定位到的那种"
examples_en:
  - "Open workflow.json in the editor so I can tweak the parameters myself"
  - "I'm done editing — check what I saved"
  - "Pick the username field on the login page and give me a reliable selector"
version: 1.0.0
author: w
---

# WorkBuddy Studio：人机协作三件套

这个技能解决一件事：**AI 不该替人做「只有人能做的判断」**。

改一个参数、确认一版配置、指出页面上到底是哪个按钮 —— 这些事让 AI 猜，猜错了没人发现；
让人做，人又要自己找文件、自己开编辑器、自己把结果贴回来。本技能把这一来一回压成两条命令。

三件套各管一摊，缺一件就退化成普通工具：

| 组件 | 作用 | 什么时候用 |
|---|---|---|
| 轻量编辑窗口（PPX） | 人改文件、确认改动，保存后自动把 sha 与校验结果交回 Agent | 需要人动手改，或需要人「点头」 |
| 本技能 + 会话服务 | Agent 侧的命令行入口，负责排队、等待、取结果 | 每次调用 |
| 浏览器插件（MV3） | 人在页面上点一个元素，插件算出稳定选择器 | 需要页面元素定位 |

## 一、开工前先自检

```bash
python scripts/studio.py status --pretty
```

- 输出里有 `会话 1.0.0 · 端口 7788` → 服务在跑，继续。
- 报 `NO_SESSION` / `SESSION_DOWN` → 先起服务（下面第二步），**不要**跳过去硬干活。
- `编辑窗口 离线` → 窗口没开。告诉用户「编辑器没开，我开一下」再继续；不要假装文件已经打开了。

自检不过时的兜底：直接读文件、把改动写清楚，让用户自己贴回去。**永远不要伪造「用户已确认」。**

## 二、起会话服务（一次即可，常驻）

```bash
python scripts/studio.py serve --port 7788
```

- 只监听 `127.0.0.1`，不对外网开放；端口被占会自动顺延（实际端口写在返回值里）。
- 想限制可打开的文件范围：加 `--root <目录>`，越界的路径会被 `BAD_PATH` 拒绝。
- 后台跑的时候把 stdout 丢掉即可，`studio.py status` 随时能问到状态。

## 三、工作流 A：让用户改文件并确认

```bash
# 1. 把文件递进窗口（立刻返回；--wait 会一直等到用户保存/关闭）
python scripts/studio.py open "D:/work/workflows/report.json" --reason "请把 pageSize 调到 50" --wait 120
```

- 退出码 `0` + `events` 非空 ⇒ 用户**保存了**（或关了窗口，看 `kind`）。
- 退出码 `3` ⇒ 超时，用户没动。**不要**自己改文件再说「已按你的要求改好」。
- 退出码 `2` ⇒ 服务或路径有问题，把 `error` 原文转述给用户。

```bash
# 2. 只等保存事件（不重新打开文件）
python scripts/studio.py wait --kind save --timeout 120
```

返回的事件里带 `sha` / `bytes` / `lines` / `validation`：

- `validation.ok = true` ⇒ 语法通过；`kind = workflow` 说明还过了工作流模型校验。
- `validation.ok = false` ⇒ 把 `message` 与 `line` 原样告诉用户，并建议在窗口里改（窗口会标红那一行）。

拿到 `sha` 之后**必须重新读文件**再谈内容，不要凭记忆复述用户改了什么。

## 四、工作流 B：拾取页面元素

```bash
# 1. 先进入等待（Agent 侧阻塞 ≤60s）
python scripts/studio.py capture --timeout 60
# 2. 同时告诉用户：打开目标页面 → 点浏览器工具栏的 Studio 图标 → 开始拾取 → 点目标元素
```

返回：

```json
{"ok": true, "capture": {"selector": "#username", "strategy": "id", "matches": 1,
 "tag": "input", "text": "", "attributes": {"name": "user"}, "rect": {...},
 "alternatives": [{"selector": "input[name=\"user\"]", "matches": 1}]}}
```

判据（按顺序）：

1. `matches == 1` 且 `strategy` 是 `id` / `attr:*` ⇒ 可以直接用。
2. `strategy == "path"` ⇒ 选择器能用但脆，**主动说明**「这个定位依赖层级，页面改版会失效」，并建议用户给元素加 `data-testid`。
3. `shadow == true` ⇒ 选择器穿不过 Shadow DOM，如实告诉用户需要框架侧支持，别硬编一个假的。
4. 超时（退出码 `3`）⇒ 问用户是没点、还是插件没连上；给 `studio.py doctor` 让他们自查。

## 五、工作流 C：会话离线时的降级

任何一步拿不到会话，都不要中断任务：

- 改文件 → 直接把 diff 贴给用户，请他确认后再写盘。
- 要选择器 → 请用户从浏览器 DevTools 里复制，或者退化为「按文案定位」（并说明风险）。

## 六、错误码对照

| code | 含义 | 该怎么做 |
|---|---|---|
| `NO_SESSION` | 服务没起 | 先 `serve`，再重试一次 |
| `SESSION_DOWN` | 服务挂了/端口变了 | `status` 确认，必要时重起 |
| `BAD_PATH` | 路径不存在或越界 | 检查 `--root` 与路径拼写，不要猜别的路径 |
| `EDITOR_OFFLINE` | 窗口没开 | 告知用户并等待，不要写入后声称已确认 |
| `TIMEOUT` | 用户没在时限内操作 | 询问是否继续等，不要自行代劳 |

## 七、边界（写死，不要临场发挥）

- **不改用户没让改的文件**：`open` 只读，写盘只发生在用户按 Ctrl+S（或在窗口里点保存）。
- **不伪造事件**：`save` 事件只能由编辑窗口发出；Agent 不许自己 POST 一条来「推进流程」。
- **不采集页面数据**：插件只上报被点中的那一个元素的属性/文本，不上报页面内容、不发网络请求。
- **不猜选择器**：拿不到就用工作流 C，不要凭截图编一个 `#submit`。

## 八、验收样例

输入：「把 report.json 打开，我改完你确认下格式对不对」

1. `studio.py open "…/report.json" --wait 120` → 等用户保存；
2. 收到 `save` 事件 → `sha` 变了；
3. `validation.ok=false` → 回：「保存成功，但第 12 行少一个逗号（`Expecting ',' delimiter`），要不要我打开窗口定位到那一行？」
4. 用户说好 → 再 `open` 一次并 `--reason` 说明；**不直接改文件**。

---

需要更细的协议说明（HTTP 路由、事件字段、插件权限）时读 `references/protocol.md`。
