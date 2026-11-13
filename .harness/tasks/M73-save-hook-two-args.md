# M73：捕获确认框「保存」接线的双参数回归

Status: `done`

> **2026-11-13 立项**。维护者实测反馈（截图）：「保存失败，提示
> 入库失败：start capture.<locals>.<lambda>() takes 1 positional argument but 2 were given」。

## 一、取证：接线停在 M69 之前

- `src/rpa_core/capture_kit/controller.py::_commit_from_dialog`（M69 起）
  调 `self._on_commit(name, selector)` —— **两个参数**；
  `edit_dialog.py::_try_save` 也用 `self._on_save(name, selector)`；
  单元测 `test_edit_dialog_commit_reports_committed_with_name` 断言收到
  `("搜索框", "#q")`，kit 侧一直是两参。
- `.harness/demo/ppx_probe/app/api/api.py::_commit_from_dialog(entry, name, selector)`
  也是三参（entry 是闭包捕获），会先校验定位、再把编辑后的 selector 写回 doc 才落盘。
- 唯独 `start_capture` 的接线还是
  `on_commit=lambda name: _commit_from_dialog(entry, name)` —— **单参数**。
  M69 升双形态编辑器时改了控制器与目标函数，漏了这一处注入点。
- 因此点「保存」时控制器传两参 → lambda 抛 `TypeError` → 被 controller
  `except` 兜成 `f"入库失败：{exc}"` 回显在框里，盘上什么都没写。

## 二、为什么门禁一路全绿（★ 恒绿判据）

`check_element_pick_contract.py::case_capture_needs_no_name` 只验了
`for hook in ("edit_dialog_factory", "edit_model", "on_commit"): hook in seen["kwargs"]`
—— **只断言 key 存在，从没真调过这个回调**。回调签名错了、甚至回调本身
是个常量，这条都照样绿。`check_element_pick_ui.mjs` 的 `startRegion` 也只看
「startCapture 里有没有落盘」，不看接线形状。

这正是「覆盖实现 ≠ 覆盖用户实际走的路」「纯函数层绿 ≠ 接线层绿」的又一例：
两端单元测各自都对，坏的是把它们接起来的那一行。

## 三、落地

1. `api.py` 接线改为
   `on_commit=lambda name, selector: _commit_from_dialog(entry, name, selector)`。
   不能只把签名改成 `lambda name, selector: _commit_from_dialog(entry, name)`：
   那会静默丢掉 selector，用户在层级编辑器里改的勾选/属性不落盘。
2. **动态契约**新增 `case_capture_commit_wires_selector`：打桩 `CaptureController`
   截获 `start_capture` 真正传进去的 `on_commit`，先 `_hold_captured` 造暂存描述符，
   再真调 `hook("搜索框", "#edited")`，断言①不抛异常②返回 None（没被校验拦）
   ③ `elements/搜索框.json` 的 `selector.css == "#edited"`。
   单参数回归在这里直接 TypeError；丢 selector 时盘上仍是 `#original`，同样红。
3. **静态判据** `check_element_pick_ui.mjs` 新增行级等值断言，钉住整行形状
   `on_commit=lambda name, selector: _commit_from_dialog(entry, name, selector),`。
   不用「源码里出现过 on_commit」那种会被自身喂饱的写法。

## 四、负向验证（25/25，逐字节还原）

- `N21` 接线退回单参数 ⇒ 期望 `pick_ui={UI_COMMIT_WIRE}`、
  `contract={PY_COMMIT_WIRE}`，其余三条判据不红。
- `N22` 收两参但用 `_selector_edit_text(entry["captured"])` 顶掉用户编辑值 ⇒
  合法语法、不抛异常、照样能保存，只有「核对落盘 css」抓得住；两层同红。
- `M3` 元验证：注入 N21 的同时放宽「静态行级断言 + 动态 case 注册」两条新判据
  ⇒ 四条 gate 全绿，证明那两条红确实是新判据报的，不是别的用例误伤。
- 期望集按推理补全、不照抄实际红；脚本开头扫哨兵、`try/finally` 还原、核 sha256。

## 五、验证

`contract 21/21`、`pick_ui 25/25`、kit + 元素库单测 48 passed、
`probe_element_library` 10/10、负向 25/25、`FULL GATE PASSED`（797 passed）。
首跑用 `PYTEST_ADDOPTS=--basetemp=<系统temp>` 绕开已知计时假红。

## 六、欠账（另立，本轮未动）

**只读取证发现**：层级编辑器保存只把 compose 结果写进 `selector.css`，
不回写 `selector.path` 各层的 `inCss` / 属性值；下次打开详情框时
`levels_from_path(path)` 用旧 path 重建层级，用户改过的勾选与属性看不见
（运行期定位用的是 css，所以不影响执行，只影响编辑态复现）。
改这个要动「编辑态真值源」的既有设计取向，按约定先给取舍再动手，
已登记 `BACKLOG.md`。真机保存链路（点保存 → 落盘 → 列表刷新）未占机复验。
