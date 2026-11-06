---
name: workflow-studio
display_name: WorkBuddyStudio
display_name_en: WorkBuddyStudio
description: 把一句自动化需求落成可编译的 workflow.json（原子能力从 rpa-core catalog 现取），用 PPX 可视化编排窗口打开工作流（Agent 推文件，人在窗口里拼流程/改参数/编译/运行并确认），或在浏览器页面上让用户点选元素拿回稳定选择器。**编辑窗口是可选件：源码树自带 PPX 工程，wheel 形态可在 Windows 用 rpa-skill editor install 从 Release 补装；没窗口时降级为文件交接 + rpa-core capture**。当用户说「帮我做个网页自动化」「写个 workflow」「把这几步做成流程」「打开编辑器」「在窗口里改这个流程」「让我看看这个工作流」「你改完我确认一下」「帮我把流程打开我手动调」「拾取元素」「选一下页面上的按钮/输入框」「要这个元素的 selector」「登录框定位不到」时使用。触发词：做个自动化、网页自动化、生成流程、写个 workflow、打开编辑器、编辑窗口、Studio、让我确认、人工确认、审阅流程、改完保存、拾取元素、选元素、元素定位、selector、选择器、抓元素、页面上点一下。
description_zh: 让 AI 与人共用同一个工作流与同一个页面：流程用 PPX 可视化编排窗口改、页面元素用浏览器插件点选，结果都回到对话里。
description_en: A human-in-the-loop bridge for RPA agents. Opens workflows in a visual PPX editor window (cards, params, compile, run) and captures page elements via a browser extension, returning structured results to the agent.
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

# 人机协作三件套（Studio）

这个技能解决一件事：**AI 不该替人做「只有人能做的判断」**。

改一个参数、确认一版配置、指出页面上到底是哪个按钮 —— 这些事让 AI 猜，猜错了没人发现；
让人做，人又要自己找文件、自己开编辑器、自己把结果贴回来。本技能把这一来一回压成两条命令。

三件套各管一摊，缺一件就退化成普通工具：

| 组件 | 作用 | 什么时候用 |
|---|---|---|
| 轻量编辑窗口（PPX） | 人改文件、确认改动，保存后自动把 sha 与校验结果交回 Agent | 需要人动手改，或需要人「点头」 |
| 本技能 + 会话服务 | Agent 侧的命令行入口，负责排队、等待、取结果 | 每次调用 |
| 浏览器插件（MV3） | 人在页面上点一个元素，插件算出稳定选择器 | 需要页面元素定位 |

> 表格里的「编辑窗口」不是每种安装形态都有。开干之前先看下一节，别对着一个没有窗口
> 的安装说「我帮你打开编辑器」。

## 形态：先看你手里这版带不带编辑窗口

窗口是**可选件**，有三条来路，跟 rpa-core 本体怎么装的关系不大：

| 来路 | 怎么来的 | 前置 |
|---|---|---|
| 装好的 exe | `rpa-skill editor install`，或装技能时加 `--with-editor`（从 GitHub Release 下 ~16MB） | 仅 Windows；路径别带中文 |
| 源码树工程 | `git clone` 仓库后由 `studio.py gui` 走 `ppx dev` | PPX + 前端依赖 |
| 没有窗口 | 只装了 wheel 又没补装 | 不影响生成流程 / 跑流程 / 元素捕获 |

一条命令判定：

```bash
{{PYTHON}} "{{SKILL_DIR}}/scripts/studio.py" doctor
```

看 `editor` 那行（`editor project` 是旧版名字，别找错行）：

- `[PASS] editor … <版本> · <exe 路径>` ⇒ 装好的窗口，下文第一到五节都适用；
- `[PASS] editor … ppx dev · <工程目录>` ⇒ 源码树形态，同上；
- `[SKIP] editor … 没装（可选件…）` ⇒ **没有窗口**。Windows 上想补一个：

  ```bash
  rpa-skill editor install
  ```

  它把便携包解到 `~/.rpa-core/editor/current/`，并写 `~/.rpa-core/editor.json`
  （窗口的工作区、rpa-core 可执行文件都从这份清单里读）。连 GitHub 需要代理时，
  Node 的内置 `fetch` 和 npm 不是同一套配置：Node 22 上先设 `NODE_USE_ENV_PROXY=1`
  再装（Node 24 默认可用）；连不上就手工下好 `RPA-Editor-win64.zip` 和同名 `.sha256`，
  用 `--editor-zip` 指向本地包。装窗口失败不影响已经装好的技能。不装就按下面降级：

  - 不要说「我帮你打开编辑器」，也别跑 `studio.py gui` / `open` / `wait`——它们会报
    `EDITOR_MISSING`；
  - 走第五节的降级：把改动写成明确的 diff 或整段贴给用户，让他自己改、改完告诉你；
  - 元素捕获改走命令行：`{{RPA_CORE}} capture browser`（浏览器页面）或
    `{{RPA_CORE}} capture desktop`（桌面窗口）。扩展源码在包内，
    `{{RPA_CORE}} install-extension` 照样能装好——`--status` 里的 `_extension_dir`
    应当指向 `site-packages/rpa_core/extension`。

