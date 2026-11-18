# M96 — 校验第二次不画框（RegisterClass 重名）+ 微信搜索框捕获取证

Status: `done`

## 背景

用户捕获「微信搜索框」后保存，反馈三件事：

1. 组合选择器 JSON 内容很多，但层级只有两层；
2. 第一层「wnd 微信」不能勾选；
3. 校验第一次框住整个微信界面、命中 1 个；第二次不再框，仍显示命中 1 个。

## 取证

- 落盘资产 `examples/api-usage/elements/微信搜索框.json`：目标是
  `Pane / name=MMUIRenderSubWindowHW`，`metadata.rect = 1024x865`——就是微信整个
  渲染区，不是搜索框。`locator.path` 不存在，所以编辑模型只有「窗口级 + 目标级」两层。
- `.harness/spike/probe_m96_dump.ps1`（RawViewWalker，高完整性）：微信 4.2.0.13
  能读到的 UIA 树共 51 个节点，只有导航栏（微信/通讯录/收藏/发现/手机/更多）、
  主视图容器、标题栏按钮；`mmui::ChatMasterView`（会话列表）**没有子节点**，
  搜索框完全不在树里。
- `.harness/spike/probe_m96_flash_and_hit.py`：高完整性下 `ElementFromPoint` 对微信
  抛 `E_ACCESSDENIED`；pywinauto 的 `children()`（走 `FindAll`）对微信返回空，
  只有 `.NET TreeWalker` 能读到子树 ⇒ Python 侧捕获必然停在窗口/Pane 级。
- 同一探针连调 `flash_rect` 三次：`True, False, False`。第二次起 `RegisterClass`
  抛 1410「类已存在」，被兜底 `except` 吞掉 ⇒ 静默不画。这就是「第二次不框选」。

## 改动

- `desktop_highlight.py`
  - 窗口类只注册一次：`RegisterClass` 失败（1410 类已存在）时复用同名类，
    不再让第二次闪框整条失败。
  - `_show_overlay` 改为返回是否真的画出：零/负尺寸回 `False`，`flash_rect`
    据此返回，杜绝「没画还报成功」。
- `desktop_agent.py`（捕获粒度兜底）
  - 新增 `_window_rect` / `_rect_fills_window`：判断捕获到的矩形是否几乎铺满根窗口
    （阈值 90%），即「只拿到整窗容器」。
  - 新增 `_finer_element_in_same_window`：屏幕级 `ElementFromPoint` + 下钻，
    **只接受同一根窗口内**、且比整窗更细的元素（覆盖层/浮层一律不收）。
  - `capture_at` 窗口作用域分支：DFS 结果若只是整窗，再问一次屏幕级；
    拿到更细的用它，拿不到仍退回原结果（不丢兜底）。
  - 起因：微信这类只实现 TreeWalker 的 provider 让 `children()`（FindAll）恒空，
    DFS 从根窗口一步迈不出去，而悬停红框走的是屏幕级命中 ⇒ 两条路粒度不一致。
- 未改定位权威、未改校验链、未动 `writeElement`/`pickPlan` 红线。

## 判据与验证

- 新增 `tests/unit/test_desktop_highlight.py`：连续三次闪框都必须 `True`
  且每次建窗/销毁；非法矩形不得报成功。此前没有任何判据覆盖「连续两次校验」，
  这正是该缺陷长期存在的原因。
- 新增 `tests/unit/test_capture_container_fallback.py`：整窗 → 换细元素；
  没有更细 → 退回窗口作用域结果；已经够细 → 不再问屏幕级；面积阈值四态。
- 元素拾取契约新增一组（26/26）：钉住「判整窗 → 回退屏幕级 → 只认同根窗口 →
  面积阈值」四处接线，缺一即红。
- `probe_m96_negative.py`：5 个注入失败集逐项相等、两文件 md5 逐字节还原、
  三个 runner 复跑全绿。
- 真机复验：`flash_rect` 连调三次均 `True`；微信窗口 `1039x873` 下，
  1024x865 判为整窗、200x30 判为非整窗。
- 全量 `check_all.py`：`FULL GATE PASSED` + `rc=0`。

## 待用户真机确认

重启工作台后重新捕获微信搜索框：

- 若悬停红框本来就是搜索框大小，这次保存下来的也应是搜索框级（回退生效）；
- 若悬停红框本身就是整个微信窗口，说明该位置的屏幕级命中同样只到渲染窗，
  那就属于「UIA 不暴露该控件」，只能走图像/坐标，不是代码问题。

判断方法：看元素详情里目标节点的属性与矩形尺寸——1024x865 就是整窗。
