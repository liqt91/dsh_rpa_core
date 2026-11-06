# M66：元素拾取的交互重设计（技术两条线收进编排层）

Status: `wip`

> **2026-11-06 立项，当日开工**。起因：维护者「一个好的产品，好的 AI 产品，是怎么定义这两条线的
> 交互逻辑的，即符合用户直觉又高效」。本文是**设计契约 + 施工单**，不是复盘。
> 前置：M65（PPX 实验基座 / ADR 0019 宿主边界冻结）、M52 S6（ADR-002 ref-only 元素引用）、
> `workbuddy-suite` 技能分发链（Release `editor-v0.1.1`）。
> **不占 active 位**：`project_state.active_plan` 仍是 `M52-selector-advisor.md`，
> 本里程碑按 M63-④ 用 `wip` 表达「旁支已开工」。

## 一、起因与问题的定性

维护者问的是**交互逻辑**，不是「再加几个按钮」。当前 `.harness/demo/ppx_probe/app/` 的原型把
技术分叉直接摊给用户：每个待补元素给「网页 / 桌面」两个按钮，用户得先判断元素在哪条线上。
而 `rpa.missingElements` 返回的 `m.command` 已经写着答案 —— **能推断的选择题不该交给人做**。

这里叠了两层「两条线」，必须分开定义，否则讨论永远对不上：

| 层 | 内容 | 定位 |
| --- | --- | --- |
| 技术线 | CLI / 会话+窗口、`capture browser` / `capture desktop` | **传输方式**，属于实现，应被编排层封装，用户不可见 |
| 产品线 | Agent 意图 / 人在回路 | 不是选择题，是**一次交接**：Agent 说「第 3 步需要这个元素」，人只做系统够不着的那个物理动作（点一下），系统接住并回执 |

## 二、取证（动手前的现状，只读取证于 2026-11-06）

| 现状 | 位置 | 后果 |
| --- | --- | --- |
| 每个待补元素给「网页 / 桌面」两个按钮 | `gui/src/main.js` 的 `renderElementsPanel` | 把可推断的技术决策转嫁给人 |
| `browserType: 'edge'` 写死 | `gui/src/main.js` 的 `captureElement` | 真机只有 Chrome 装了插件；且该参数在 `ExtensionCaptureSession.__init__` 被 `**_ignored` 吞掉 |
| 状态全靠一行 `el.status.textContent` | `gui/src/main.js` 的 `captureElement` / `pollCapture` | 无倒计时、取消、能力探测、失败归因；等待期不知道在等什么 |
| spawn 之后才知道行不行 | `api/api.py` 的 `start_capture` | 用户会在「看起来在等」的状态里空等 60 秒，这是最伤效率的一处 |
| 文案混写两种手势 | `api/api.py` 的 `instructions` | 选了网页却看到「桌面按 F9」 |
| `_CAPTURES[name]` 单键 | `api/api.py` | 同名元素在不同流程里互相覆盖 |
| 按钮 22px、字号 11px、色用 `--faint` | `gui/src/style.css` 的 `.elemRow .capBtn` 等 | 低于 WCAG 2.2 `2.5.8` 的 24×24 命中区，对比度存疑 |
| 5 个 CLI 参数是死开关 | `src/rpa_core/cli.py` 的 `capture browser` 分支 | `--browser-type` / `--start-url` / `--page-url` / `--user-data-dir` / `--headless` 被实现忽略（纯内存探针验过），SKILL.md 第四节却把它当示例照抄 |

**红线**：昨天修掉的 `--with-editor` 假警告，和这里是同一种病 —— **状态反馈没跟真值对齐**。
本里程碑任何「已就绪 / 已捕获」都必须从 `env-status` / `editor.json` / 落盘文件推导，
禁止用「进程起来了」暗示「能力在线」。

## 三、设计原则（四条，判据要能钉住）

1. **意图里不出现实现名词**。用户可见文案禁止 `capture / browser / desktop / serve / session / UIA / 扩展`；
   文案集中在一张 `PICK_TEXT` 表里，静态判据扫表，不扫整份源码（避免误伤内部变量）。
2. **不确定性归系统**。能推断就推断；推断不了才问，且只问事实（「这个控件在浏览器里吗？」），
   不问技术。
3. **状态说真话**。每个可视状态从能力快照与落盘真值推导；能力不在线时**先修能力**，
   不进等待态。
4. **自动决策可解释、可覆盖、可撤销**。主路径零选择；折叠的「换个方式」只本次生效，
   不写回配置。

## 四、决策表：编排器把两条线收敛成唯一 target

输入两样：`miss.missing[].command`（生成流程时已写死）与能力真值（`rpa-core env-status` 的
`browsers.*.installed/online`）。输出唯一 `target ∈ {browser, desktop}` + `hover` + `reason`。

