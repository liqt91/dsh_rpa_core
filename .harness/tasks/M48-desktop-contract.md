# M48 桌面契约一片（D1 + D2 + D3）

Status: `done`

Plan: `.harness/adr/0018-desktop-locator-contract-extension.md`
由来: `docs/yingdao-gap-catchup.md` §4「M48 桌面契约一片」

## 目标

把桌面元素的 locator 从「扁平字段等值匹配」升级为「有层级上下文 + 控件级模糊匹配 + 锚点前置」，
与影刀属性表 / 节点树形态对齐。三件事（D1/D2/D3）共用**一次**执行器契约改动，摊薄
「模型 + 两执行器 + manifest + 契约四条门禁」的固定成本。

## 授权与破戒

- M44 承诺「捕获即编辑**不动执行器契约**」；本片的 D1/D2 **有意破戒**。
- **破戒授权已获**（2026-09-29，见 `.workbuddy/memory/2026-09-29.md`）：
  「M48 破戒已获授权（locator 可加 path 等字段）」。
- 编号 2026-10-08 定案：M48 保持本片定义，不重编号。
- 书面依据：ADR 0018。

## 现状基线（2026-10-08 探针实测）

| 项 | 现状 |
| --- | --- |
| `DesktopLocator` | `extra="forbid"`；字段 `backend` `automationId` `controlType` `name` `title` `className` `classNameRe` `handle` `controlId` `menuPath` `foundIndex`；**无 path / 无 anchor** |
| agent `_describe_info` | `selector.locator` 只带 `backend`/`controlType`/`automationId`/`name`；`metadata` 带 `controlType`/`automationId`/`name`/`className`/`windowHandle`/`windowTitle`；**祖先链未回传**（`_window_scope_hit` 的 DFS 里有，但只用于 hit-test） |
| uia `_find`（`desktop.py` L875） | 判据仅 `control_type` / `title`(=locator.name) / `automation_id`，**全等值** |
| win32 `_find`（`desktop_win32.py` L813） | `title` / `class_name`（等值）/ `class_name_re`（正则 search）/ `control_id` / `found_index` |
| `matchMode` | 只作用于 `attachWindow` 的窗口 `title`（`desktop.py` L773 写死「className 恒等值」） |
| GUI 字段表 | `LOCATOR_FIELDS_BY_BACKEND`（`gui/element_editor.py` L97）：uia = controlType/automationId/name；win32 = title/className/classNameRe/controlId/foundIndex |

## 切片

### D1 桌面节点树（祖先链）

- agent：`_describe_info` 回传祖先链 → `selector.path`（**注意**：与 M44 的 `selector.path`
  同名不同义——那里是 css 路径后缀，这里是控件祖先链；文档与判据都要写明防混淆）。
- 模型：`DesktopLocator.path: list[LocatorStep] | None`，可选；出现时校验每级形状。
- 执行器：uia / win32 `_find` 各加「按 path 逐级收窄」过滤。
- GUI：确认框节点树（可勾层级列表，与 M44 Web 节点树同款交互，数据源是控件祖先链）。

### D2 控件级 matchMode

- 扩面到 `automationId` / `name`；**不作用**于 `className`（保持等值）/ `controlId` / `foundIndex`。
- 默认 `exact`；**未显式给 matchMode 时行为零变化**（专用判据钉住）。
- 同步 `manifest.errors` 声明。
- 歧义行为复用 `attachWindow` 既有口径（配 `foundIndex`）。

### D3 锚点起步

- `DesktopLocator.anchor: AnchorSpec | None`（内嵌一个 `DesktopLocator`，不发明第二套定位语言）。
- 运行期：目标查找前先解析 anchor；找不到 → **明确错误码**（不静默跳过、不回落）。
- **不做**：自动推荐引擎、相似元素成组（后续里程碑）。

## 判据要求（每条各做一次负向验证）

- D1 全链：模型 → uia → win32 → manifest → 契约**四条各负向验证一次**。
- 契约测试的窗口类名占位符按 §5 规则（`docs/yingdao-gap-catchup.md`）。
- D2 扩面同步 `manifest.errors`（`check_error_contract.py`）。
- 「未给 matchMode 行为零变化」必须有独立判据。
- GUI 节点树按 M44 S5 口径：**单向**（树 → locator 字段），末级不可取消。

## 牵连的四契约（项目铁律）

- `test_protocols` 总数
- `i18n.js`
- `test_field_tables_match_what_executors_actually_read`
- `test_extension_installer`（如涉版本）

## 状态记录

