# M65：宿主边界冻结 + PPX 实验基座

Status: `wip`

> **2026-10-28 立项**。维护者拍板：PPX 壳继续推进，**Qt 代码留着不动、也不改**，并把这条写进
> 项目要求。沿用 M63 先例**不占 active 位**（M52 未收口）——它是旁支实验，主线仍是 selector-advisor。
>
> 前置：ADR 0019。本任务**不触产品代码**（`src/` / `commands/` / `tests/` 一行不改），
> 且「没触」由 S1 的门禁自己证明，不靠自觉。

## 一、决策来源（维护者拍板，2026-10-28）

| # | 问题 | 拍板 | 处置 |
|---|---|---|---|
| ① | PPX / Tauri 用哪个 | 先做 PPX 初版看效果 | 原型落 `.harness/demo/ppx_probe/`，独立 venv、独立项目 |
| ② | 既有 Qt 代码怎么办 | **留着不动、也不改** | 逐文件 sha256 冻结 + 门禁比对（S1） |
| ③ | 迁移要不要现在谈 | 不谈，先把实验做实 | ADR 0019 §2.3 三条门槛：覆盖层 / 打包分发 / 桌面集成 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| **S1** | 新增 `.harness/scripts/check_host_boundary.py` + `.harness/gui-freeze.json`（22 个 `.py` 的 sha256），进 `check_all.py` | 三条判据：GUI 冻结（集合 + 逐文件哈希）、原型不得 import `PySide6` / gui UI 模块、能力层不得依赖 `ppx_py`；负向验证 14 条全中 | ✅ |
| **S2** | 项目要求写入：ADR 0019 / `AGENTS.md`（产品方向 + 规则 14）/ `invariants.md`（#16） | `check_docs` 绿；三条渠道读到的口径一致 | ✅ |
| **S3** | PPX 原型：三栏编辑器 + 真实目录 + 编译校验 | 89 条命令、8 个样例流程全部编译通过；bootstrap 热态 20 ms | ✅ |
| **S4** | PPX 原型：**覆盖层实证**（透明置顶 + 鼠标穿透 + 高亮框跟随） | 见 §五；四条读数全落（`transparent=True` 假透明 98.09% 灰 / 抠色真透明 / 整窗不可命中 / mousemove 0 vs 3 / 轮询跟随可用） | ✅ |
| **S5** | PPX 原型：编辑 + 保存闭环（改参数 → 落盘 → 复编译） | 15 条判据全中（见 §六）；**新增节点不做**（涉及 require/default/oneOf，属 S6） | ✅ |

## 三、判据设计（为什么长这样）

**为什么用 sha256 基线，而不是「约定别动」。** 「不改 Qt」此前只是口头共识，下一个人换个说法
就能绕过去（「我只动了这一行」「顺手格式化一下」）。基线把「碰它」变成一次显式动作：改完必须
`--update-freeze` 重建基线，那一步的 diff 会留在提交里，签字与偷改在历史里长得不一样。

**为什么基线是快照而不是允许清单。** 允许清单要人维护「哪些文件可以动」，那就退化成审批；
快照只回答一个问题：跟上次冻结时是否一字不差。

**白名单为什么按前缀放行。** 首版 `GUI_ALLOWED_IN_DEMO` 用精确相等，结果原型里
`from rpa_core.gui.i18n import load_command_display_names` 被拼成 `rpa_core.gui.i18n.load_command_display_names`
⇒ **判据把自己判红**。改成按模块前缀放行后才对。这条留在这儿，因为「白名单打不中合法用法」
是这类判据最常见的翻车形态。

**删除场景为什么只在临时树里造。** 探针有成批 `unlink` / `rmtree` 会撞上删除守卫
（按 turn 累计），守卫一旦 fail-closed，探针就会在「删得太多」上失败、把真正的判据结论掩盖掉。
所以临时树用序号目录、**不自动清理**，删除语义在沙盒里验，不碰真仓库。

## 四、负向验证（`probe_host_boundary.py`，14 条全中）

| 方向 | 用例 | 期望 |
|---|---|---|
| 绿 | 基线一致 | 无报错 |
| 红 | 改一个字节 | `内容被修改` |
| 红 | 新增文件 | `新增了文件` |
| 红 | 删除文件 | `文件被删除` |
| 绿 | 回填后重新绿 | 无报错 |
| 红 | 基线文件被删 | `缺少冻结基线` |
| 红 | 原型 `import PySide6` | `不得引入 Qt` |
| 红 | 原型 `from rpa_core.gui import app` | `不得依赖 gui` |
| 绿 | 原型只用 `gui.i18n` + 能力层 | 无报错 |
| 红 | 能力层 `import ppx_py` | `不得依赖宿主壳` |
| 绿 | 清掉泄漏 | 无报错 |
| 红 | **真仓库**注入一个字节 | 门禁红 |
| 绿 | 逐字节还原核 md5 | 一致 |
| 绿 | 还原后门禁重新绿 | 无报错 |

取证输出：`.harness/spike/probe_host_boundary.out`。

## 五、S4 覆盖层实证（已做，结论在 ADR 0019 §2.4）

探针 `overlay_probe.py` 跑了四轮，每轮都靠读数而不是观感：

