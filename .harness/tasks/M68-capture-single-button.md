# M68：元素捕获收成「一个按钮 + 确认弹窗」

Status: `done`

> **2026-11-12 立项并当日完成**。起因是维护者一段完整的产品判断：
>
> 「元素捕获界面简化为单个按钮：点击按钮即开始捕获，无需预先填写资产名称，无需选择桌面或
> Web 元素类型，也不提供直接写选择器名称入库的方式。环境准备等提示信息统一显示在弹出式
> 桌面框中，元素资产区域不展示任何提示。捕获完成后弹出一个 GUI 用于编辑元素属性而非直接
> 入库，可编辑内容包括资产名称，且资产名称支持中文输入，用户确认后才保存。另提供元素详情
> 按钮，点击后显示同一 GUI，供对已保存元素重新编辑。」
>
> 拆成五条硬要求：①面板只剩一个按钮 ②提示全部走桌面浮窗、面板零提示 ③捕获完不直接入库、
> 先弹框改属性 ④名称支持中文 ⑤详情按钮打开**同一个** GUI。
> 前置：M66（拾取交互重设计）、M67（编辑器工作区路径）、M52 S6（ADR-002 ref-only）。
> 不占 active 位（`M52-selector-advisor` 仍是主线）。

## 一、改前 / 改后

M66 的六态状态条把**技术分叉和过程状态全摊在面板上**：预填名输入框、网页/桌面下拉、
手写选择器 + 「写入库」按钮、四行进度文案。维护者的判断是这些本该由程序推断或在别处播报。

| 维度 | 改前（M66） | 改后（M68） |
| --- | --- | --- |
| 入口 | 每个待补元素两个按钮 + 工具条三个控件 | 面板只有一个「捕获元素」 |
| 资产名称 | 开抓前手填（重名要自己避） | 抓完自动起名 `元素1/2/3`，在弹窗里改 |
| 元素类型 | 用户选「网页 / 桌面」 | `m.command` 推出（`resolve_target` 三分支） |
| 手写选择器 | 输入框 + 「写入库」 | **通道删除**（`rpa.writeElement` / `rpa.pickPlan` 一并删） |
| 过程提示 | 面板状态条四行文案 | 桌面浮窗 `show_notice`，面板零提示 |
| 入库时机 | 捕获成功当场落盘 | 先进 `_CAPTURES` 暂存，用户确认才 `commitElement` |
| 详情 / 改名 | 行内展开只读信息 | 同一个弹窗，可改名，改名联动引用 |

## 二、前端（`.harness/demo/ppx_probe/app/gui/`）

- `index.html`：工具区只剩 `<button id="elemCapture">捕获元素</button>`；`</main>` 后新增
  `#elemDialog`（标题 / 名称输入 / meta / note / 取消 / 保存），`hidden` 起始。
- `main.js`：`ELEM_TEXT` 文案表集中，`renderElementsPanel` 只渲染「待补元素」与「资产」两段，
  不再有状态条节点；`openCaptureDialog` 与 `openAssetDetail` 共用 `showElemDialog`，
  用 `dialogMode`（`capture` / `detail`）决定确认时走 `commitElement` 还是 `saveElement`。
- 名称输入框 `type=text` + Enter 提交，但 `event.isComposing` 时**不抢键**（中文输入法上屏
  的那一下回车不是提交）。
- 轮询：首轮 800ms、其后 1000ms；ready → 弹框；`offline` / `failed` 静默收尾并 `restoreApp()`
  ——**失败的话由后端浮窗说**，前端不再自己写一遍。

## 三、后端协议（`app/api/api.py`）

新增：`rpa.commitElement(path, capture_id, name)`、`rpa.discardCapture(path, capture_id)`、
`rpa.saveElement(path, name, new_name)`。删除：`rpa.pickPlan`、`rpa.writeElement`。

- **捕获与落盘解耦**：`_hold_captured(entry, descriptor)` 只写 `entry["captured"]` +
  `entry["preview"]`（名称/类型/选择器/匹配数/节点/是否覆盖），`commit_element` 才写
  `elements/<name>.json`。会话键从「元素名」改成 `(path, capture_id)`，`capture_id` 由
  `_next_capture_id()` 递增生成——两条捕获并行也不会互相踩。