- 2026-10-08：任务单 + ADR 0018 建立，schema 定稿待维护者过目；**未动 `src/`**。
- 2026-10-08（开工后）：**D1 + D2 + D3 全部落地**，12 处负向验证全绿。
- 2026-10-08（任务 #8）：**GUI 展示 + 四契约同步完成**，负向验证扩到 19 处全绿；
  修掉编辑器静默丢结构字段 / `inert_locator_keys` 反向误报两个真 bug；
  **本片收口**，FULL GATE PASSED。

### 落地明细

| 切片 | 模型层 | 两执行器 | 其它 |
| --- | --- | --- | --- |
| D1 | `LocatorStep`（4 键、每级至少一键、`extra=forbid`）+ `path` 字段 | `_step_matches` / `_narrow_by_path` / `_find` 逐级收窄 | agent `_ancestor_chain` / `_locator_step_for` / `_dfs_smallest_at(path_out=…)` / `_describe_info(path_infos=…)` |
| D2 | `effective_match_mode` / `matches_text` 纯函数 + `VALID_MATCH_MODES` + `match_mode` 字段 | uia 作用于 `name`/`automationId`；win32 作用于 `title`；`controlType`/`className` 恒等值 | — |
| D3 | `AnchorSpec`（内嵌 `DesktopLocator`，禁嵌套）+ `anchor` 字段；`ErrorCode.ANCHOR_NOT_FOUND` | `_find` 顶部先解析 anchor，未命中 ⇒ `AnchorNotResolved`；两后端两调用点各接 `try/except` 报 `ANCHOR_NOT_FOUND` | `manifest.errors` 同步（findElement 门禁强制 + click/getText/input 主动补声明面） |

### 判据

- `tests/contract/test_desktop_locator_path.py`：**39 项**（D1 模型/agent/执行器 + D2 纯函数/两执行器 + D3 模型/两执行器/错误详情）。
- 桌面相关全量：`tests=315 failures=0 errors=0`（含 199 skipped，均为需要真机/E2E 的项）。
- 负向验证：`.harness/spike/probe_m48_negative.py` **12 处注入**（D1×4 + D2×4 + D3×4）
  → 全部精确红、逐字节还原 md5 一致、还原后复绿（39 passed）。

### 踩坑留档（本轮真事故）

- **探针 stdout 接管道 ⇒ BrokenPipe 打断 `restore_all()` ⇒ I8 注入留在源文件里**。
  后续 D3 改动是在「带着哨兵的工作区」上做的，直到下一次对照跑才暴露（对照红而非注入红）。
  已在探针加两道防线：① `main()` 开头 `assert_no_sentinel()`（带哨兵硬失败）；
  ② 注入循环包 `try/finally: restore_all()`。**调用侧也不要给探针接管道**（输出重定向到文件再读）。

## 任务 #8：GUI 节点树 + 四契约同步（2026-10-08 完成）

### 定位「四契约」的实锤

ADR 0018 §3 点名的四条，逐条实测后**与本仓实际不符**，以实测为准：

| 铁律条目 | 本仓实况 |
| --- | --- |
| `test_protocols` 总数 | **本仓无此文件**；对应面是 `tests/contract/test_gui_element_editor.py` 的字段表契约 |
| `i18n.js` | `src/rpa_core/devserver/static/i18n.js`（**本仓唯一一份**，非两份） |
| `test_field_tables_match_what_executors_actually_read` | 在 `tests/contract/test_gui_element_editor.py:127`，**真实拦截**（见下） |
| `test_extension_installer` | 未涉扩展版本，**不受影响** |

### 修掉的两个真 bug（本轮最有价值产出）

1. **编辑器静默数据丢失**：`compose_locator('uia', split_locator(loc))` 对带 `path`/`anchor`/`matchMode`
   的 locator 返回 `{'backend': 'uia', 'automationId': 'x'}` —— 结构字段**全被丢弃且不报错**。
   用户点一次「确定」，捕获回传的祖先链就没了，且没有任何提示。
   修法：`compose_locator(..., *, structured=None)` 透传结构字段 + `_compose()` 接入
   （`self._structured = structured_locator_values(locator)`）。往返等价判据见
   `test_editor_roundtrip_preserves_structured_locator_fields`。
2. **`inert_locator_keys` 反向误报**：把真被消费的 `path`/`anchor`/`matchMode` 报成「不消费的死字段」。
   修法：`known |= set(STRUCTURED_LOCATOR_KEYS)`（`matchMode` 因进了标量字段表自动消解）。

### 标量 vs 结构字段的分层（本片设计定稿）

