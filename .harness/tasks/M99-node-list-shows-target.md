# M99 — 桌面编辑框「层级展示不全」（16 层只显示前 10 行）

Status: `done`

## 背景

用户重新捕获微信搜索框（元素名「重新捕获」）后反馈：层级数量不少、目标已经不是
微信主窗口，但编辑框里数不出 14 级，怀疑展示不全。

## 取证

- 落盘 `examples/api-usage/elements/重新捕获.json`：`metadata.rect = 179x19`，
  `locator.path` **14 级**，末级 `Edit / name=搜索 / mmui::XValidatorTextEdit`
  ⇒ 捕获粒度正确，问题不在数据侧。
- `desktop_editing.nodes_from_descriptor()` 对该资产产出 **16** 条节点
  （1 窗口 + 14 层 path + 1 目标），数据是全的。
- `edit_dialog._build_desktop_children()` 只建 `_NODE_ROWS = 10` 行的 checkbox，
  其余靠 `_layout_nodes()` 按 `_node_scroll` 重排；而 `_setup_node_scroll()`
  **把 `_node_scroll` 写死成 0**，`_selected_node` 也是 0。
- 后果：一开框看到的是最上面 10 条，前缀全是无名容器
  （`Group` / `Custom` / `mmui::XView`），目标那行（第 15 条）在窗口外，
  属性面板显示的还是窗口节点的只读属性。

## 改动

`src/rpa_core/capture_kit/edit_dialog.py`

- 新增 `_initial_node_view()`：返回 `(选中行, 滚动偏移)`，取最后一个
  `selected` 的节点（没有则取末条），并把偏移夹到「让该行可见」的位置。
- 新增纯函数 `_clamp_node_scroll(scroll, total)`：夹进
  `[0, total - _NODE_ROWS]`，`_initial_node_view` 与 `_setup_node_scroll` 共用。
- `_setup_node_scroll()` 不再清零偏移，只负责夹取 + 同步 `SIF_POS`。
- 层级标题改为 `层级（勾选参与定位）· 共 N 层，可滚动`：不报总数时，
  用户看到一屏 `(无名)` 会以为层级抓漏。

## 判据

`tests/unit/test_capture_kit.py`（新增 10 条，共 33 条）：

| 用例 | 守什么 |
|---|---|
| `test_node_view_opens_on_target_when_chain_exceeds_screen` | 16 层时目标落在可视区内 |
| `test_node_view_keeps_top_when_chain_fits_screen` | 不超一屏时不平白滚动 |
| `test_node_view_falls_back_to_last_when_nothing_selected` | 无 selected 时按末条处理 |
| `test_node_view_of_empty_chain` | 空链返回 (0, 0) |
| `test_clamp_node_scroll[5 组]` | 上下界夹取 |
| `test_setup_node_scroll_keeps_initial_offset` | ★ 接线：建滚动条时不许把偏移清零，且选中行指向目标、`SIF_POS` 同步 |

可见性断言写的是 `scroll <= row < scroll + _NODE_ROWS`，不写死具体数字。

## 负向验证

`.harness/spike/probe_m99_negative.py`，5 个注入、失败集逐项相等、md5 还原、复跑绿：

| 注入 | 失败集 |
|---|---|
| `initial-view-fixed-top` | 3 条（可见性 2 + 接线 1） |
| `return-first-row` | 4 条（含接线） |
| `clamp-no-upper` | 2 条参数化用例 |
| `clamp-no-lower` | 2 条 |
| `setup-resets-zero` | 1 条（接线） |

两处期望集第一次写错、按实际逐条核对后修正：`target = 0` 那种注入会被后面的
「按 selected 回扫」覆盖，等于没生效 —— 换成 `return 0, scroll` 才真正打到；
接线用例原本没断言选中行，补上后 `return-first-row` 才红。

## 门禁

`check_all.py` → `FULL GATE PASSED` + `rc=0`；改动文件 ruff 全绿。

## 遗留（未在本单处理）

1. `accessibility.poke_provider()` 的 `SendMessageTimeoutW` **不检查返回值**，
   实测对微信返回 `拒绝访问`（错误 5），日志却记 `ok=true` —— 唤醒里这一步
   一直静默失败，需要单独修并加判据。
2. 微信 4.2.0.13 的 UIA 树是「进程首次被 UIA 查询时按 gate 决定」：一旦在
   gate=0 时被查询过，写 gate / 重发 `WM_GETOBJECT` / 重建窗口都救不回来
   （本轮逐项实测）。窗口关掉再打开后校验不到，属于这一类，靠 UIA 侧修不了，
   要么在启动瞬间抢写 gate，要么走图像兜底。