- **中文名全链路**：`_valid_element_name` 只挡 `/ \ : * ? " < > | .`、控制字符、首尾空白、
  >48 字、Windows 设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9），CJK 放行；写盘用
  `json.dumps(ensure_ascii=False, indent=2)` + **LF**。
- **改名联动引用**：`_rewrite_refs(path, old, new)` 遍历全 AST（`children` / `then` / `else` /
  `catch`）把 `elementRefs` 的值一起改；`saveElement` 改名失败要**回滚文件名与引用**，
  同名则返回 `unchanged: True` 空操作。`commitElement` 只在「按同名重抓」时允许 `overwrite`。
- `delete_element` 被引用时先 `_notify("删不掉", ...)` 再返 `ELEMENT_IN_USE`——拒绝的理由
  由浮窗说，不用前端横幅。

## 四、浮窗（`src/rpa_core/capture_kit/float_window.py`）

`CaptureFloat` 加第二种形态 `show_notice(title, detail, on_close=None, auto_hide_ms=0)`；
`show()` 可以把同一个实例**原地升级**成捕获形态（环境检查完接着抓，不闪两个窗）。提示形态
`on_close is None` 时按钮 `SW_HIDE`（纯告知）。

三个坑全在真机上撞出来的，代码里都留了注释：

1. **★ 窗口类只注册一次 ⇒ WNDPROC 绑死第一个实例**。`RegisterClass` 按类名在进程内只生效
   一次，`wc.lpfnWndProc = self._wnd_proc` 于是永远指向第一个 `CaptureFloat`。第二个窗开着、
   内容不动、日志一行异常都没有。改成模块级 `_dispatch` + `_INSTANCES[hwnd]` 查表分派。
2. **★ `DT_WORDBREAK` 断不开中文**。中文没有空格，长说明被右边按钮一刀切掉，用户看到半截话。
   自写 `_wrap_px`（CJK≈15px、ASCII≈8px，最多 3 行）。
3. **★ `DrawText` 的 rect 是 `(left, top, right, bottom)`，不是 `(left, top, width, height)`**。
   按后者传，第二行起 `bottom` 落在 `top` 上面（`top=36, bottom=18`），GDI 拿负高度做垂直居中
   ⇒ 标题和说明**叠在同一片区域**。修正后把换算收进 `_text(hdc, text, box=(x,y,w,h), ...)`，
   调用方只谈 `(x, y, w, h)`，语义错位不可能再发生。

## 五、判据

| 判据 | 位置 | 结果 |
| --- | --- | --- |
| 单按钮 / 弹窗控件 / 真点（DOM shim 跑 main.js） | `scripts/check_element_pick_ui.mjs` | 17/17 |
| 工具区唯一按钮、资产行 2 动作、删除失败保留行 | `scripts/check_element_library.mjs` | 8/8 |
| 捕获暂存 + 中文名 + 改名联动 + 轮询四态 | `.harness/scripts/check_element_pick_contract.py` | 15/15 |
| 桥层注册 / 详情 / 中文名 / 删除护栏 / dist 同步 | `apps/xhs-workbench/tests/test_editor_element_library.py` | 25/25 |
| 注入破坏 7 条 + 1 条元验证 | `.harness/spike/negative_element_library.py` | 8/8 |
| 资产全链路进程内探针 | `.harness/spike/probe_element_library.py` | 10 项 |
| 浮窗：3 行不叠字 / 不越界 / 提示形态 / 同类复用 | `.harness/demo/ppx_probe/_float_probe.py` | PASS |

**★ 判据教训（第四坑）**：浮窗早先只判「暗像素计数 > 200」。三行字叠成一坨时暗像素照样有
2000+，于是**叠字状态一直是绿的**。现在改成三件套：`_ink_bands` 把文字区切成墨迹带，
断言「行数 == 期望行数」（多条挤成一条即叠字）；`_ink_right` 断言右边界没顶到按钮；`_wrap_checks`
在纯函数层断言「真折了、每行不超宽（按最小字宽 8px 算上限，不误报）、不超 3 行」。
负向验证用的是**修复前那三张真实截图**：三张全部判出「1 条带」，判据从绿变红，方向对得上。

`ruff` 全绿（顺带清掉 `_INSTANCES` 上多余的引号，那是 WNDPROC 那次改动留下的，在上一次全量
门禁之后才写进去）。

