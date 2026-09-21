# M37：closeTabs 增加 ignoreBeforeUnload（beforeunload 抑制，best-effort）

状态：`done`

## 0. 背景与目标

影刀「关闭网页」带「忽略对话框」参数，对应 `chrome.tabs.remove` 关不掉的
`beforeunload` 确认框（有未保存表单的页面会卡在「离开此页面？」确认框上，
标签页留在原地）。M32 落地 closeTabs 时登记为后续任务，本里程碑实装：
`browser.closeTabs` 新增 `ignoreBeforeUnload`（boolean，默认 `true`，对齐影刀）。

## 1. 语义决策

- **缺省语义的单一兜底点在扩展**：Python 侧「显式给才转发」（`None` 不进 args），
  扩展用 `args.ignoreBeforeUnload !== false` 判真。避免双份默认值形成漂移源——
  哪天一侧改默认值，另一侧补发的默认值会悄悄把它盖回去。
- **抑制手段**：remove 前向目标页注入 MAIN world 脚本清 `window.onbeforeunload`。
  这是 Chromium-extensions 组讨论确认的标准缓解：`onbeforeunload` 属性形式可清；
  `addEventListener` 注册的处理器无法枚举清除——故只能是 **best-effort**，
  拦截残留时 remove 失败、如实进 `failedTabIds`。
- **注入失败不阻断关闭**：`chrome://`、已休眠标签等注入必失败（无 host 权限），
  此时 remove 照常执行。不造「注入失败 = 关闭失败」的假账。
- **`injectImmediately: true`**：executeScript 默认等 document idle，永不 idle 的
  页面会把整个 op 拖到超时。代价是与「页面尚未注册处理器」存在竞态（注入后页面
  又 set onbeforeunload）——关闭场景的目标页通常已加载完，竞态窗口小，记录在案。
- **范围**：只动 closeMany（批量路径）。单标签 `tabs.close` op 如有同样诉求，
  后续另行登记。

## 2. 实现

- `commands/browser/closeTabs.json`：新增 `ignoreBeforeUnload`（boolean，
  default true，description 写明 best-effort 边界）。
- `src/rpa_core/executors/browser.py`：closeTabs 分支读取（None 不转发），
  details 记 `ignoreBeforeUnload` 生效值（证据可见）。
- `src/rpa_core/executors/browser_ext.py`：`tabs_close_many` 增加
  `ignore_before_unload: bool | None = None`，非 None 才进 args。
- `extension/background.js`：closeMany case 内 per-tab 注入（`world: "MAIN"` +
  `injectImmediately: true`，独立 try/catch 包裹），既有逐个记账结构不变。
- `scripts/check_close_ops.mjs`：chrome 替身补 `scripting.executeScript`（记录
  事件序），新增 3 组行为矩阵 + 2 条反漂移正则 + 1 条 manifest 断言。

## 3. 验证

- check_close_ops 29→37 项，三条负向验证全过：
  ①缺省语义反转（`!== false`→`=== true`）→ 行为矩阵与正则双双红；
  ②删 closeMany 的 `world: "MAIN"` 行 → 变红（前提：正则切进 case 切片，见 §4）；
  ③删 manifest default → 变红；还原后回绿。
- 契约测试 18→21 项（显式 false 转发 / 显式 true 转发 / 缺省证据记 true 且
  args 不显式发送）。
- FULL GATE PASSED（收集实测 1086 项 = M36 后 1083 + 新增 3，delta 对账一致）。
- 真机验证：与 M32/M35 的真机验收同批覆盖（关带 beforeunload 的页面观察不再
  弹确认框；chrome:// 页注入失败仍可关、如实记账）。

## 4. 教训

- **同一文件的多个编辑绝不能放进同一并行批次**：本次 browser.py 与
  check_close_ops.mjs 各有 2-4 处编辑同批发出，每个编辑基于批次开始前的快照
  应用、最后落盘者覆盖前面——browser.py 出现 NameError、check_close_ops.mjs
  丢了全部 M37 断言，而门禁照常「全绿」。同文件多处修改要么合并为一次大
  Edit，要么逐条串行；改完用 Grep 验证落点再跑门禁。
- **门禁断言必须切进被测代码自己的切片**：全文件正则 `world: "MAIN"` 被
  page.call/page.eval 的同款注入喂出假绿灯（删掉 closeMany 的 world 行照样
  绿）——改为以相邻 case 名（`tabs.closeMany` → `tabs.listWindows`）为界切片
  后再负向验证才真红。
