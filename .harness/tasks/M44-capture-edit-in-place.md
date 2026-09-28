# M44 捕获即编辑（对齐影刀元素编辑器的 P1 一整片）

状态：`done`

由来：维护者给出影刀**真实界面截图**（桌面元素编辑 / Web 元素编辑各一张）并问
「影刀的元素捕获后，编辑界面比我们丰富很多……以上我们能做吗」。M39 §9 已做过
15 项逐控件判定矩阵与 P1–P5 分期；维护者选定 **「P1 一整片」** 开工。

**P1 的边界（与维护者确认过）**：不动执行器契约。locator 语义（层级化、控件级匹配方式）
属 P2；会话保活（活体校验/预览的地基）属 P3。

## 0. 一句话结论

差距有一半在**流程位置**，不在控件数量。影刀是「捕获即编辑」（控件与捕获同一窗口），
我方是「捕获确认框 → 保存 → 元素库点『编辑』」两步——所以 P1 的主体不是加控件，
而是**把确认框升级成编辑器本体**（复用同一份 `ElementEditorForm`），再补齐影刀同款的
两个流程出口（保存并继续 / 重新捕获）与 Web 节点树、网页侧 Ctrl 光标变蓝。

## 1. 分步实现（S1–S6）

### S1 content.js：Ctrl 光标变蓝 + 祖先链回传

- **光标**：纯函数 `cursorKeyOf`（Control→ctrl / Meta→meta，其余 null）+
  `cursorStyleText`（`* { cursor: url(data:image/svg+xml,…2F6BFF…) 4 2, auto !important; }`，
  蓝色 `#2F6BFF` 箭头带白描边）+ DOM 开关 `showCaptureCursor`/`clearCaptureCursor`。
  **`clearCaptureCursor` 先用自己的引用移除、再按 id 兜底**——第一版只靠
  `getElementById` 兜底，查询不可用就移不掉（判据逼出来的真缺陷）。接线四处：
  `onKey`（armed 且 Ctrl/⌘ 按下→注入；Esc→清）、新增 `onKeyUp`（无条件清）、
  `capture()` 成功路径、`onRuntimeMessage` 撤防分支、`teardown()`。
  ⌘（Meta）与 Ctrl 同口径：捕获手势在 macOS 上的等价修饰键就是它。
- **祖先链**：`cssSelectorFor` 重构为 `pathFor(el).map(e => e.fragment).join(" > ")`。
  `pathEntryFor` 每级产 `{tag, id, classes, nthOfType, fragment}`；上限 6 级
  （`MAX_PATH_DEPTH`）。**逐字节产出不变**（`check_capture_helpers.mjs` 钉住），
  只是拆出可展示的中间结构。回传 `selector: { css, path: pathFor(el), candidates }`。
  节点树上的路径与真正下发执行的选择器**同源**（都出自 `pathFor`）——这是
  「一处生成、两处复用」，不是两套口径。
- **模型**：`_path_errors`——`selector.path` 可选（加法兼容，老文档不受牵连），
  出现则每级需非空 `tag`/`fragment`。**必须放 `selector` 内部**：顶层未知键被
  pydantic `extra="ignore"` 静默丢弃。

### S2 抽取 `ElementEditorForm`（GUI 重构，行为不变）

`element_editor.py` 的编辑逻辑（browser 主 css + 候选提升 / desktop locator 字段勾选 /
就地校验）抽成独立 `QWidget`（带 `changed` Signal / `blocked` / `problems()` /
`revalidate()` / `result_document()`）；`ElementEditorDialog` 变薄壳，
`__getattr__` 转发 ⇒ **27 项既有判据零改动**。这一步是 S3 的前置：确认框要复用的
是**同一份实现**，不是再抄一份（两套口径必然漂移）。

### S3 `ElementDialog` 升级为捕获即编辑

- 删掉 `selector_edit` 文本框（browser 一行 css / desktop 一行 locator JSON 的旧
  编辑面），改为内嵌 `ElementEditorForm`；只读候选块删除（其展示职能归编辑区的
  候选列表，点一下即提升为主定位）。
- `accept()` 只判「元素名非空」+ `form.revalidate()`/`form.blocked`；selector 合法性
  **不在这里判**——模型判据是唯一权威，确认框自己再判一遍就是第二套规则。
- 判据改写 9 条（全部是「正测让用户看一行 JSON」的旧接口判据，属换接口连带）；
  其中 `hides_candidates_section_when_absent` 改名为
  `candidates_section_is_browser_only`，口径反而更硬：browser 收集 0 条 → 明说
  「没有收集到」；desktop → **连控件都没建**（`hasattr` 为 False）。

### S4 app 接线：保存并继续 + 重新捕获

- `ElementDialog` 三个出口：**保存 / 保存并继续 / 重新捕获**（外加取消），
  `intent()` 区分。前两个走同一套校验（都落盘），「重新捕获」**不校验**
  （本次不落盘，校验没有对象——连空元素名都放行）。
- **意图与关窗方式分开**（`_close_with(intent)`）：QDialogButtonBox 的 accepted
  信号不带来源；两个新按钮用 **ActionRole** 是故意的——AcceptRole 会被自动接到
  accepted → `accept()`，「重新捕获」就被迫校验，把用户困在对话框里。
- `_confirm_element_save` 返回形状改为 **`(意图, 载荷)`**：三个出口里有两个都产出
  文档，只回文档分不清「存完收工」还是「存完再来一个」。
  **保留方法名、只改返回形状**：有 4 处测试把 `_confirm_element_save` 打桩来避免
  真模态对话框（M40 的挂住事故），改名会让桩静默失效 ⇒ 弹真对话框 ⇒ **挂住**；
  形状不匹配只会得到干净的 `TypeError`/`ValueError`。
