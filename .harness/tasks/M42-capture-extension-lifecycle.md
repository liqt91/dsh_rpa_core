# M42 网页捕获：已打开页面不生效 / 捕获后红框残留

状态：`done`

由来：维护者 2026-09-28 报障（M41 收口后）——

> 捕获网页元素时，不会在已经打开的网页上生效，捕获网页元素后，网页上红框还在

两条现象**同源**，都指向「扩展与已打开标签页之间的捕获态同步不可靠」。这是崩溃类之外的
**功能可用性**缺陷：网页捕获是混合捕获的主通道，它不工作时桌面腿又在浏览器内容区让位
（hybrid）⇒ 用户侧是「点了没反应」。沿用本仓纪律：先定性机制、再改实现、判据落到
**只在浏览器里跑的语义**上（node 桩求值），最后逐条负向验证 + 全门禁。

## 0. 一句话结论

1. **声明式 content script 只在页面加载时注入**。扩展装载/重载之后，**已经打开**的标签页
   不会再拿到脚本；页面里那个旧脚本与扩展的上下文也已经断开（`chrome.runtime.id` 消失、
   `sendMessage` 抛 `Extension context invalidated`）。而 `background.js::broadcast()` 此前
   **只用 `chrome.tabs.sendMessage` 且把失败静默吞掉**（注释写的是「页面无 content script
   （chrome:// 等），忽略」——它同时吞掉了「已打开的普通 http 页面」这一大类）⇒ `capture_arm`
   永远送不到这些页面，用户侧就是**「已经打开的网页上不生效」**。
2. **僵尸脚本会把红框永久留在页面上**。同一个旧脚本仍在 `document` 上挂着 `mousemove` 监听
   （`armed` 还是它最后收到的那次广播值），所以**红框照画**；但它既收不到 `capture_disarm`
   （通道断了），`capture()` 的两个失败分支原先是 `setHint(...); return`——**刻意不清框**
   （注释：「保留红框与提示，用户可再试」）。两件事叠加 ⇒ **「捕获后网页上红框还在」**。
3. 因此修复分两侧，且必须**成对**才有意义：`background` 侧**补注入**（让已打开页面立刻可用，
   幂等由 content 侧的实例守卫保证）+ `content` 侧的守卫**改为可接管**（活实例拦人、
   僵尸实例先拆干净再接管）。只做一侧都修不好：只补注入会被「装过就拒绝」的旧守卫挡住；
   只改守卫则没有任何东西会触发第二次注入。
4. 顺带堵掉同一族的另外两条：**host 断开时兜底撤防**（推送模型没有心跳，`capture_disarm`
   只能由 host 下发，通道一断就再也送不出去 ⇒ 页面永久停在捕获态）、**`capture_arm` 先落盘
   再广播**（补注入的脚本启动时会查 `storage.session`，先后颠倒会让它在窗口期内读到
   `false`，arm 在这一页丢失——又是一次「已打开的网页上不生效」）。

## 1. 证据

### 1.1 现象的两半都能在代码里闭环（无死角）

| 环节 | 事实 | 出处 |
| --- | --- | --- |
| 已打开页面拿不到 arm | `broadcast` 失败被静默吞 | `background.js::broadcast`（改前） |
| `onUpdated` 补发覆盖不到它 | 只在 `status === "complete"` 时补发 | `background.js` |
| 僵尸脚本仍在画框 | `armed` 是本地变量，`onMove` 只看它 | `content.js::onMove` |
| 僵尸脚本收不到撤防 | 通道已断，`chrome.runtime.onMessage` 收不到新广播 | 同上 |
| 失败路径不清框 | `setHint(...); return`（两处） | `content.js::capture` |
| 旧守卫挡住补注入 | `if (window.__rpaCaptureInstalled) return;` | `content.js`（改前） |

### 1.2 为什么「重新加载扩展」是最容易踩到它的操作

本仓的开发闭环就是「改 `extension/` → 在浏览器里重新加载扩展」。而 `chrome://extensions`
的「重新加载」**不会**把新脚本注入到已打开的标签页——它只影响之后加载的页面。于是
「改完扩展就地验证」的每一次都会命中这两条现象。M41 刚改过 `content.js` 的高亮几何，
维护者在这个闭环里报障，与机制完全吻合。

### 1.3 本轮**没有**做的验证（如实记）

本机没有可脚本化的「真机扩展重载 + 已打开标签页」复现环境，因此：