## 五点五、当晚真机事故：参数键 camelCase 配 snake_case（18:05 现场）

交付后第一次真机捕获：**网页元素抓到了（trace 里 `result_received` 时间戳完整），编辑框
没弹、资产没新增**。逐层排查后根因在前端发出的参数键名：

- 桥层 `ppx_bridge.dispatch` 用 `inspect.signature(handler).bind(**params)` **严格绑定**，
  没有任何 camelCase → snake_case 换算；
- main.js 给 `capturePoll/commitElement/discardCapture/saveElement` 发的是 JS 惯例的
  `captureId` / `newName`，后端参数叫 `capture_id` / `new_name` ⇒ 桥层回
  `INVALID_PARAMS` ⇒ `ppx.call` 抛异常 ⇒ 前端轮询的 `catch` 静默收尾、会话丢弃——
  捕获已暂存但永远等不来 ready，用户看到「抓完什么都没发生」。
- **为什么判据全绿却拦不住**：Python 侧测试用 snake_case 直连 handler、DOM shim 用的是
  假 `ppx`——没有一条判据走「真实参数名」这条路（正是「判据要覆盖用户实际走的路」）。

修复：main.js 四处参数键统一回仓内约定 snake_case（`capture_id` / `new_name`，与既有
`node_id` / `run_id` 一致）；新增固定门禁
`.harness/scripts/check_rpc_wire_contract.py`——静态抽取 main.js 每处
`ppx.call('字面量方法', {字面量对象})` 的顶层键，与 AST 解析出的 api.py 签名做
「键 ⊆ 参数 且 必填参数 ⊆ 键」双向校验（动态方法/参数单列计数不强判）。负向验证：
把 `capture_id` 改回 `captureId` 注入一次，门禁按行号精准报红，md5 逐字节还原。

## 五点六、次日开机事故：env-status 超时预算误杀 + 管道收尾钉死线程（11-13 早现场）

用户报「RPA Probe TIMEOUT: PPX API 调用超时（30000ms）」，捕获按钮没反应。证据链：

1. 活服务器探测：`rpa.startCapture` 3.3s 返回 `blocked`，fix=「连不上运行时
   （TimeoutExpired）」——`rpa-core env-status` **热机实测 ~3.6s**（开机冷启动更久），
   `capability_snapshot` 的 `timeout=3.0` **必然误杀**，capability 永远
   `runtime:false` ⇒ 捕获永远被挡。
2. 更坏的一档：`subprocess.run(capture_output=True, timeout=...)` 超时后 kill 的是
   shim（`rpa-core.exe`），**孙进程 python 仍持有 stdout 管道**，run 的收尾读堵到
   孙进程自己退出——冷机上远超 30s ⇒ HTTP 处理线程被钉死 ⇒ 前端 30s 超时。
3. 定位手法：健康服务器上逐 RPC 探测全部毫秒级 ⇒ 嫌疑收敛到 env-status；
   直接 `rpa-core env-status` 计时 3.6s 实锤；输出里 `edge.online=true` 说明
   环境本来一切正常，纯粹是预算不够 + 杀法不对。

修复：`_env_status_json()` 改用「**临时文件 + Popen + wait(timeout)**」——kill 完 shim
立刻返回，孙进程接着写文件无害，处理线程永不挂；超时预算 3s → **10s**
（`_ENV_STATUS_TIMEOUT`）。契约判据补三条形态闸（源码无 `capture_output=True`、
走 `TemporaryFile`、预算 ≥5s），双向注入负向验证均按名报红、md5 还原。活服务器
端到端复验：capabilitySnapshot 1.8s 返回 `runtime:true / extensionOnline:true`，
startCapture 2.6s `waiting` → poll `pending` → cancel `cancelled`，桌面 agent 无残留。

## 五点七、维护者拍板：编辑框收进 capture_kit（2026-11-13）

维护者问「元素编辑框能否做成捕获 kit 的一部分，而不是在 web 页面中弹出来」，并在
「极简确认框进 kit / **全量搬进 kit** / 维持 web」里拍了**全量**：确认与详情都改
win32 原生，web 弹窗降为非 Windows 兜底。

动机正是前两次事故的教训：web 弹窗让「捕获 → 确认入库」强依赖页面生命周期
（主窗还原、iframe 活着、RPC 桥通畅），两个真机事故都死在「回页面的路上」。