- `_on_element_captured`：`recapture` → 复位会话后直接 `_capture_element()`；
  `save_and_continue` → 落盘 + 刷新元素库后 `_capture_element()`。此刻
  `_capture_session` 已复位为 None，不会撞上「已有捕获 → 取消」分支；迟到结果
  守卫（会话一对一）不变。

### S5 Web 节点树（勾层级 → 拼回 css）

`ElementEditorForm._build_browser` 末尾建 `path_list`（QListWidget 可勾项，
每行 = 一级 `fragment`，根→目标）：

- **单向**（树 → 主选择器）：`css_edit` 是唯一落盘口径。不做双向同步是有意的——
  把 css 解析回层级不可靠（手改的 / 候选提升来的 css 都不出自这条路径），硬做
  双向就是立第二套口径。树只承诺一件事：**再次勾选，就按所选层级重写主选择器**；
  两个入口写同一个字段，后动手的赢。
- **建树时按 css 反推一次勾选态**（css 是路径**后缀** → 只勾后缀；匹配不到则全勾）。
  没有这一步，用户上一轮截短的祖先会在这一轮被随手一次勾选覆盖回全路径。
- **末级（目标本身）不可取消**：勾了会立刻被勾回——「不指向目标的定位方案」不成立。
- 老元素没有 `path`：不建树也不摆空壳。`path` 经 `_extras` 原样落盘（写回不抹）。

### S6 判据 + 负向验证 + 台账

- 新增判据：`test_element_descriptor.py` path 校验 11 项（含 desktop 不套用 browser
  规则）；`test_gui_element_editor.py` 节点树 5 项；`test_gui_panels.py` 出口 3 项 +
  旧判据改写 9 条；`test_gui_capture.py` 重启 2 项 + 旧桩形状改 4 处 + 「保存不重启」
  反向断言 1 条。node 门禁：`check_capture_lifecycle.mjs` 场景 11（光标 12 项）+
  场景 12（path 3 项），`EXPECTED_LISTENERS` 8→9；`check_capture_helpers.mjs` +6 项。
- **负向验证 10/10**（探针 `.harness/spike/probe_m44_negative.py`，本仓标准流程：
  对照绿 → 字节级注入（锚点必须恰好出现 1 次）→ 必须 `N failed` 且**无** `N error`
  / node 门禁必须命中**指定的那条**检查名 → 逐字节还原核 md5 → 全部还原后再对照全绿）。
  10 处：S1 三处（keyup 监听 / 丢 path / cursorKeyOf 认错键）+ S1 模型一处
  （`_path_errors` 短路 → 6 failed）+ S3 两处（重新捕获被改成要校验 / 意图记成保存）+
  S4 两处（两个重启分支各自短路）+ S5 两处（末级不勾回 / 建树不反推）。
  **无一处挂住**（S4 的破坏路径会走到 `name, document = payload` 解包 None，
  但测试断言先红，不会推进到真模态对话框）。
- 全量门禁真值见 §3。

## 2. 踩坑记录

1. **`QDialogButtonBox.buttonRole` 在 PySide6 里是实例方法**（C++ 侧是 static），
   必须**从该按钮所属的按钮盒**上问；拿另一个对话框的盒子查返回 `InvalidRole`，
   看着像「角色接错了」——其实是测试查错了对象。为此在 `ElementDialog` 上留了
   `button_box` 引用。
2. **`_close_with` 与校验分离**：三个出口若都靠 `accept()` 收尾，调用方分不清来源；
   若都走校验，「重新捕获」被无谓拦截。
3. **状态栏断言站不住**：「已保存元素 x，继续捕获…」会被新一轮 `_capture_element`
   的「捕获中…」即时覆盖——被覆盖本身就是重启生效的表现，改断言会话对象而不是文案。

## 3. 验收（真值）

- `tests/contract/test_gui_panels.py` + `test_gui_capture.py` +
  `test_editor_element_display.py` + `test_gui_element_editor.py` + `test_gui_home.py`：
  **104 passed**（offscreen，`-o addopts= -q -p no:randomly`）。
- `tests/unit/test_element_descriptor.py`：**26 passed**（含 path 新增 11 项）。
- node 门禁独立跑：`check_capture_lifecycle.mjs` 全 PASS（含 S11 光标 12 项 +
  S12 path 3 项）、`check_capture_helpers.mjs` 全 PASS。
- ruff：`All checks passed!`。
- 全量 `check_all.py`：见 PROGRESS 当日条目（末行 `FULL GATE PASSED` + 退出码）。

## 4. 刻意不做（P1 边界，都有去处）

- **桌面节点树 / 控件级匹配方式**：动执行器契约（locator 是扁平字段、无层级），
  P2。
- **活体校验 / 预览 / 相似元素**：需要「会话在对话框期间保持 arm」的生命周期改造
  （`work()` 的 `finally: session.close()` 在对话框弹出前就撤了防），P3。
- **桌面 Ctrl 光标变蓝**：系统级 `SetSystemCursor`（四条恢复路径），P4。
- **锚点 / AI 智能定位 / XPath**：P5。
- **实时页面树**（重开页面再扫 DOM）：需要活通道，见 P3；离线树（勾选-重拼）
  已不缺。

## 5. 残留（登记不修）

- `ElementDialog` 的「重新捕获」重启时主窗会闪一下（先 `showNormal` 弹对话框、
  确认后再最小化）：两个出口都要先还原主窗才能弹对话框，闪烁不可避免；
  若要消除需把对话框挪到捕获会话内弹出（P3 的会话保活一并解决）。
- 树的勾选态在「候选提升」后不回读（css 不出自路径，反推退化为全勾）：
  下一次勾选才重写，行为已在判据里钉住（`test_path_tree_derives_checks_from_shortened_css`
  第二段）。
