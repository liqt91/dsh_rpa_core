# M97 — 悬停框到搜索框、按捕获却是整个微信窗口（粒度来源分叉）

Status: `done`

## 背景

M96 加了「DFS 只拿到整窗 → 回退屏幕级」的兜底，用户重启工作台后重新捕获微信搜索框：

- 悬停红框 **能** 框到搜索框那一层（说明屏幕级命中是好的）；
- 按捕获键保存下来的仍然是整个微信窗口（1024×865）。

即两条路都能给出细元素，但**捕获走的那条没有用上它**。

## 取证

真机 trace（`%TEMP%\rpa-capture-trace.log`，2026-11-18 16:09）：

```
"mark": "main_enter", "mode": "hover", "image": false
"mark": "hover_hit_slow", "ms": 677.7, "scoped": false      ← hover 全程走快路径
"mark": "hook_click_seen"
"mark": "result_emitted", "outcome": "descriptor"
```

`scoped: false` 是关键：hover 红框一直由 `_hover_hit` 的**快路径**
（`ElementFromPoint` + `_drill_to_leaf`）画出来，而快路径的返回值里
`leaf = None`（leaf 只有 DFS 分支才会填）。

捕获瞬间的复用条件是 `last_leaf is not None` ⇒ 微信这种 `FindAll` 返回空的
provider **永远不复用**，一路退到 `capture_at` 再走一遍 DFS，最后拿整窗。

代码级三条分叉（都能独立造成「框的和存的不一样」）：

1. `_hover_capture` 捕获复用只看 `last_leaf`，而「画红框的那个元素」是
   `hit_info`（快路径 = 下钻后的 fine，DFS = leaf）；
2. 同一帧里 `last_rect` 更新、`hit_info is None` 时 `last_hit_info` 保留旧值 ⇒
   「矩形换到新处、元素还是上一处」，复用就把 A 存成 B；
3. `_finer_element_in_same_window` 要求 `_root_window_handle(info.handle) == 根窗口`，
   而 Qt 自绘控件的 `handle` 恒为 0 ⇒ 拿 0 去比永不相等，把唯一可用的命中挡掉
   （hover 快路径对同一形态有 `_win32_root_at` 兜底，两条路口径不一致）。

## 改动

`src/rpa_core/capture/desktop_agent.py`：

- **hover 捕获复用的对象改成 `last_hit_info`**（画红框的那个元素），删掉 `last_leaf`
  这个第二粒度源；`last_hit_info` / `last_path` 与 `last_rect` 同进同退——这一帧没
  命中就把旧元素清掉，不再「矩形是新的、元素是旧的」。
- `_finer_element_in_same_window`：`handle == 0` 时改用 `_win32_root_at(x, y)` 定身份
  （与 hover 快路径同口径）；三个失败分支各留一条 trace
  （`finer_unavailable` / `finer_other_window` / `finer_still_fills_window`），
  下次真机复现能直接看出断在哪一步。
- `capture_with_retry` 择优改成 `_capture_rank`：**粒度优先、命中数次之**，且「唯一
  命中」只有落在具体控件上才算够好。此前微信 provider 不支持 `FindAll` ⇒ 搜索框
  `verifyCount` 恒 0，而整窗 Pane 泛化能「命中」多次 ⇒ 按命中数择优会把细元素换回整窗。
- `capture_at` 语义模式也写 `metadata.rect`（与 `_describe_info` 共用新的 `_info_rect`）。
  此前只有图像模式写，rect 恒缺 ⇒ `_capture_rank` 无从判粒度。

## 判据

`tests/unit/test_capture_container_fallback.py`（12 条）：

- 快路径形态（`leaf=None`、`hit_info` 有）必须复用画红框的元素；
- 这一帧没命中元素 ⇒ 不许复用上一帧的旧元素；
- `handle=0` 的虚拟元素按光标处根窗口采信；别的窗口仍然拒绝；
- 重试择优：细元素命中 0 次 vs 整窗命中 3 次 ⇒ 取细元素；
- 整窗即使「唯一命中」也不提前返回；
- 没有 rect 不许当成「已钻到具体控件」；
- `capture_at` 语义模式落 rect。

元素拾取契约（26/26）第 4 个锚点：`handle=0` 时必须有 win32 根窗口兜底。

## 负向验证

`probe_m97_negative.py`：7 个注入，失败集逐项相等；`finer-root-no-fallback`
同时在单测与契约两层红（只改一层就绿是假绿灯的典型形态）；`desktop_agent.py`
md5 逐字节还原；还原后两个 runner 复跑全绿。

## 全量门禁

`check_all.py` → `FULL GATE PASSED` + `rc=0`。

## 待用户真机确认

重启工作台后重新捕获微信搜索框：悬停红框是搜索框、保存的目标节点也应是搜索框
（`metadata.rect` 约 200×30，而不是 1024×865）。

若仍落到整窗，看 trace 里这几条即可定位：

- `finer_unavailable reason=element_from_point_raised`：屏幕级被 provider 拒；
- `finer_other_window`：命中的是别的窗口（浮层/覆盖层）；
- `finer_still_fills_window`：屏幕级也只到渲染宿主，属 UIA 不暴露该控件，
  只能走图像方案（用户此前明确「按图捕获先不做」）。
