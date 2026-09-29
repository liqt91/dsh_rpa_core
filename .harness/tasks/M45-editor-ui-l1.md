# M45 编辑界面交互收尾（差距方案 L1 全部：A1–A4）

状态：`done`

由来：`docs/yingdao-gap-catchup.md` v2.1 §6 编辑界面专项快照排出的 **L1 交互层四项**
（纯交互/展示，无契约改动）一次性收口。M45 对应方案 §4 的「M45 交互收尾」里程碑，
做完后编辑界面与影刀的**观感差距清零**，剩余差距只剩「需要页面活着」（C1/C2/C3）
与「需要动契约」（D1/D2/D3）两档。

## 1. 分片实现

### A3 默认元素名统一生成器（三端一致）

- 方案：``el_{种类}``（种类 = tag / controlType 规整词干；空退 ``element``），
  撞名加序号 `_2`、`_3`…。两份实现（零构建双端没法真共享代码）：
  Python `element_editor.py::suggest_element_name` / JS `app.js::suggestElementName`
  （锚点 `[element-name-helpers:start/end]`）。
- 规整口径两侧同序：小写 → 去结尾 `control`（UIA 的 `ButtonControl`）→ 只留
  `[a-z0-9_]`。废弃 Web 侧 `{tag}_{时间戳}`（不可读不可复现，desktop 退化 `element_*`）。
- **共享用例表** `tests/contract/data/element_name_cases.json`：Python 侧
  `test_element_naming.py` 与 JS 侧 `scripts/check_element_name.mjs`（切片求值，
  进 `PURE_FUNCTION_SCRIPTS`）跑同一份数据——任一侧改动导致输出分叉，至少一侧红。
- GUI 接线：`app.py::_confirm_element_save` 把**既有元素名清单**传给生成器
  （只换函数不传清单的话，撞名保护形同虚设，连续采集会反复弹覆盖确认）。

### A2 M44 残留两处

- **R1 重启闪窗**：`_on_element_captured` 的 `showNormal`/`present_window` 从
  「无条件先还原」改为**收场才还原**——`recapture` / `save_and_continue` 两个出口
  保持主窗最小化（确认框本就 always-on-top，不依赖主窗还原；`_capture_element`
  自己会 `showMinimized`，已最小化时是 no-op）。
- **R2 提升不回读**：`_promote()` 改主 css 后立刻 `_sync_path_checks_from_css()`
  ——此前勾选态只在建树时推一次，提升后树停在旧勾选直到下次勾选才被重写。

### A1 Web 属性表（影刀编辑器右侧三列表的离线版）

- 纯函数：`attribute_rows(entry, fragment)`（一层展开成属性行，初态从**当前
  fragment** 反推：id 用了就勾、首类勾次类不勾、nth 用了就勾——这是「如实还原」，
  次类的勾选权正是属性表新增的颗粒度）+ `compile_fragment(entry, rows)`
  （编译规则：id 勾且「等于」→ 整层 `#id`（捕获口径）；否则 tag 恒在 + id 包含
  `[id*=…]` + class 等于 `.cls` / 包含 `[class*="…"]` + nth `:nth-of-type(n)`；
  包含值转义 `"` 与 `\`，与 content.js 的 `cssEscapeAttr` 同口径）。
- UI：`ElementEditorForm` 节点树下挂 QTableWidget（参与 | 属性 | 匹配方式 | 值），
  随树选中层切换，默认选中末级；tag 行锁定；值列只读（值来自捕获，改值等于发明
  不存在的属性——留给将来的活体联动）。任何编辑 → 重编译该层 fragment →
  更新树行文本 → 经既有 `itemChanged` 路径重写 css（**落盘口径唯一**，树/属性表
  只是「从捕获数据重新拼 css」的两个入口）。
- 不动执行器：消费方照旧 `querySelectorAll`。

### A4 元素库搜索/过滤

- `ElementPanel` 加搜索框：按名称/摘要（含定位串）过滤**显示**（大小写不敏感），
  清空恢复全部；`set_elements` 存全量、渲染走过滤；不动元素资产。

## 2. 判据

- A3：`test_element_naming.py` 4 项（共享用例表 / JS 接线 / GUI 接线 / 规整边界）
  + `check_element_name.mjs`（14 用例切片求值 + 2 接线断言，进 `PURE_FUNCTION_SCRIPTS`）。
- A2：`test_capture_restart_paths_never_restore_main_window`（**次序断言**——记录
  `showNormal` 调用时刻，重启完成前不得出现；只看终态抓不到闪烁，旧实现终态也是
  minimized）+ 对照组「保存收工要还原」（防判据过泛化）；
  `test_path_tree_resyncs_checks_after_candidate_promotion`（后缀候选与非路径候选
  两个方向的回读）。
- A1：纯函数 2 项（初态反推 / 编译规则含转义）+ 表单级 2 项（改匹配方式重写 css /
  随树选中切换且不牵连末级 / tag 锁定）。
- A4：`test_element_panel_search_filters_display_only`（名称/摘要/大小写/清空）。

## 3. 负向验证

- A3 双侧注入：JS `ordinal=3` → node 门禁 5 FAIL 精确红；Python 去 `control$` 剥离
  → `test_python_side_matches_shared_case_table` + `test_normalize_element_hint_edge_cases`
  2 failed 精确红；均逐字节还原（备份 md5 + INJECTED 标记检查）复绿。
- A2 双注入：无条件还原主窗（R1 旧行为）→ 次序判据红；删 `_sync_path_checks_from_css()`
  调用 → R2 判据红；还原后复绿。
- A1 双注入：`[class*=` 改 `[class^=`（编译错）→ 编译判据红；`_on_attr_edited`
  不回写 fragment（接线断）→ 两个表单级判据红；还原复绿。
- A4 注入：过滤条件恒假（不过滤）→ 搜索判据红；还原复绿。

## 4. 验证

- 受影响契约测试合跑：**115 passed / exit 0**（naming 4 + editor 41 + capture 24 +
  panels 34 + extension 12）。
- ruff `All checks passed!`；12 个 `.mjs` 门禁全过（含新增 `check_element_name.mjs`）。

## 5. 残留（登记）

- 桌面属性表的「匹配方式」列（D2）与桌面节点树（D1）仍空——契约层，M48。
- 属性表值列只读（改值=发明不存在的属性；等 C1 会话保活后可做「改+活体试」）。
- 帮助按钮未做（纯搭车项，可随任意一片带上）。
- 已知风险（待真机确认）：连续采集路径不再还原主窗，确认框靠 always-on-top 展示
  ——若真机上确认框被压在别的窗口后面，改回「还原但去掉 present_window(self)」。