判形态别用「命令跑不跑得动」当依据：`rpa-core` 本体在几种形态下都好用，差的只是
编辑窗口这个 PPX 应用，以及由它提供的「在窗口里改 + 插件点选」交互。

下文命令行里的前半截（解释器绝对路径 / `rc.py` 入口）是安装时写死的，原样照抄即可。
若命令里还能看到 `{{…}}` 这种没被替换的花括号，说明这份技能是手拷进来的、没走安装器，
重装一次就能补齐：

```bash
rpa-skill install --force
```

## 零、先有流程：从需求生成 workflow.json

**开工前先确认运行时在位**。这一条不过，下面所有 `catalog` / `validate` 都跑不了，
而失败会表现成「流程写不出来」而不是「缺个运行时」，很容易误判：

```bash
{{RPA_CORE}} --help
```

- 打出 `{validate,run,resume,catalog,…}` ⇒ 往下走。
- 打出 `RPA_CORE_NOT_FOUND` ⇒ **别硬编流程**，也别说「技能坏了」。把输出里 `fix`
  的第一条原样抄给用户（通常是重装时指一下仓库根：
  `rpa-skill install --force --rpa-core-dir <rpa_core 仓库根>`），并说明「技能装好了，
  只是它要调的那半（Python 运行时）没接上」。
- 要一次看全：`rpa-skill doctor`；没有 `rpa-skill` 命令时用
  `{{PYTHON}} "{{SKILL_DIR}}/scripts/studio.py" doctor`（顺带报 PPX 与浏览器扩展在不在）。

用户说「帮我把这个页面上的事自动做一遍」时，产出是一份 `workflow.json`。这一步的知识
**全部**在 `references/assembly.md`——节点形状、数据流语法、元素引用、编译器兜不住什么，
都以它为准，本文不重复（重复就会漂移）。

三条最容易踩的：

1. **能力清单现取，不靠记忆**：`{{RPA_CORE}} catalog --view agent`。卡片里的 `params`
   决定参数键，`x_outputs` 的键名决定 `${steps.<节点id>.outputs.<键>}` 怎么引用。
2. **交互元素写 `elementRefs` 占位**，选择器值交给用户捕获（第四节），不要在 `with` 里
   编一个 selector。
3. **生成后一定跑 `{{RPA_CORE}} validate`**；但要说清楚它的边界——它只查键集合与引用根，
   数据流写错、混排的 `${}` 不解析、输出的 key 拼错，它都**不会**报。所以过了 validate
   之后仍要按 `references/assembly.md` §7 的清单自查，别把「编译绿」当成「能跑」。

## 一、开工前先自检

```bash
{{PYTHON}} "{{SKILL_DIR}}/scripts/studio.py" status --pretty
```

- 输出里有 `会话 1.0.0 · 端口 7788` → 服务在跑，继续。
- 报 `NO_SESSION` / `SESSION_DOWN` → 先起服务（下面第二步），**不要**跳过去硬干活。
- `编辑窗口 离线` → 窗口没开。**先确认这版有没有窗口**（看上面 doctor 的 `editor` 那行）：
  有就说「编辑器没开，我开一下」再继续；没有就**不要**去开，直接按第五节的降级走。
  两种情况都不要假装文件已经打开了。

自检不过时的兜底：直接读文件、把改动写清楚，让用户自己贴回去。**永远不要伪造「用户已确认」。**

## 二、起会话服务（一次即可，常驻）

```bash
{{PYTHON}} "{{SKILL_DIR}}/scripts/studio.py" serve --port 7788 --root "<工作流所在目录>"
```

- 只监听 `127.0.0.1`，不对外网开放；端口被占会自动顺延（实际端口写在返回值里）。
- **`--root` 要指向 workflows/ 的上一级**：会话按它放行绝对路径，窗口也按它做越界守卫。
  两边根不一致时，窗口会回 `BAD_PATH` 并写明它认的根——把报错里的 `studio.py serve --root
  <目录>` 原样照抄重起即可，别去猜路径、也别说「编辑器坏了」。
  也可以用一条命令起全套：`studio.py gui --root "<工作流所在目录>"`，它会把同一个根
  注入窗口；连已起的服务时会从 `status` 里读它的根。
- 后台跑的时候把 stdout 丢掉即可，`studio.py status` 随时能问到状态。

## 三、工作流 A：让用户改文件并确认

```bash
# 1. 把文件递进窗口（立刻返回；--wait 会一直等到用户保存/关闭）
{{PYTHON}} "{{SKILL_DIR}}/scripts/studio.py" open "D:/work/workflows/report.json" --reason "请把 pageSize 调到 50" --wait 120
```

