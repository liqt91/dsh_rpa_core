# M104 — 详情勾选状态持久化与最大化窗口保持

Status: `wip`

## 背景

维护者现场（2026-11-19）：

1. 网页元素详情打开后，层级/属性**全没勾**；桌面元素保存时取消勾选的层级，重开详情直接丢失；
2. 详情里改完保存，原本最大化的 RPA 主窗缩成普通大小。

## 根因

两条都不是「界面画错了」，而是状态没跨过保存边界：

- 网页 `apply_selector()` 只把新的 css 写进 `selector.css`，不回写 `path[i].inCss` 与
  class 勾选。重开时 `levels_from_path()` 只能从捕获期的 `inCss` / 首 class 默认勾去猜。
- 桌面 `compose_selector()` 把未勾选层级从 `locator.path` 里**直接删掉**。盘上不存在了，
  重开时 `nodes_from_descriptor()` 自然画不出来。
- pywebview(WinForms) 的 `restore()` 把 `WindowState` 设为 Normal：捕获/编辑前主窗若是
  最大化，结束后就被还原成普通大小。

## 方案（维护者拍板：加 editState 快照）

`selector.editState` 存编辑态勾选快照：网页 `{"levels": [...]}`，桌面 `{"nodes": [...]}`。
旧资产没有这个键时回退现有推断，开发期硬切不做迁移。

- 写侧 `apply_selector(descriptor, text, edit_state)`：定位文本与快照**同一次**落盘；
- 读侧 `build_edit_model()` 优先用快照；网页快照层数与 path 不符时宁可回退推断，
  避免勾选错位到别的层；
- win32 `ElementEditDialog._try_save` 把 `{"levels"|"nodes": ...}` 作为回调第三参交出；
  `CaptureController._commit_from_dialog` 与 Web `confirmElemDialog` 同步透传；
- `MaximizedAwareHost`（`rpa_core/capture_kit/window_host.py`）包装宿主：`minimize()` 前
  探测最大化状态，`restore()` 时按原状态选 `maximize()` / `restore()`，状态用后清零。
  原生探针由 ppx 侧 `_webview_is_maximized()` 提供（拿不到就 False，不阻塞捕获）。

## 判据

- 契约：网页快照往返 / 旧资产回退 / 层数不符回退 / 桌面未勾层级保留（4 条）；
  窗口适配：最大化还原、普通窗保持、状态不跨轮泄漏（3 条）；
- JS：`check_element_library.mjs` 捕获确认保存必须把 `edit_state.levels` 交给 RPC；
- `check_element_pick_ui.mjs` 的保存接线判据改为**行级等值**，钉住三参透传；
- 负向 `probe_m104_negative.py` 六个注入：写侧、网页读侧、桌面读侧、前端接线、
  确认框接线、窗口最大化分支，逐项断言失败集相等 + md5 逐字节还原；
- `npm run build` 重建 dist；全量 1083 passed / FULL GATE PASSED。

## 待真机确认

重载 ppx（api.py 与前端都改了），打开一个网页元素详情看层级/class 是否按保存时勾选；
桌面元素取消某层级勾选后保存、重开应仍在树里且仍未勾。最大化窗口在详情保存后应保持最大化。
