# M36 门禁测试泄漏全局输入：clipboard 用例往维护者前台粘贴 "hi"

状态：`done`

关联：M30 S2（引入肇事用例）、`tests/conftest.py`、`tests/contract/test_desktop_params.py`、
`executors/desktop.py`（clipboard 模式实现）

## 0. 现场

维护者报告（2026-09-21）：「今天老是在不同的输入框莫名其妙输入 hi，是不是哪个门禁
测试导致的」。

## 1. 根因（已实证）

`desktop.input` 的 clipboard 模式实现（`executors/desktop.py:472-486`）是
「`element.set_focus()` → 写系统剪贴板 → `pywinauto.keyboard.send_keys("^v")`」。
`send_keys` 走 SendInput，是**全局键击**——落到当时前台聚焦的任何窗口。

M30 S2 的 `test_input_command_shares_the_same_wait_semantics` 只桩了 `_find`
（返回 MagicMock 元素），**没桩剪贴板写入与 Ctrl+V**：

1. `element.set_focus()` 是 MagicMock → 什么都不做，焦点仍在维护者当前窗口；
2. `win32clipboard` 真实清空并写入 `"hi"`（顺带劫持剪贴板）；
3. `send_keys("^v")` 真实发送 → "hi" 粘贴进维护者当时聚焦的输入框。

该用例随 M30（`ac2cc93`，当日）进默认套件，**每次全量跑套件必触发一次**：
M30/M32/M34/M35 的门禁 + 当日独立 pytest 共 6+ 次 → 6+ 次 "hi" 粘贴与剪贴板劫持，
与「老是、不同输入框」完全吻合（贴到哪取决于当时焦点在哪）。

对照组：M30 S3 的修饰键用例**特意**打桩了 `send_keys` 并写明原因（「测试不该动
用户的键盘」，`test_desktop_click_plan.py:562`）——同一批工作里立了规矩，S2 的
用例漏了执行。**教训：打桩边界必须沿「真实副作用面」划，不能沿「参数传递面」划**；
桩住了元素查找不等于桩住了元素之外的全局动作。

## 2. 修复（两层）

- **肇事用例**（`test_desktop_params.py`）：剪贴板四函数与 `send_keys` 全部打桩
  为记录型；断言改为「调度了写入 `hi` 与一次 `^v`」——意图不变，真实粘贴效果
  归 `RPA_DESKTOP_E2E=1` 的桌面 E2E（不冒充真机结论）。
- **结构性防线**（`tests/conftest.py` autouse `_block_global_input`）：默认测试进程
  禁止合成全局输入——`pywinauto.keyboard.send_keys`、`desktop_win32.send_keys`
  （模块级 from-import 的早绑定名，必须单独钉）、`win32clipboard` 四个写入口，
  一律改为报错，报错信息指明出路。Windows-only；`RPA_DESKTOP_E2E=1` 时不安装
  （E2E 真机输入合法）；用例内自有 monkeypatch 晚于 autouse 生效，LIFO 覆盖不冲突。

## 3. 负向验证

- [x] 探针测试直接调真实 `pywinauto.keyboard.send_keys("^v")` → 守卫立即拦红
      （AssertionError 带处置指引），探针已删。
- [x] 桌面契约三件套（params/click_plan/attach_window）全绿。

## 4. 备注

- `executors/desktop.py` / `desktop_win32.py` 的产品实现**不动**：真实场景下
  「聚焦目标控件再粘贴」是正确行为；缺陷在测试越界，不在功能。
- 维护者侧后果：当日剪贴板被反复清写成 "hi"（剪贴板历史如有留存可自行清理）。
- FULL GATE PASSED（1069 passed / 2 xfailed / 12 skipped，1083 项；用例数与 M35
  持平——本片只给既有用例打桩 + 加 conftest 守卫，未增删用例）。