- 退出码 `0` + `events` 非空 ⇒ 用户**保存了**（或关了窗口，看 `kind`）。
- 退出码 `3` ⇒ 超时，用户没动。**不要**自己改文件再说「已按你的要求改好」。
- 退出码 `2` ⇒ 服务或路径有问题，把 `error` 原文转述给用户。窗口回的 `open-failed`
  也会秒回这个码（带 `BAD_PATH` 和可照抄的修法），**不要**当成超时继续等。

```bash
# 2. 只等保存事件（不重新打开文件）
{{PYTHON}} "{{SKILL_DIR}}/scripts/studio.py" wait --kind save --timeout 120
```

返回的事件里带 `sha` / `bytes` / `lines` / `validation`：

- `validation.ok = true` ⇒ 语法通过；`kind = workflow` 说明还过了工作流模型校验。
- `validation.ok = false` ⇒ 把 `message` 与 `line` 原样告诉用户，并建议在窗口里改（窗口会标红那一行）。

拿到 `sha` 之后**必须重新读文件**再谈内容，不要凭记忆复述用户改了什么。

## 四、工作流 B：拾取页面元素

**先分形态**（见上文「形态」一节）：`editor` 是 `[PASS]` 才走下面这种「会话里等插件回传」；
是 `[SKIP]` 就跳过那段，改用命令行通道（两条都随包发布，不依赖窗口）：

命令行通道**不会替你打开浏览器**：`capture browser` 只是让已在线的扩展进入等待，
由人在页面上点一个元素。先跑 `{{RPA_CORE}} env-status` 看 `browsers.*.online`，
离线就先打开装好扩展的浏览器，别在等待态里让用户空点。
`--browser-type` / `--start-url` / `--page-url` / `--user-data-dir` / `--headless`
这五个参数目前被实现忽略，不要写进命令里、也不要以为它们会起浏览器或打开网址。

```bash
# --workflows 相对于当前目录，务必显式给绝对路径，且 capture / elements / run 用同一个根；
# --flow 是流程目录名（存到 <workflows>/<flow>/elements/<--save-as>.json），不是文件路径；
# --save-as 与 --flow 必须成对，缺 --flow 会报 BAD_REQUEST。
{{RPA_CORE}} capture browser --save-as userField --flow login \
  --workflows "<工作区绝对路径>/workflows" --timeout 60
# 桌面：hover 实时高亮 + 热键或点击捕获（Windows）
{{RPA_CORE}} capture desktop --hover --save-as saveBtn --flow login \
  --workflows "<工作区绝对路径>/workflows"
```

结果写到 `<workflows>/<flow>/elements/<name>.json`，**落盘即绑定**同名的 `elementRefs`
（ref-only，值不进 `with`，没有第二个「应用」步骤）。核对：

```bash
{{RPA_CORE}} elements list --flow login --workflows "<同一个绝对路径>/workflows"
```

```bash
# 有窗口时（doctor 的 editor = PASS）：Agent 侧等插件回传
# 1. 先进入等待（Agent 侧阻塞 ≤60s）
{{PYTHON}} "{{SKILL_DIR}}/scripts/studio.py" capture --timeout 60
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
- 要选择器 → 先试命令行捕获（`{{RPA_CORE}} capture browser` / `capture desktop`，不依赖会话与窗口）；
  仍拿不到再请用户从 DevTools 里复制，或退化为「按文案定位」并说明风险。

## 六、错误码对照

| code | 含义 | 该怎么做 |
|---|---|---|
| `NO_SESSION` | 服务没起 | 先 `serve`，再重试一次 |
| `SESSION_DOWN` | 服务挂了/端口变了 | `status` 确认，必要时重起 |
| `BAD_PATH` | 路径不存在或越界（会话侧和窗口侧都会给） | 报错里带窗口认的根和 `studio.py serve --root <目录>`；照它对齐两边，别猜路径 |
| `EDITOR_OFFLINE` | 窗口没开 | 告知用户并等待，不要写入后声称已确认 |
| `TIMEOUT` | 用户没在时限内操作 | 询问是否继续等，不要自行代劳 |
| `INTERNAL_ERROR` | 窗口 Python 侧抛了未预期异常 | 看 `~/.rpa-core/editor.log` 的 traceback，别下「窗口本体坏了」的结论 |

**窗口报错怎么取证**：`0.1.3` 起窗口会把 Python 异常写到 `~/.rpa-core/editor.log`
（可用 `RPA_EDITOR_LOG` 改路径）。要一眼看全根与资源，设 `RPA_EDITOR_SELFTEST=1` 再启动
窗口 exe，它不进界面、直接输出 JSON，里面有 `workspace / allowedRoots / sessionRoot / logPath`。
看到 `INTERNAL_ERROR: Python API 执行失败` 时，**先读日志**——那句话只是桥层的统一包装，
不是原因本身。

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

需要更细的内容时读 `references/`：

- `assembly.md` —— **宿主无关**的 `workflow.json` 组装规范（生成流程时必读）。
  它只依赖 `rpa-core` 的契约，不假定任何特定的 AI 客户端，可以原样搬到别的 agent。
- `protocol.md` —— 会话服务与插件的协议细节（HTTP 路由、事件字段、插件权限）。
