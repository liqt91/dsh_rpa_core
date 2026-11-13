# M72：让 elementRefs 在网页端可见、可解除

Status: `done`

> **2026-11-13 立项**。维护者实测反馈：「删除元素，提示第三步引用它，但是第三步的
> selector 是空的，且没有找到其他设置项引用这个元素」。

## 一、取证：不是误报，是引用被界面藏了

只读扫 `apps/xhs-workbench/workflows/xhs_product_search.json`：`typeKeyword`
（第 3 步，`browser.input`）有 `"elementRefs": {"selector": "xhs_search_box"}`，
而 `with.selector` **不存在**。

按 ADR 002 / ref-only 契约，被引用参数本来就不进 `with`，运行期从 `elements/` 解析；
所以参数面板显示 selector「未设置 / 必填但当前未设置」是**正常形态**，删除护栏拦得对。
真正的缺陷是两层呈现：

| 层 | 现状 | 后果 |
| --- | --- | --- |
| 节点参数面板 | 只渲染 `with`，`node.elementRefs` 完全不显示 | 必填字段假红、用户找不到引用 |
| 删除说明框 | `referencedBy: ["typeKeyword"]` → 只显示「第 3 步」 | 不给字段名/资产名，也没有去处理的入口 |

## 二、落地

**api.py（唯一权威 + 全量写回）**

- `_all_element_refs(path)` 扫 `_NESTED` 四分支，逐条返回
  `{nodeId,param,name,command}`（不去重：同节点两字段引用同一资产要显示两处）；
  `_element_references(path,name)` 从它 filter；`elementDetail` / `deleteElement` 的
  `referencedBy` 统一改成结构化数组，删除护栏语义不变。
- `saveNodeParams` 新增可选 `element_refs`（全量）：`None` = 不碰引用，
  `{}` = 清空该节点全部引用；值走 `_valid_element_name` 校验，非法不落盘。

**main.js / style.css（网页端）**

- `selectCard` 读 `res.node.elementRefs` 存 `nodeRefs`；表单顶部新增引用面板，
  每行 `param → 资产名` + 可撤销的「解除引用」；标记进 `pendingUnbinds`，
  由 `isDirty()` 算脏，随「保存参数」以 `element_refs` 全量提交。
- 被引用字段不再给空输入框 +「必填但当前未设置」，改只读说明「已引用元素「x」」
  + 隐藏 input（保证 `currentValues()` 全量提交不触发 `INCOMPLETE_SUBMIT`）；
  解除待生效时退回普通控件，方便补值。
- 删除说明框每条引用列「第 N 步 · 参数 → 资产名」，点击关框并 `selectCard`
  跳到该节点；详情弹框同样逐条摊开。Esc / 取消 / 关闭仍都能退。

## 三、判据（改判据后重做负向验证）

| 判据 | 结果 |
| --- | --- |
| `.harness/scripts/check_element_pick_contract.py` | 20/20（+结构化引用、+解除写回） |
| `scripts/check_element_library.mjs` | 12/12（+引用行可点跳转、+参数表面板/解除提交） |
| `scripts/check_element_pick_ui.mjs` | 24/24（+引用结构、+引用可视化接线） |
| `apps/xhs-workbench/tests/test_editor_element_library.py` | 26 测（+解除后删除、夹具改合法 AST） |
| `.harness/spike/negative_element_library.py` | 22/22（20 注入 + 2 元验证，四条 gate 全等断言） |
| 完整门禁 | `FULL GATE PASSED`（797 passed / 20 skipped / 2 xfailed） |

★ 首轮负向 N18 暴露一条**恒绿判据**：`region.includes('"param"')` 被同域的排序键
`ref["param"]` 喂饱，产出里删掉 param 字段照样绿 ⇒ 改成精确产出锚点。另两条连带红
有因果（排序键 `KeyError` 让所有引用扫描 case 一起红），按推理补进期望集，
不是照抄实际红灯。

★ 全门禁首跑撞 `test_retry_backoff_cannot_exceed_workflow_deadline` 假红：沙箱把
`tempfile.gettempdir()` 指到仓库根，50ms 预算写盘必超；用
`PYTEST_ADDOPTS=--basetemp=<系统temp>` 复跑即绿，不是本轮回归。

## 四、欠账 / 边界

- 真机（PPX 窗口里点删除 → 跳节点 → 解除 → 保存 → 再删）未占机复验；行为判据跑的是
  DOM shim，服务端写回有真文件 case。详见 BACKLOG。
- 目前只能「解除引用」，没有「改绑到另一条资产」的下拉入口；解除后该参数需要自己填值
  或重新捕获，编译会如实报缺参数，不替用户猜选择器。
- kit 边界不变：捕获/编辑之外的逻辑仍在网页端，服务端护栏只防绕过界面直调 RPC。