`LOCATOR_FIELDS_BY_BACKEND` **只放标量字段**（值能摊成一个输入框一个字符串）；
`path`（层级列表）/ `anchor`（嵌套 locator）走新增的 `STRUCTURED_LOCATOR_KEYS`，
由专门控件（只读表格 / 只读说明）承载。为防 `STRUCTURED_LOCATOR_KEYS` 退化成
「想加什么就加什么的豁免名单」，配套 `test_structured_keys_are_actually_read`
（AST 扫执行器，每个结构键必须真被 `locator.<key>` 读过）。

`matchMode` 是两后端**唯一合法共享**的字段键（作用于 uia 的 `automationId`/`name`、
win32 的 `title`），故 `ALL_FIELD_KEYS` 改用 `dict.fromkeys` 去重。

### 改动清单

| 文件 | 改动 |
| --- | --- |
| `gui/element_editor.py` | `matchMode` 进两后端标量表；`STRUCTURED_LOCATOR_KEYS`；`ALL_FIELD_KEYS` 去重；`inert_locator_keys` 排除结构字段；新增 `structured_locator_values`；`compose_locator` 加 `structured` 参数；新增 `_build_locator_path_tree`（只读 4 列表格）/ `_build_anchor_view`（只读锚点说明） |
| `devserver/static/i18n.js` | 补 `anchor: "锚点"`（1 行） |
| `commands/desktop{,_win32}/findElement.json` | `input_schema.locator.properties` 补 `path`/`matchMode`/`anchor`；`errors` 补 `ANCHOR_NOT_FOUND`（各 2 行 diff） |
| `commands/desktop{,_win32}/{click,getText,input}.json` | `errors` 补 `ANCHOR_NOT_FOUND`（各 1 行） |
| `tests/contract/test_gui_element_editor.py` | 两表断言允许共享 `matchMode`；`offered |= STRUCTURED_LOCATOR_KEYS`；新增 `test_structured_keys_are_actually_read`；后端可见性判据改为「对面后端**独有**键才隐藏」；新增 5 条 M48 用例（含静默丢失钉子）= **59 项** |
| `tests/contract/test_editor_i18n.py` | 新增 `test_i18n_covers_desktop_locator_alias_fields`（事实源 = `DesktopLocator` 模型别名）+ `test_i18n_locator_labels_come_from_the_model_not_from_manifests`（钉住 manifest 侧天生不完整）= **6 项** |

### GUI 只读展示的取舍

D1 祖先链表格与 D3 锚点说明**都是只读**，本片不提供改写入口。理由写进 docstring：
`path` 是层级结构（要改得动就是一棵树编辑器，属独立切片）；`anchor` 是内嵌 locator
（要在确认框里再套一层定位器编辑器，交互与判据都未定）。**先把数据如实展示出来、
不丢**是本片的边界，改写入留在后续。

### 判据与负向验证

- 目标测试：`test_gui_element_editor.py` 59 + `test_editor_i18n.py` 6 + `test_desktop_locator_path.py` 39
  = **104 项全过**（零失败零错误）。
- 负向验证：探针扩到 **19 处注入**（I1–I12 原有 + I13–I19 本轮新增），
  **全部精确红、全部逐字节还原 md5 一致、还原后复绿**。
  - I13 GUI：uia 字段表删 `matchMode` → `50 passed / 5 failed`
  - I14 GUI：`STRUCTURED_LOCATOR_KEYS` 少登记 `path` → `51 / 4`
  - I15 GUI：`compose_locator` 丢掉 `structured` → `54 / 1`
  - I16 GUI：`inert_locator_keys` 不再排除结构字段 → `42 / 13`
  - I17 i18n：删 `anchor` 标签 → `5 / 1`
  - I18 i18n：删 `path` 标签 → `4 / 2`
  - I19 i18n：删 `matchMode` 标签 → `5 / 1`

### 本轮踩坑留档（三条，都是「假绿灯」家族）

1. **I17 首跑是假绿灯（`passed=4 failed=0`）——它暴露的是判据缺口，不是探针问题。**
   `test_i18n_fields_cover_required_input_schema_keys` 的遍历是 `required` → 该键的
   `properties` **一层**。桌面命令 `required == ["locator"]`，正好走进 `locator.properties`；
   而 `test_editor_i18n.py` 原本对比的是 `required` 里的**顶层键名** `"locator"`，
   所以 `locator` 内部字段一个都扫不到——删掉 `anchor` 标签门禁毫无反应。
   （我第一次把成因写成「文件里有两份字典」，**是错的**：全仓只有一份 `i18n.js`。
   实测 `_i18n_block("fields")` 有 82 键且含 `anchor` 才是事实。）→ 补模型侧判据。
   **教训：门禁条目「存在」不等于「覆盖到」；注入式负向验证是唯一能区分两者的手段。**