| 输入 | 决策 | 用户看到 |
| --- | --- | --- |
| `command` 以 `browser.` 开头 | `browser`（扩展腿） | 「在页面上点一下这个元素」 |
| `command` 以 `desktop.` 开头 | `desktop` + `--hover`（桌面 hover 腿） | 「把鼠标移到那个控件上，点一下」 |
| 命令缺失 / 无法归类 / 混合流程 | 扩展在线走 `browser`，否则 `desktop --hover`（桌面 hover 本身就是双通道仲裁） | 「点一下要用的那个元素」 |
| 能力不在线 | **不 spawn**，返回具体修复动作 | 「浏览器还没连上 → 一键打开 / 手动填写」 |
| 用户展开「换个方式」 | `override` 覆盖自动决策，仅本次 | 三项，附「通常不用改」 |

**探测发生在点按钮之前**：`rpa.missingElements` 同批带 `capabilities`，
`startCapture` 再核一次（防两次探测之间能力漂移）。绝不 spawn 后才告诉用户不行。

## 五、状态机与文案

六态：`待补 → 探测 → 已就绪 → 等待点选 → 已捕获 → 已绑定`（`已绑定` 与 `已捕获` 合并展示，
ref-only 落盘即绑定，不需要第二个「应用」按钮）。每态只回答「轮到谁 / 还要多久 / 出不去怎么办」。

| 态 | 界面 | 可操作 | 出口 |
| --- | --- | --- | --- |
| 待补 | 一行一个缺口，主按钮「补一下」 | 点 / `Enter` / 折叠「换个方式」 | — |
| 探测 | 按钮变「检查环境…」≤1s | 取消 | 不在线 → 具体修复动作，**不进等待态** |
| 已就绪 | 高亮目标步骤 + 一行动作提示 | 开始 / 取消 | 10s 自动开始，不加二次确认 |
| 等待点选 | 常驻条：倒计时 + 取消 + 键盘替代入口 | 取消 / 重来 | 到点回待补，保留 name 与步骤 |
| 已捕获 → 已绑定 | 「第 3 步 · userField 已就绪」并定位到卡片 | 查看 / 重抓 | — |

文案对照（用户可见层）：

| 场景 | 旧 | 新 |
| --- | --- | --- |
| 主按钮 | 网页 / 桌面 | 补一下 |
| 启动 | 正在启动捕获「x」(browser)… | 检查环境… |
| 等待 | 去浏览器 Ctrl+Click 目标元素（桌面按 F9） | 在页面上点一下「登录按钮」 · 还剩 48 秒 |
| 失败 | 捕获结束但未落盘（退出码 3） | 没收到这次点选，再试一次？ |
| 成功 | 已捕获并绑定「x」 | 第 3 步已就绪 · userField |

`aria-live="polite"` 只播报**状态跃迁**，不播报倒计时每秒变化（否则屏幕阅读器被刷屏）。

## 六、契约改动（施工口径）

- `rpa.startCapture` 入参 `{ path, name, override? }`，**去掉** `target` / `browserType`；
  `target` 由后端 `resolve_target(command, capabilities)` 单函数产出。
- 新增 `rpa.capabilitySnapshot()`；`rpa.missingElements` 同批返回 `capabilities`。
- `rpa.capturePoll` 改 `{path, name}`，返回 `state / deadlineAt / reason / boundNodeId`，
  前端不再从「进程是否存活」猜状态。
- 新增 `rpa.cancelCapture({path, name})`；`_CAPTURES` 改 `(path, name)` 复合键。
- 结果 schema 两腿统一：`{name, selector, strategy, matches, kind, boundNodeId}`。
- **不做前向兼容、不写数据迁移**（`invariants.md` #15）。

## 七、施工范围（P0 本轮 / P1 欠账）

**P0（本轮落地）**

- `api/api.py`：能力快照、`resolve_target`、探测前置、复合键、`cancelCapture`、
  `capturePoll` 的状态/截止时间/原因、文案按目标单义。
- `gui/src/main.js`：`PICK_TEXT` 文案表、单主按钮 + 折叠覆盖、六态 UI、倒计时/取消、
  状态条 `aria-live`、成功定位到卡片。
- `gui/src/style.css`：命中区 ≥24×24、正文字号 ≥14px、焦点环可见、状态同时用文字+图形。
- `workbuddy-suite/skills/workflow-studio/SKILL.md` 第四节：删掉「自己起浏览器 + 载扩展」与
  5 个死参数示例，改成「先开浏览器并确认连上，再点一个元素」；`--workflows` 显式绝对路径。
- 新增判据探针：UI 文案禁词、单主按钮、能力假绿、`resolve_target` 唯一性、渲染层无障碍。

**P1（登记欠账，不在本轮）**

- **键盘等价路径**（WCAG 2.2 `2.5.7`）：现在只有 Ctrl+Click / F9 两条指针路径。
  真做需要动 `src/rpa_core/cli.py` / 捕获会话（焦点移到目标 → `Enter` 确认），
  不是前端能补的。本轮**不假装已经支持**：等待态给「取消 / 手动填写选择器」这类键盘可达出口，
  并在任务单登记。