- **未做**真机复现（不能声称「真机验证通过」）。本轮证据 = **代码机制闭环**（1.1）+ **桩求值
  的真行为断言**（§3）+ **逐条负向验证**（§4）。
- 待维护者确认的两点：① 涉事浏览器与是否最近重新加载过扩展；② 报障页面是否就是
  「加载完成之前」打开的。若现场另有形状，按新证据再切。

## 2. 改动

### S1 `extension/content.js`：实例守卫可接管 + 僵尸自愈

- 顶部守卫由 `window.__rpaCaptureInstalled`（**装过就一律拒绝**）换成实例注册表
  `window.__rpaCaptureInstance = { alive, teardown }`：**活着的实例才拦人**（同一上下文里
  重复注入必须幂等——补注入会与 `onUpdated` 的补发、后续多次 arm 撞车），**僵尸实例先
  `teardown()` 拆干净**（清覆盖层 + 摘掉全部监听 + 让出实例位）再接管。
  `alive()` 由**每个实例自己的闭包**判断（`chrome.runtime && chrome.runtime.id`）——
  注册表写在 `window` 上，但 `chrome` 绑定属于写入者，所以新旧实例各自判各自的上下文。
- 监听器登记改走辅助函数 `on(target, type, handler, options)` 并记账到 `bound`，
  `teardown` 据此**真的摘掉**（否则僵尸实例会继续抢手势、继续画框）。
- `onMove` 增加自愈：`if (!isAlive()) { hideOverlay(); return; }` —— 僵尸实例不再画框，
  红框在鼠标一动时就消失（等补注入的实例接管）。
- `capture()` 两条失败分支由「保留红框 + 提示」改为 **`hideBox()` + 提示**：红框必须收掉
  （通道断了不会再有 disarm 来清它），提示条留着说明原因。为此把 `hideOverlay` 拆出
  `hideBox`（只收框不留提示）。

### S2 `extension/background.js`：补注入 + 统一撤防

- 新增 `ensureContentScript(tab)`：`chrome.scripting.executeScript({files:["content.js"]})`，
  只对 `http(s)`/`file` 页面（受保护页面注入必失败，直接跳过）。
- `broadcast(armed)` 改为：`armTab` 成功即继续；失败且**正在 arm** 时补注入成功后重发一次。
  **撤防不补注入**——新注入的脚本默认就是未 arm，为撤防往每个页面塞脚本毫无收益
  （捕获态由 arm 建立，能收到 arm 的页面必然也能收到撤防）。
- `armTab` 返回是否送达（不再静默吞失败的两种成因）。
- 新增 `disarmCapture()` 统一撤防（清会话 id + **落盘** `setCaptureArmed(false)` + **广播**），
  `sendCapture` 与 `case "capture_disarm"` 都改走它。
- `port.onDisconnect`（host 断开 ⇒ 捕获会话必然结束）**兜底撤防**。
- `case "capture_arm"`：`setCaptureArmed(true).then(() => broadcast(true))`——先落盘再广播。

### S3 `extension/manifest.json`：版本 `0.4.0` → `0.4.1`

行为修复要能被识别（`hello` 上报 `extVersion`、CRX 更新清单与注册表都取自 manifest）。
联动两处硬编码断言（`test_extension_installer.py`）。
**注意：维护者需要重新加载扩展才生效**（见 §5）。

## 3. 判据

两条新 node 门禁（都进 `check_all.py` 的 `PURE_FUNCTION_SCRIPTS`）。选 node 而不是 Python
的理由与本仓既有口径一致：这几个语义**只在浏览器里跑**，Python 侧读源码最多证明
「写了 `if (previous.alive())`」这行字，证明不了「活实例真被拦下 / 僵尸真被拆干净」。

- `scripts/check_capture_lifecycle.mjs`（**28 项**，桩 `window`/`document`/`chrome`/`navigator`/
  `CSS`/`location` 整文件求值 content.js）：
  S1 首次安装（净监听器 8 = 3 × onLeave + 5 × 手势、查态一次、未 arm 不画框）·
  S2 重复注入幂等（同上下文不重装、实例对象不换、runtime 监听不叠加）·
  S3 僵尸自愈（失效后鼠标移动不再留框）·
  S4 僵尸**被接管**（旧监听器摘干净不叠加 / 旧红框清掉 / 新实例登记且是活的 / 接管后能重新画框）·
  S5 失败路径收框（只剩提示条、未上报）·
  S6–S9 三条手势路径真触发捕获（`contextmenu` button=0 / `mousedown` button=2 / Ctrl+click）、
  普通单击不触发 · S10 提示文案按平台（Mac 用 ⌘）。