2. **探针把「非断言失败」判为失败，而我写的 I15 恰好会造成 `IndentationError`。**
   I15 的 `old` 只截到 `locator[key] = value`，替换成空串后紧邻的 `return locator`
   会跟 `for` 同级 → 语法错误而非断言失败 → 该注入**永远不会被当作有效命中**。
   修法：`old` 连带 `return locator` 一起截，`new` 保留 `return locator`。
3. **备份目录是旧轮次留下的，缺新增的 `editor` / `i18n` 两份。**
   首跑会在**注入完第一条之后**才在 `restore_all()` 撞 `FileNotFoundError`，
   把工作区留在脏状态。→ 新增 `refresh_backups()`（起手把干净源文件整体刷新进备份，
   刷新前先 `assert_no_sentinel()`），`restore_all()` 也改成备份缺失即硬失败。

## 残留（同片未完）

- GUI **改写**入口（祖先链增删改、锚点编辑）——本片只做到只读展示，属后续切片（M50）。
- `test_protocols`（ADR 0018 §3 点名）在本仓**不存在**，是 ADR 沿用了其它仓的措辞；
  已按实测改为「GUI 字段表契约」。ADR 侧已同步更正（见 ADR 0018 §3）。

## 真机复验（2026-10-08，抓到并修掉一个真 bug）

### 背景

本片此前**全部走替身元素**（`FakeElement` / `FakeInfo`），39 项契约测试全绿——但替身只能
证明「算法在我们填的键上对」，证明不了「真实 UIA 树吐出来的形状是什么」。故按
「先复验，再开 M50」的要求，写了真机探针 `.harness/spike/probe_m48_real.py`
（`RPA_DESKTOP_E2E=1`，起 WinForms 靶子程序）。

### 抓到的真 bug（首跑 11/13，2 条红）

**症状**：`path` 首级恒为「根窗口自身」，而 `_narrow_by_path` 规定「在候选的**后代**里找这一级」。
UIA 里**窗口不属于自己的后代**（实测 `window.descendants()` 中 `control_type == "Window"`
的个数 = **0**）⇒ **每一条真实 path 都在第一级收窄为空**、桌面元素定位恒失败
（`_find` 恒返回 0 命中）。契约测试 39 项全绿、替身形状下完全看不出。

**定因诊断**（真机探针）：`_step_matches(step0, wrap)` = True（窗口自身匹配得上该级）、
但 `window.descendants()` 里没有 `Window` 级 ⇒ 收窄出 0 个 scope。

### 修法定案（选择题 → 用户选 A）

| 选项 | 内容 | 结果 |
| --- | --- | --- |
| **A（选定）** | **产侧剥根级**：捕获侧不再生成根窗口那一级；执行器谓词保持「只看后代」 | ✅ |
| B（否决） | 放宽 `_narrow_by_path` 谓词为「含候选自身」 | 会让谓词对别的用法也变松，得不偿失 |

**A 的落地**：
- 新增 `capture/desktop_agent.py::_path_steps_from(chain, root_hwnd)`：按 `handle == root_hwnd`
  剥掉根窗口级；`_describe_info` / `capture_at` 两处调用点接入。
- `path` 语义随之**定稿为「根窗口**之下**到目标的容器级」**。
- `executors/desktop.py` / `desktop_win32.py` 的 `_narrow_by_path` **保持「只看后代」**
  （docstring 写明理由与定案日期）。

### 真机实测形状（新增事实）

| 目标 | 真实祖先链 | 剥根后 `path` |
| --- | --- | --- |
| `Submit` / `Count` 按钮 | `[Window]`（**WinForms 无中间 Pane**，直接挂 Form 下） | `[]` ⇒ **不写 path**（正确：根级不携带收窄信息） |
| `打开` 按钮（ComboBox 箭头） | `[Window, ComboBox(optionsCombo)]` | `[ComboBox/optionsCombo]` ✓ |
| 标题栏按钮（最小化/最大化/关闭） | `[Window, TitleBar]` | `[TitleBar]` ✓ |

### 探针断言的两处修正（都是我自己写错的判据）

1. 原探针拿 `Submit`（链只有 1 级）当靶子却断言「path 必须非空」——**断言与真实语义不符**。
   改为：多级链目标（`打开`）验「path 非空 + 不含根级 + 回放命中」；单级链目标（`Submit`）
   验「path 为空 ⇒ 不收窄 ⇒ 仍命中」。