- `capture browser` 那 5 个死参数：删或标 deprecated；动 `src/rpa_core/cli.py` 要过全门禁，
  与 UI 改动分开发布，否则分不清是哪边出问题。
- 混合流程的显式 `capture hybrid` CLI 入口（目前靠 `desktop --hover` 内部双通道）。

## 八、边界

- **能改**：`.harness/demo/ppx_probe/app/` 的 `main.js` / `api.py` / `style.css`、
  `SKILL.md` 第四节、新增判据探针、台账。
- **不能碰**：`src/rpa_core/gui/`（ADR 0019 逐文件哈希冻结）、已推的 `editor-v0.1.1` tag（不 force）。
- **发布顺序**：UI 改了 → 重打窗口包 → bump `ppx.toml` → 新 tag `editor-v0.1.2` 触发 CI；
  技能文档改了 → `rpa-skill install --target dsh --force`。**两件事别合成一次发布**。
  本轮只做本地提交，push / 发版须维护者另行授权。

## 九、验收判据（能红才有资格进门禁）

1. 静态扫 `PICK_TEXT` 的值，出现实现名词任一 → 红。
2. 渲染层断言每个待补行**恰好 1 个主操作按钮**；「换个方式」默认折叠。
3. `resolve_target` 给定 `command` + 能力快照输出唯一值；注入「忽略命令类型」必须红。
4. 注入「插件离线仍报已就绪」必须红 —— 防昨天那种假警告。
5. 渲染层断言主按钮命中区 ≥24×24、焦点环可见、`aria-live` 存在。
6. 端到端：待补 → 「第 3 步已就绪」，用户操作 1 次、零技术选择；超时/取消后 `name` 与步骤不丢。
7. 改完做负向验证：注入 → 确认真红 → 逐字节还原核 md5。

## 十、P0 落地记录（2026-11-06 当日）

状态仍是 `wip`：P1 三条欠账未动（键盘等价手势、CLI 死参数、显式 hybrid 入口）。

**改了什么**

- `api/api.py`：`capability_snapshot` / `resolve_target` / `_pick_plan`（探测与决策的唯一权威）、
  `rpa.pickPlan`、`rpa.cancelCapture`、`rpa.writeElement`；`startCapture` 去掉 `target` / `browserType`
  并复用 `_pick_plan`，`capturePoll` 改 `{path,name}` 返回 `state/deadlineAt/reason/boundNodeId`，
  `_CAPTURES` 改 `(path,name)` 复合键，`desktop` 一律 `--hover`，两条腿统一结果 schema。
- `gui/src/main.js`：`PICK_TEXT` 文案表 + `PICK_ICON` 状态符号；每个缺口**恰好一个**主按钮
  「补一下」，技术选择收进折叠「换个方式」（自动 / 网页 / 桌面，仅本次生效）；
  六态状态条（探测 / 就绪 / 等待 / 已绑定 / 取消 / 超时失败）、倒计时、`Esc` 取消、
  手动填写选择器（键盘可达）、成功后定位并高亮目标卡片。
- `gui/src/style.css`：主按钮 32px、正文 ≥14px、`:focus-visible` 焦点环、状态用符号+文字+颜色、
  <960px 单列、语义色全走 token。
- `SKILL.md` 第四节：删掉「自己起浏览器 + 载扩展」与 5 个死参数示例，补 `--workflows`
  绝对路径、成对 `--save-as/--flow`、落盘即绑定、`elements list` 核对。

**判据（两条进默认门禁）**

- `scripts/check_element_pick_ui.mjs`（12 条）：文案禁词 / 状态键齐全 / 命中区与焦点环 /
  **最小 DOM shim 真跑 `renderElementsPanel`**（单主按钮、折叠、aria-live、手动填写入口）/
  前后端契约 / **端到端状态流**（一次点击 → bound、取消与超时保留 name 与步骤）。
- `.harness/scripts/check_element_pick_contract.py`（8 条）：真跑 `resolve_target` 四分支、
  能力快照解析、离线不 spawn、override 不能绕闸、轮询四态、手填元素拒绝路径穿越。
- `.harness/spike/probe_element_pick_ui.py`：7 组负向注入，按推理写 `must_red`、
  等值断言「实际红 == 期望红」、`unchanged` 防误伤、md5 逐字节还原、哨兵自检。

**取证**：基线 20/20 GREEN；7 组注入全 `GREEN (negative verified)`、无哨兵残留；
门禁逐条 23/23 rc=0（pytest 换系统 temp 避开 50ms 计时假红）；技能冒烟 PASSED；
`vite build` rc=0（本机绕过 pnpm 的联网 deps-check，直调 `node node_modules/vite/bin/vite.js build`）；
改动文件 CRLF=0；ruff 全仓 All checks passed。

**一个实现取舍**：任务单 §五写「已就绪 10s 自动开始」，实现按 10s 落地，但
`startCapture` 在 `beginCapture` 才 spawn —— 就绪态不占前台，桌面 hover 不会在用户还没切到
目标窗口时抢鼠标。前端不重复算 target，探测结果一律来自 `rpa.pickPlan`。