| 轮次 | 改了什么 | 拿到什么 |
|---|---|---|
| v1 | 只用 `transparent=True` | 灰板 `#F0F0F0` 占 98%⇒假透明；穿透时 mousemove 0 次 vs 恢复后 3 次 |
| v2 | 加 `BackColor=洋红` | 背景变洋红但 `SetLayeredWindowAttributes` 返回 0（**但没查 last_error**） |
| v3 | 改走 `form.TransparencyKey` + 报 `last_error` | 灰 0.0% / 洋红 0.0% ⇒ 真透明，截图能看到下层终端文字；API 也 `ok=true, last_error=0` |
| v4 | 抠色后测「哪块吃鼠标」 | 透明区与红框采样点**均未命中自身窗口** ⇒ 覆盖层自身收不到点击，交互控件得另开窗口 |

★ **v2 的误判要记住**：当时用「pid 是否等于自己」判穿透，得出 `clickthrough_effective: false`。
WebView2 的渲染跑在独立进程（`msedgewebview2.exe`）里，`Chrome_RenderWidgetHostHWND` 的 pid
**永远**不等于本进程 ⇒ 这个判据恒假。正确判法是 `GetAncestor(hwnd, GA_ROOT) == 我们的 hwnd`。
判据写错比没判据更坏：它会让「其实穿透了」看起来像「没穿透」。

## 六、S5 保存闭环实证（已做）

两个探针，都是读数：

**`roundtrip_probe.py`（保存保真度，8 个样例流程）**

| 层 | 读数 | 结论 |
|---|---|---|
| 文本层：`json.loads` → Qt 编码口径重排 | **5 个逐字节相同 / 3 个不同** | 3 个不同是原文把数组写在单行（`"menuPath": [ ... ]`），换谁保存都会重排；**不是 PPX 的问题** |
| 模型层：`model_validate(...).model_dump(by_alias=True)` 对原文 | **0 个无损**，每个多 14~70 处 | `elementRefs`/`output_aliases`/`retry_*`/`_exprModes`/`timeout_seconds` 等默认值会被灌进文件 |

⇒ **保存不走 `model_dump`**：在原始 dict 上定点改键 + `Workflow.model_validate` 复校 + Qt 同款
编码（`indent=2` / `ensure_ascii=False` / 末尾换行 / `write_bytes` 保 LF）。

**`save_probe.py`（15 条判据，headless 直调 RPC）**

| # | 判据 | 读数 |
|---|---|---|
| 1 | 沙盒流程进列表 | 9 个流程 |
| 2 | 字段类型：manifest 优先、未声明按既有值推断 | `url/string/manifest`、`timeoutMs/integer/manifest`、`headless/boolean/existing`、`userAgent/string/existing` |
| 3 | 改 3 个参数 | ok、changed、编译通过 |
| 4 | 磁盘值是新的且类型没变 | `timeoutMs: 30000`（int）、`headless: true`（bool） |
| 5 | **未触碰的行逐字节不变** | 改 3 个参数 = 6 条 `+/-`，无其它漂移 |
| 6-7 | 同值再存 | `changed=False`、文件 SHA 不变（不做无谓写盘） |
| 8-9 | 类型不符 / JSON 合法但类型不符 | 都 `INVALID_VALUE`，文件 SHA 不变 |
| 10-11 | 新增键 / 非 action 节点 | `INVALID_VALUE`（指向 S6）/ `NODE_NOT_EDITABLE` |
| 12-13 | 外部改过文件 | `FLOW_CHANGED`，且**不覆盖**外部内容 |
| 14-15 | 真实窗口端到端 | 改 `timeoutMs` 20000→4500 → 保存 → `1924→1923 B`、`sha 6ab2bcea1460`、27 ms、编译通过；磁盘 `diff` **只有那一行** |

**负向验证**：把 `_coerce` 改成恒返回原文（等于关掉类型系统）⇒ 探针 **9 条变红**，还原后 md5
逐字节一致、复绿。

★ **本片自己踩的坑（判据被合法用法打中）**：首版按「manifest 声明类型」强制转换，而未声明的
`headless` 落到 `string` 默认 ⇒ 用户输入 `true` 被存成 `"true"`：**保存成功、编译通过、运行期才炸**。
改成「声明优先、未声明按既有值推断，且改完类型必须一致」才对。

★ **顺带挖到的仓库级漂移**：`examples/baidu-news-top10/workflow.json` 的 `browser.navigate` 节点
带 `headless` / `userAgent` 两个**没有任何 manifest 声明**的参数，且缺 `required` 的 `browserType`；
按 `orchestrator.py:798` 的 `additionalProperties: false` 校验，这个样例真跑会被 `INVALID_INPUT`
拦下。已登记 BACKLOG（不属于 M65 范围，没动它）。

## 七、未决

- **S6（新增参数 / 新增节点）**：需要 `required`/`default`/`oneOf`/enum 语义与 manifest 字段级声明
  渲染，等于移植 `param_form.py`（1391 行）的核心，本片没做。
- **覆盖层的工程形状**已清楚但未实现进原型：抠色基色 + 整窗穿透 + 轮询 `GetCursorPos` 三件套，
  交互控件必须另开非穿透小窗口。轮询频率与 CPU 占用未测。
- **打包分发**（ADR 0019 §2.3 第二条）与**桌面集成**（第三条：单实例/全局热键/托盘）都还没碰。
- **`gui.i18n` 的位置债**（ADR 0019 §4-1）：原型依赖它，但冻结期内不能搬。
- 原型目录在 ruff `exclude` 里，不进 lint/文档门禁 —— 有意如此（脚手架 ≠ 产品代码），
  代价是质量只能靠人工与实证。
- 原型**只写 `_sandbox/` 里的副本**（`sandbox/` 在流程下拉里单独分组）：保存试验绝不碰
  `examples/` 与 `workflows/` 的真实流程。