落地：

- `capture_kit/_win32.py`（新）：窗口类注册 / `_INSTANCES` 分派 / 定时器 / 折行 /
  画字 / 字体从 float_window 下沉，浮窗行为不变（`_float_probe` 复跑 PASS）。
- `capture_kit/edit_dialog.py`（新）：`ElementEditDialog`——标题 + 名称 EDIT +
  只读多行 meta（选择器全文可复制）+ 错误行 + 保存/取消。消息泵手写 ctypes 版
  `IsDialogMessage` 循环（**`win32gui` 又没有这个成员**，同 CreateFont/SetTimer 坑），
  Enter=保存 / Esc=取消 / Tab 轮换 / EDIT 原生 IME（免 web 端 isComposing 守卫）。
  `SetForegroundWindow` 必须 try——环境性失败（句柄无效 6）会把窗口线程打死。
- `controller.py`：新增 `edit_dialog_factory / edit_model / on_commit / on_discard`
  注入口与 **editing / committed / discarded** 三态；`_result` 与 `_editing`
  同一把锁落定（否则宿主轮询会插进 ready 缝隙同时弹两个框）；工厂给 None
  （非 Windows）退回旧 ready 语义。
- api.py：`_do_commit` / `_do_rename` 抽成唯一权威（RPC 与桌面框回调共用，
  pop 归各自调用方）；`rpa.editElement` 详情弹同一个框（`_EditSession` 复用
  capture_poll 状态机）；前端 editing 只轮询、committed 刷新、discarded 收尾，
  详情先试 `editElement` 失败再回 `elementDetail` 页面框。

判据：contract 17/17（+轮询新三态、+详情桌面框）、pick_ui 20/20（+editing/
committed/详情优先桌面）、xhs 26 测（+editElement 注册）、负向 **10/10**
（新增 N8 删 committed 刷新、N9 详情不走桌面框）。★ 判据夹具的坑：DOM shim 的
`registry` 模块级共享 ⇒ 旧用例的处理器在新用例 click 上重放（N9 注入才暴露），
改每用例一副新节点 + `#elemDialog` 起始 hidden 对齐真实 DOM。

## 五点八、编辑框 v2：影刀式可选择器编辑 + 三处真机修补（2026-11-13 下午）

维护者验收 v1 后三连反馈：①保存按钮显示不全 ②编辑框要有元素各信息的编辑功能
（参考影刀）③资产/参数面板太挤。

- **按钮被裁**：`_HEIGHT` 当外框高传给 CreateWindowEx，`WS_CAPTION` 标题栏 + 边框
  吃掉 ~27px。修法：外框一律 `_win32.adjust_window_size`（ctypes 版
  `AdjustWindowRectEx`——**`win32gui` 又没有这个成员**）从客户区反推；探针加
  `_client_checks`（按钮矩形必须完整落在客户区内）作回归判据。
- **影刀式编辑**：名称 + **定位选择器可编辑**（网页=css 一行、桌面=locator JSON
  多行），只读区放元素信息。校验前置（`_selector_edit_error` 不改 doc，
  非法不落半成品）；`_apply_selector_edit` 写回；确认链 `_commit_from_dialog`
  与详情链 `_save` 都是「先校验定位 → 再动文件」。
- **SetFocus 同步卡 1.5s**：本机前台序列化/辅助技术客户端让建窗期 `SetFocus`
  阻塞 ⇒ 用户看到「窗半天不出来」。改成窗口可见后 `PostMessage` 给自己再设焦点
  （`_WM_APP_FOCUS`），卡顿藏在可见之后。
- 面板：资产行选择器独占一行允许折行（`word-break: break-all`，不再单行截断），
  参数行类型列 96→108px、删除列留足。

判据：contract 18/18（+选择器编辑校验/写回/入库）、_edit_probe 含客户区越界
判据 PASS、pick_ui 20/20、负向 10/10、FULL GATE PASSED。

## 六、欠账

- 真机点一遍「点按钮 → 桌面抓 → 弹框改名 → 确认入库」全链路（当前靠 DOM shim + 进程内探针）。
- 浮窗判据目前是手动跑的脚本（`.harness/demo/` 不进默认门禁：要真开窗，不能进 CI）。
- 捕获期「用户手抖点到别的元素」只能重抓，没有「换个候选」的入口。