2. 原探针断言 `path=[Window] 仍命中`——这条期望的是**旧（错误）语义**。改为
   `path=[Window]（指向根自身）⇒ 不命中（谓词未放宽）`，把修复后的正确行为钉住。

**修后真机复验 16/16 全部通过**。

### 契约测试侧的连带修正（真机暴露的替身缺陷）

`FakeElement` **只挂 `element_info` 却不转发四键**，而真机祖先链里的元素就是
`UIAElementInfo`（info 层级对象），`_locator_step_for` 直接读 `control_type` / `automation_id` /
`name` / `class_name` 四键 ⇒ 替身传进去恒返 `None`、`path` 恒为空却**不报错**。
修法：给 `FakeElement` 加四键转发（鸭子类型对齐真机）。
`tests/contract/test_desktop_locator_path.py` 原 4 条「选项 B 语义」钉子重写为「选项 A 语义」，
并新增 2 条（剥根级 / 只看后代）；现 **44 项**。

### 负向验证扩到 21 处

`probe_m48_negative.py` 新增 **I20**（agent：`_path_steps_from` 不再剥根级 ⇒ 精确红 2 项）、
**I21**（uia：`_narrow_by_path` 放宽成「自身或后代」⇒ 精确红 3 项，钉住「谓词没被放宽」）。
**21 处全部精确红、逐字节还原 md5 一致、还原后复绿（44 passed）。**

> I21 首版注入把 `for` 循环体缩进搞错 ⇒ 整个模块坏掉（`passed=0 failed=44`，
> 是收集/导入级失败而非「精确红」）⇒ 重写为**合法语法的精准放宽**。教训：
> 「精确红」要红在**针对性判据**上，把模块打倒不算命中。

## 收口结论（2026-10-08）

D1 + D2 + D3 + GUI 展示 + 四契约同步**全部落地**；真机复验 16/16 通过，
并据此修掉「path 首级残留根窗口」这个真 bug（选项 A：产侧剥根级）。
负向验证 **21 处**全绿。

### 门禁

- `check_all.py`：`1407 passed / 21 skipped / 2 xfailed`，**2 failed** 均为
  `test_gui_command_matrix.py` 的 QProcess 用例（`test_panel_streams_jsonl_into_rows` /
  `test_shutdown_kills_running_child`）——**环境缺陷挂账**（QProcess 探针实测
  `ProcessError.FailedToStart` / `pipe: 系统找不到指定的文件`，与本次改动无关，见
  §环境挂账）。故本轮**门禁末行不是 `FULL GATE PASSED`**（这 2 项是既有环境债，
  非本片引入）。
- **排除这 2 项挂账后全量绿**：`pytest --deselect <两条> -q` ⇒ 全点号、**退出码 0**、
  零 `F` 零 `E`（1407 passed 等价口径）。据此判定本片改动未影响其余任何用例。
- 桌面 e2e（`RPA_DESKTOP_E2E=1`）：首轮 1 failed（`test_windows_desktop_vertical_slice`），
  对照复跑绿 ⇒ **环境/焦点竞争噪声**（见 §环境挂账），非本片回归。
- 真机复验探针：**16/16 通过**。

**本片可收口**，残留两条均为后续切片（M50 GUI 改写入口）与 ADR 措辞更正。

## 环境挂账

### QProcess 在沙箱内起不了子进程（既有，非本片引入）

`FailedToStart` 立即失败（0.00s），`errorString = "pipe: 系统找不到指定的文件。"`。
被测行为就是 QProcess ⇒ **不换 subprocess 消红**。挂账，别查仓库代码。2 failed 恒存在。

### 真机 E2E 焦点竞争假红（本片实测确认）

**症状**：`test_windows_desktop_vertical_slice` 偶发 `failed`，伴随进程级
`Windows fatal exception: code 0x8001010d`（= `RPC_E_CANTCALLOUT_ININPUTSYNCCALL`：
输入同步调用期间不能发起跨进程 UIA 调用），崩溃栈在 pywinauto `uia_element_info._get_elements`。

**归因证据（三条，缺一不可）**：
1. 该工作流的 locator **完全不含 `path`**（用 `className`/`controlId`/`foundIndex`），
   而本片改动只在 `locator.path is not None` 时生效 ⇒ 路径无关；
2. 崩溃码指向「输入同步 + 跨进程调用」被拒 = 焦点竞争的典型机理；
3. **对照复跑绿**（同代码同用例，第二次通过）。

**处置**：记为环境挂账，**不动 e2e 代码**（与用户 2026-10-08 定案一致）。
真机 E2E 维持「缺省不进默认门禁」，需要时 `RPA_DESKTOP_E2E=1` 且**维护者不占机**。