- `scripts/check_capture_broadcast.mjs`（**23 项**，按锚点切出捕获通道区 + 注入 chrome 送达模型：
  **只有「页面里有可用脚本」时 `sendMessage` 才成功**，补注入才让页面变得可送达）：
  S1 已打开页面「先失败 → 补注入 → 重发成功」· S2 有脚本的页面不白注入 · S3 受保护页面不注入 ·
  S4 撤防不补注入 · S5 注入失败不阻断也不重发 · S6 统一撤防（落盘 + 清会话 + 广播）·
  S7 回传即撤防 · C1 `port.onDisconnect` 必须撤防且仍重连 · C2 `capture_arm` 先落盘后广播、
  ack 立即回 · P1 补注入的文件名 ↔ `manifest.json` 声明一致（改名即静默失效）。

原本在 `tests/contract/test_capture_extension.py::test_browser_capture_hint_uses_platform_gesture_label`
里的两条 `addEventListener(...)` 源码断言**已同步改写法**（注册改走 `on(...)` 辅助函数），
并在 docstring 指向本 node 门禁（S6–S10 是它的行为版）。该测试保留**接线**断言，行为断言
不再重复 —— 这是「弱断言（读源码证明写了这行字）→ 强断言（桩事件真求值）」的迁移，
判据只增不减。

## 4. 负向验证

探针 `.harness/spike/probe_m42_negative.py`，**13 处注入全部精确命中**，报
`_m42_negative_report.txt`：

- content.js 5 处：c1 守卫退回「装过就拒绝」/ c2 不拆僵尸 / c3 teardown 不摘监听 /
  c4 僵尸照画不误 / c5 失败路径退回「保留红框」。
- background.js 8 处：b1 不补注入 / b2 撤防也补注入 / b3 去掉协议白名单 / b4 撤防不落盘 /
  b5 回传后不撤防 / b6 host 断开不撤防 / b7 先广播后落盘 / b8 文件名与 manifest 脱钩。

四条防假绿灯都做了：① **对照跑**（干净态两条门禁先跑绿）；② **失败类型判定**（必须
`FAIL |` 行 + 退出码非 0，且输出里**不得**出现 `TypeError:`/`ReferenceError:`/`SyntaxError:`/
`RangeError:`/`\n    at `——脚本自崩的退出码也是 1，那是**假红**）；③ **挂住判定**（`timeout=90`
超时单独报，不混进「红」）；④ **逐字节还原核 md5**（两个文件收尾都与 clean 一致）。
另加起手自检：文件里若还有上轮哨兵，直接 ABORT 而不是猜着还原。

## 5. 残留（登记不修）

- **维护者需要重新加载扩展**（或在浏览器里等 CRX 更新到 `0.4.1`）这两条修复才生效。
  旧版本扩展在已打开页面上仍然只能靠「刷新页面」恢复。
- **跨 isolated world 的僵尸脚本拆不掉**：若 Chrome 给扩展重载前后的 content script
  分配了**不同的 isolated world**，新脚本既读不到旧实例、也无法摘它的监听器
  （注册表写在各自的 `window` 上）。此时补注入仍能让页面**恢复可用**，但旧实例的框
  可能继续画到页面重新加载为止。本轮没有条件实测 world 归属，两种模型下**行为都正确**
  （同 world：接管并拆干净；不同 world：直接安装，旧实例随页面重载消失）——故按两种都成立设计。
- **`_BROWSER_CONTENT_CLASSES` 只有 `Chrome_RenderWidgetHostHWND`**：Firefox 的网页内容区
  类名是 `MozillaWindowClass`，hybrid 下桌面腿不会让位（会与扩展抢手势）。与本条报障无关，
  登记待办。
- 未改 `chrome.tabs.onUpdated` 的补发路径（新加载的页面必有脚本，无需补注入）。

## 6. 验收

- 两条新 node 门禁：**28 + 23 项全过**（`check_all.py` 已收录）。
- 负向验证：**13/13 命中**，逐字节还原。
- 受影响的 Python 契约测试：`test_capture_extension.py` + `test_extension_installer.py` +
  `test_capture_hybrid.py` = **91 passed / exit 0**。
- 全量门禁真值见 PROGRESS 条目（`check_all.py` 末行 + 退出码）。
