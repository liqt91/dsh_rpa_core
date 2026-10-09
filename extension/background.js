// rpa_core 扩展 background service worker（MV3，零构建）
//
// 两条通道，均经 **Native Messaging 长连接**（ADR 0015）：
//   ① 捕获通道：host 推送 capture_arm/capture_disarm → 广播到全部标签页；
//      content script 的捕获结果经 port 回传 host。
//   ② 执行通道（一等公民）：host 推送 command → 本地执行（tabs/scripting/cookies）
//      → result 经 port 回传。
//
// host 名 `com.rpa_core.ext_bridge`，由浏览器按需拉起（无 8765 常驻服务、无 HTTP 轮询）。
// 权限：默认「整个浏览器」（全部窗口/标签页/Cookie）；可用 chrome.storage.local 的
//      rpaExecPermission 收窄为 {mode:"tabs",tabIds:[...]} 或 {mode:"origins",allow:[...]}。
const HOST_NAME = "com.rpa_core.ext_bridge";
// 构建标识：与 manifest.json 的 version 保持一致（契约测试钉三方相等）。
// **Load unpacked 的扩展不会因源码文件更新而自动重载**——浏览器里跑的永远是加载那一刻的
// 快照（2026-09-29 真机排障：仓库已修好「已开网页无红框」，但浏览器还跑着旧 background，
// 表现成「修复无效」）。把标识随 ack/进度/结果回传，host 侧对账即可判「扩展过期」，
// 不用再靠症状猜。
const EXT_BUILD = "0.8.0";
const RECONNECT_MS = 3000;      // 断开后的重连退避
const ALARM_NAME = "rpa-bridge-reconnect";  // SW 被回收时的兜底拉起（MV3 alarm 最小 30s）
const PERMISSION_KEY = "rpaExecPermission";
const INSTANCE_KEY = "rpaInstanceId";

let port = null;
let connecting = false;
let cachedInstanceId = "";
let cachedFocusedAt = 0;
let lastBridgeError = "";
let captureSessionId = null;
// 捕获激活态：旧 HTTP 模型每 5s 重复广播，新页面/新标签页自然被 arm；改为推送后
// 必须自己维持该语义（content script 启动时查询 + 页面加载完成时补发）。
const CAPTURE_ARMED_KEY = "rpaCaptureArmed";

async function setCaptureArmed(armed) {
  try {
    await chrome.storage.session.set({ [CAPTURE_ARMED_KEY]: !!armed });
  } catch { /* session storage 不可用：退化为仅内存 */ }
}

async function isCaptureArmed() {
  try {
    const data = await chrome.storage.session.get(CAPTURE_ARMED_KEY);
    return !!data[CAPTURE_ARMED_KEY];
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------- 宿主身份
// 扩展装在哪个浏览器里，执行通道就用哪个浏览器（扩展通道没有"启动浏览器"概念）。
// 宿主身份随 hello 上报 host，供「打开网页」校验执行宿主。
function detectHostBrowser() {
  const ua = navigator.userAgent || "";
  // 顺序敏感：Edge/Opera/Brave 的 UA 里都含 "Chrome/"，必须先判壳
  if (/Edg\//.test(ua) || /EdgA\//.test(ua)) return "msedge";
  if (/OPR\//.test(ua) || /Opera/.test(ua)) return "opera";
  if (/Brave/.test(ua)) return "brave";
  if (/Vivaldi/.test(ua)) return "vivaldi";
  if (/Firefox\//.test(ua)) return "firefox";
  if (/Chrome\//.test(ua)) return "chrome";
  if (/Safari\//.test(ua)) return "safari";
  return "unknown";
}

function hostInfo() {
  let extVersion = "";
  try {
    extVersion = (chrome.runtime.getManifest && chrome.runtime.getManifest().version) || "";
  } catch { /* 极端情况拿不到 manifest，置空 */ }
  return {
    browser: detectHostBrowser(),
    instanceId: cachedInstanceId || "",
    // 浏览器内核版本（如 Chromium/xxx），仅诊断用
    version: navigator.userAgentData && navigator.userAgentData.brands
      ? (navigator.userAgentData.brands.find((b) => /Chromium/.test(b.brand)) || {}).version || ""
      : "",
    // 插件自身版本（manifest.json 的 version），用于判断是否最新
    extVersion,
    platform: navigator.platform || "",
    userAgent: navigator.userAgent || "",
  };
}

// 持久 per-profile 实例 id：同一 profile 的多个窗口/页签（同一 storage）共用同一 id，
// 不同用户数据目录各自生成 —— 正好区分"同浏览器不同实例"（多 profile）。
async function ensureInstanceId() {
  if (cachedInstanceId) return cachedInstanceId;
  try {
    const stored = (await chrome.storage.local.get(INSTANCE_KEY)).rpaInstanceId;
    if (typeof stored === "string" && stored) {
      cachedInstanceId = stored;
    } else {
      cachedInstanceId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
      await chrome.storage.local.set({ [INSTANCE_KEY]: cachedInstanceId });
    }
  } catch {
    // storage 不可用（极端）：退回运行时随机 id，实例区分退化为"当轮运行时"
    cachedInstanceId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
  }
  return cachedInstanceId;
}

// 最近一次检测到宿主浏览器窗口处于前台聚焦的时刻（wall clock ms）。
// 仅内存缓存（SW 回收会丢，属可接受近似）；`focused` 与影刀「焦点优先→最后失去焦点」路由对应。
async function focusedState() {
  let focused = false;
  try {
    const win = await chrome.windows.getLastFocused();
    focused = !!win.focused;
  } catch { focused = false; }   // 无窗口/API 不可用统一按无焦点处理
  if (focused) cachedFocusedAt = Date.now();
  return { focused, focusedAt: cachedFocusedAt };
}

// ---------------------------------------------------------------- 连接（串行化）
// S0 实测：并发调用 connectNative 会拉起多个 host 进程，必须用 connecting 标志串行化。
async function connect() {
  if (port || connecting) return;
  connecting = true;
  try {
    const instanceId = await ensureInstanceId();
    if (port) return;
    try {
      port = chrome.runtime.connectNative(HOST_NAME);
    } catch (err) {
      port = null;
      lastBridgeError = String(err);
      scheduleReconnect();
      return;
    }
    port.onMessage.addListener(onHostMessage);
    port.onDisconnect.addListener(() => {
      lastBridgeError = chrome.runtime.lastError ? chrome.runtime.lastError.message : "";
      port = null;
      // host 断开 ⇒ 捕获会话必然已经结束（会话由 host 单方发起）。**必须自行撤防**：
      // 推送模型没有心跳，capture_disarm 只能由 host 下发，通道一断就再也送不出去了
      // ——页面会永久停在「捕获态」：红框跟着鼠标走，而怎么点都捕获不到
      // （M42 维护者报障「捕获元素后网页上红框还在」）。arm 状态同理要落盘置假，
      // 否则之后新开的页面查态会查到一个永远为真的 armed。
      disarmCapture();
      scheduleReconnect();
    });
    lastBridgeError = "";
    const focus = await focusedState();
    post({
      type: "hello",
      browser: detectHostBrowser(),
      instanceId,
      extVersion: hostInfo().extVersion,
      version: hostInfo().version,
      platform: navigator.platform || "",
      userAgent: navigator.userAgent || "",
      focused: focus.focused,
      focusedAt: focus.focusedAt,
    });
  } finally {
    connecting = false;
  }
}

function scheduleReconnect() {
  setTimeout(connect, RECONNECT_MS);
}

function post(payload) {
  if (!port) return false;
  try {
    port.postMessage(payload);
    return true;
  } catch (err) {
    lastBridgeError = String(err);
    return false;
  }
}

// ---------------------------------------------------------------- host → 扩展
function onHostMessage(msg) {
  if (!msg || typeof msg.type !== "string") return;
  switch (msg.type) {
    case "ready":
      // host 已绑定本地端点（诊断用；身份由 hello 上报）
      break;
    case "command":
      runCommand(msg);
      break;
    case "capture_arm":
      captureSessionId = msg.sessionId || null;
      // **先落盘捕获态再广播**：补注入的脚本启动时会查 storage.session 拿当前态，
      // 先广播后落盘会让它在窗口期内读到 false——arm 在这一页丢失，用户侧又是
      // 「已经打开的网页上不生效」。
      setCaptureArmed(true).then(() => broadcast(true));
      // ack：让发起方确认 arm 已到达扩展（诊断用，host 会广播给客户端）；
      // extBuild 供 host 对账「浏览器里跑的扩展是不是当前构建」
      post({ type: "capture_armed", sessionId: captureSessionId, extBuild: EXT_BUILD });
      break;
    case "capture_disarm":
      disarmCapture();
      post({ type: "capture_disarmed", sessionId: null });
      break;
    case "capture_verify":
      // 活体校验（M47）：独立于捕获会话的按需请求——host 的校验通道只等
      // capture_verify_result（按 requestId 配对），与本会话的 arm/disarm 互不干扰。
      runVerify(msg);
      break;
    case "cancel":
      // 宿主已按超时返回调用方；迟到结果会被 host 丢弃，这里无需额外处理
      break;
    case "ping":
      post({ type: "pong", seq: msg.seq });
      break;
    default:
      break;
  }
}

// ---------------------------------------------------------------- 捕获通道
// content script 启动时查询当前捕获态（新页面/新标签页无需等下一轮广播）。
// 返回是否送达——失败有两种成因，**都不该被静默吞掉**：① 受保护页面（chrome:// 等）
// 本来就没有脚本，无需处理；② **扩展装载/重载之前就已打开的标签页**，它要么没有脚本、
// 要么只剩一个与扩展断链的僵尸脚本（见 content.js 的接管守卫），必须补注入才能让这一页
// 可用（M42 维护者报障「不会在已经打开的网页上生效」）。
async function armTab(tabId, armed) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "rpa-capture-arm", armed });
    return true;
  } catch {
    return false;
  }
}

// 补注入 content script。声明式 content_scripts **只在页面加载时**注入，所以对
// 「已经打开的标签页」而言这是唯一手段。幂等由 content.js 的实例守卫保证：活实例被拦下，
// 僵尸实例先被拆干净再接管。
async function ensureContentScript(tab) {
  if (!tab || tab.id == null) return false;
  if (!/^(https?|file):\/\//i.test(tab.url || "")) return false;   // 受保护页面注入必失败
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: false },
      files: ["content.js"],
    });
    return true;
  } catch {
    return false;   // 页面已卸载等：这一页不需要脚本
  }
}

async function broadcast(armed) {
  const tabs = await chrome.tabs.query({});
  // **活跃标签页优先**：用户眼前这页（每个窗口的 active tab）先送。标签页多时
  // 逐页「送一次失败→补注入→再送」是串行的，可能要数秒才轮到后排——不能让
  // 眼前这页排在队尾（2026-09-29 维护者复现「已经打开的网页没有红框，必须刷新」）。
  const active = tabs.filter((tab) => tab.active);
  const rest = tabs.filter((tab) => !tab.active);
  const progress = { tabs: tabs.length, armedCount: 0, injected: 0, failed: 0 };
  const armOne = async (tab) => {
    if (await armTab(tab.id, armed)) {
      progress.armedCount += 1;
      return;
    }
    // 撤防不补注入：新注入的脚本默认就是未 arm，为撤防塞一遍脚本毫无收益
    // （捕获态由 arm 建立，能收到 arm 的页面必然也能收到撤防）。
    if (!armed) return;
    if (!(await ensureContentScript(tab))) {
      progress.failed += 1;
      return;
    }
    progress.injected += 1;
    if (await armTab(tab.id, armed)) progress.armedCount += 1;
    else progress.failed += 1;
  };
  for (const tab of active) await armOne(tab);
  for (const tab of rest) await armOne(tab);
  // 现场取证：广播结果回传 host（客户端侧落进计时日志）。真机再报「某页无红框」时
  // 按这几个数就能定位是「送达了但脚本没接住」还是「注入没发生」，不用再猜。
  post({
    type: "capture_arm_progress",
    sessionId: captureSessionId,
    armed,
    extBuild: EXT_BUILD,
    ...progress,
  });
}

// 统一撤防：清会话 id + 落盘捕获态 + 广播到全部标签页。
// 「落盘」供新页面/补注入脚本查态，「广播」是已打开页面的出路——两者都要做。
function disarmCapture() {
  captureSessionId = null;
  setCaptureArmed(false).then(() => broadcast(false));
}

function sendCapture(descriptor) {
  post({
    type: "capture_result",
    sessionId: captureSessionId,
    extBuild: EXT_BUILD,
    ...descriptor,
  });
  disarmCapture();
}

// 活体校验（M47）/ 编辑预览（M48）：在**用户眼前的活跃标签页**上查找 css 并让
// content script 黄框闪烁（flash）或驻留高亮（preview）或清场（clear）——mode 由
// host 信封携带，原样透传给 content script，语义收口在那里。
// 只发活跃页是有意的：校验的对象是「刚捕获元素的那一页」，广播到全部标签页既慢
// 又可能命中别的页面上的同名结构，给出误导性的计数。页面没有脚本（或只剩僵尸）时
// 补注入一次再试——与 arm 广播同款语义。结果按 requestId 回传，host 侧据此配对。
//
// [verify-target:start]
// 选定校验目标页。**真前台窗口优先**：Chrome 整体在前台时（正常使用），
// `windows.getAll()` 里恰有一个 `focused:true` 的窗口，取它的活跃标签页就是用户眼前那页。
//
// 为什么不能只用 `tabs.query({active:true, lastFocusedWindow:true})`（2026-10-08 真机实锤）：
// `lastFocusedWindow` 是**窗口**级的「最近聚焦」记忆，Chrome 整体失去前台（GUI 对话框抢
// 焦点、切到别的应用）时它**不会失效**，仍指向上一次聚焦的那个窗口。开了两个浏览器窗口时
// 它就在两个窗口之间来回指——真机 trace 抓到同一条 css 的回复一拨来自 explore 页（count 0）、
// 一拨来自 search_result_ai 页（count 1），**计数抖动其实是「打到了哪一页」在抖**。
// 因此顺序是：① 真前台窗口的活跃页（准）→ ② 最近聚焦窗口的活跃页（Chrome 不在前台时的
// 近似，保留旧行为兜底）→ ③ 任一活跃页（极端兜底，如只剩无窗口的标签页）。
async function pickVerifyTab() {
  try {
    const windows = await chrome.windows.getAll({ populate: false });
    const focused = windows.find((win) => win.focused && win.id != null);
    if (focused) {
      const tabs = await chrome.tabs.query({ active: true, windowId: focused.id });
      if (tabs.length) return tabs[0] || null;
    }
  } catch {
    // windows API 不可用/无窗口：退回下面的兜底链，不让取页失败变成校验失败
  }
  let tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tabs.length) tabs = await chrome.tabs.query({ active: true });
  return tabs[0] || null;
}

// 本浏览器**在操作系统级是否前台**。
//
// 为什么需要它（2026-10-08 维护者报障「Chrome 与 Edge 都开了同样的页面，校验时
// **两个页面都闪黄框**，虽然命中数只算 1 个」）：host 侧的 `ElementVerifier._exchange`
// 把 `capture_verify` 广播给**全部在线端点**（端点 = 一个浏览器实例）——本机实测就有
// `chrome` + `msedge` 两个 ⇒ 两个浏览器都收到、都闪框；而 host 只认**首个回传**，
// 所以命中数仍是 1 个。修法：把「是否前台」这个只有浏览器自己知道的事实交给浏览器
// 自判——**非前台的浏览器不闪框**（并且延后应答，把「首个应答」让给前台那个）。
//
// `windows.getLastFocused().focused`：浏览器整体失焦（前台被另一个浏览器/应用占住）时
// 为 false，正是需要的语义（与 `focusedState()` 同源）。API 不可用/无窗口时返回 **true**
// ——fail-open：宁可多闪一个框，也不能让「判不出前台」变成「谁也不应答、host 等满超时」。
async function isBrowserForeground() {
  try {
    const win = await chrome.windows.getLastFocused();
    return !!win.focused;
  } catch {
    return true;
  }
}
// [verify-target:end]

// 非前台浏览器延后应答的时长：给前台那个浏览器「先回」的机会（host 认首个回传）。
// 必须远小于 host 侧 5s 硬超时（`ElementVerifier(timeout=5.0)`），否则会把
// 「两个浏览器都不在前台」这种正常情形拖成 verify-timeout。
const VERIFY_BACKGROUND_ANSWER_DELAY_MS = 120;

async function runVerify(msg) {
  const mode = String(msg.mode || "flash");
  // 预览页签要的是「页面截图 + 红框」（M47.11，对齐影刀）：截图本身由 host 的
  // `wantShot` 触发（编辑区只在**点开预览页签**时才要图，普通 flash 校验不要——
  // 每次都截一张 base64 PNG 既慢又占内存）。
  const wantShot = msg.wantShot === true;
  const reply = {
    type: "capture_verify_result",
    sessionId: msg.sessionId || null,
    requestId: msg.requestId || null,
    extBuild: EXT_BUILD,
  };
  try {
    // 非前台：不闪框、且延后应答。前台那个浏览器会**立即**回，host 认首个回传 ⇒
    // 命中数与黄框都锁定「用户眼前那个浏览器」。两个浏览器都不在前台时，各自
    // 延后 120ms 后仍会以 silent 应答 ⇒ 不闪框、计数照常有值（等价旧行为，不假超时）。
    // 延后期间用户可能切回来了：那时再判一次，前台就转正常路径。
    let silent = false;
    if (!(await isBrowserForeground())) {
      silent = true;
      await new Promise((resolve) =>
        setTimeout(resolve, VERIFY_BACKGROUND_ANSWER_DELAY_MS)
      );
      if (await isBrowserForeground()) silent = false;
    }
    const tab = await pickVerifyTab();
    if (!tab || tab.id == null) {
      post({ ...reply, error: "no-active-tab" });
      return;
    }
    // 要截图时让 content 把黄框**驻留**住：截图必须先有框、后拍照，否则拍到空页面。
    // 拍完由 content 自己 700ms 后清（见 shot 分支），不依赖 host 再发一次 clear。
    const ask = {
      type: "rpa-capture-verify",
      css: String(msg.css || ""),
      mode,
      silent,
      keepFlash: wantShot,
    };
    let resp = null;
    try {
      resp = await chrome.tabs.sendMessage(tab.id, ask);
    } catch {
      // 没脚本 / 僵尸脚本：补注入后重试一次
      if (!(await ensureContentScript(tab))) {
        post({ ...reply, error: "inject-failed", url: tab.url || "" });
        return;
      }
      try {
        resp = await chrome.tabs.sendMessage(tab.id, ask);
      } catch {
        post({ ...reply, error: "no-response", url: tab.url || "" });
        return;
      }
    }
    // M47.12：预览截图**改由 host 统一走桌面坐标截屏**（ImageGrab 截窗口那块屏幕），
    // 与影刀同一路（维护者实测：影刀的 web 元素截图会把浏览器前面窗口也截进去，
    // 那正是坐标截屏的特征）。所以扩展侧**不再拍图**，只负责把 content 算好的
    // 视口矩形 / 视口屏幕原点回传——拍图要窗口绝对矩形，那是 host 的活。
    //
    // `windowId` 要留着：host 靠它找窗口矩形。chrome.windows.get 能拿到该窗口的
    // 顶层 hwnd（Windows 上 Chrome 的窗口句柄），拿不到就只回 windowId 让host
    // 自己在 Z 序里找。
    let shot = null;
    let shotProbe = null;
    if (wantShot && !silent) {
      const got = await windowHandleFor(tab);
      shot = got.hwnd;
      shotProbe = got.probe;
    }
    post({
      ...reply,
      ...(resp || {}),
      silent,
      url: tab.url || "",
      ...(shot ? { windowHandle: shot } : {}),
      ...(shotProbe ? { devProbe: shotProbe } : {}),
    });
  } catch (err) {
    post({ ...reply, error: String((err && err.message) || err) });
  }
}

// 顶层窗口句柄：host 侧桌面坐标截屏需要窗口的绝对矩形，而截图已从扩展移到 host
// （M47.12）。Windows 上 chrome.windows.get 的 winId 就是 Chrome 的顶层 hwnd；
// 拿不到（权限/其它平台）返回 null，host 会退回按进程名在 Z 序里找那个浏览器窗。
// **不抛**：句柄拿不到只影响预览出图，不影响命中数这条真判据。
async function windowHandleFor(tab) {
  // 诊断（M47.12 真机：换到 Edge 后has_hwnd 一直 false）：把**原始形态**一并回传，
  // 免得只能靠猜「是字段不存在 / 类型不对 / get 抛错」。devProbe 只在 wantShot
  // 时附带，体积可忽略，且不含用户数据（只有 windowId 与句柄数值）。
  const probe = {
    hasTab: !!tab,
    windowId: tab ? tab.windowId : null,
    hasWindowId: !!(tab && tab.windowId != null),
  };
  try {
    if (!tab || tab.windowId == null || tab.windowId === chrome.windows.WINDOW_ID_NONE) {
      probe.reason = "no-window-id";
      return { hwnd: null, probe };
    }
    const win = await chrome.windows.get(tab.windowId, { populate: false });
    probe.gotWindow = !!win;
    const raw = win && win["nativeWindowHandle"];
    probe.rawType = typeof raw;
    probe.rawValue = typeof raw === "number" ? raw : null;
    probe.hasKey = !!(win && "nativeWindowHandle" in win);
    const hwnd = raw;
    // 只认「像是真句柄」的值（正整数）；其它形态（0 / undefined）一律当拿不到。
    probe.accepted = typeof hwnd === "number" && hwnd > 0;
    if (!probe.accepted) probe.reason = "not-a-number-or-zero";
    return { hwnd: probe.accepted ? hwnd : null, probe };
  } catch (err) {
    probe.reason = "throw";
    probe.error = String((err && err.message) || err);
    return { hwnd: null, probe };
  }
}

// 页面加载完成时补发 arm：推送模型下新页面不会自动收到此前的广播
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if (info.status !== "complete") return;
  if (await isCaptureArmed()) armTab(tabId, true);
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "rpa-capture-state") {
    isCaptureArmed().then((armed) => sendResponse({ armed }));
    return true;   // 异步应答
  }
  if (msg && msg.type === "rpa-capture-result") {
    // contentBuild：页面里 content script 自己报的构建标识——background 是新的不代表
    // 页面里的脚本是新的（补注入前的那份可能还是扩展上次加载时的快照）
    sendCapture({ descriptor: msg.descriptor, contentBuild: msg.contentBuild });
    sendResponse({ ok: true });
    return true;
  }
  if (msg && msg.type === "rpa-capture-cancelled") {
    sendCapture({ cancelled: true });
    sendResponse({ ok: true });
    return true;
  }
  return false;
});

// ---------------------------------------------------------------- 执行通道

async function getPermission() {
  const data = await chrome.storage.local.get(PERMISSION_KEY);
  const perm = data[PERMISSION_KEY];
  return perm && perm.mode ? perm : { mode: "browser" };   // 默认整个浏览器
}

// 权限校验点：默认放行全部；收窄后才逐条比对（与宿主侧 ExtensionExecHub.allows 同语义）
async function assertAllowed(cmd) {
  const perm = await getPermission();
  if (perm.mode === "browser") return;
  const args = cmd.args || {};
  if (perm.mode === "tabs") {
    const allowed = (perm.tabIds || []).map(String);
    if (args.tabId && allowed.includes(String(args.tabId))) return;
    throw new Error(`tab ${args.tabId || "(any)"} outside allowed tab scope`);
  }
  if (perm.mode === "origins") {
    const allow = perm.allow || [];
    const url = args.url || "";
    if (url && allow.some((origin) => url.startsWith(origin))) return;
    throw new Error(`url ${url || "(none)"} outside allowed origins`);
  }
  throw new Error(`unknown permission mode ${perm.mode}`);
}

async function runCommand(cmd) {
  let payload;
  try {
    await assertAllowed(cmd);
    const value = await executeCommand(cmd);
    // 预检失败：扩展不抛异常（抛出去的 message 会被 errorCode 的文本启发式误判），
    // 而是在返回值里带结构化 precheck；这里把它翻成与执行器对齐的失败响应。
    if (value && value.precheck && value.precheck.code) {
      payload = {
        type: "result",
        id: cmd.id,
        ok: false,
        error: { ...value.precheck, code: value.precheck.code, message: value.precheck.message },
      };
      post(payload);
      return;
    }
    payload = { type: "result", id: cmd.id, ok: true, value: value || {} };
  } catch (err) {
    payload = {
      type: "result",
      id: cmd.id,
      ok: false,
      error: { code: errorCode(err), message: String(err && err.message || err) },
    };
  }
  post(payload);
}

// 预检/通道的稳定错误码白名单：优先用结构化 code（M28 S2），文本启发式只作兜底
const KNOWN_ERROR_CODES = [
  "ELEMENT_NOT_FOUND",
  "ELEMENT_AMBIGUOUS",
  "ELEMENT_COVERED",
  "ELEMENT_DISABLED",
  "ELEMENT_NOT_VISIBLE",
  "PERMISSION_DENIED",
  "TIMEOUT",
];

function errorCode(err) {
  const code = String((err && err.code) || "").trim().toUpperCase();
  if (KNOWN_ERROR_CODES.includes(code)) return code;
  const msg = String(err && err.message || err);
  if (msg.includes("did not match") || msg.includes("no element")) return "ELEMENT_NOT_FOUND";
  if (msg.includes("outside allowed")) return "PERMISSION_DENIED";
  if (msg.includes("Timeout") || msg.includes("timeout")) return "TIMEOUT";
  return "EXECUTOR_FAILED";
}

async function executeCommand(cmd) {
  const args = cmd.args || {};
  switch (cmd.op) {
    case "ping":
      return {
        version: chrome.runtime.getManifest().version,
        host: hostInfo(),
        permissions: await getPermission(),
      };
    case "tabs.list": {
      const tabs = await chrome.tabs.query({});
      return {
        tabs: tabs.map((tab) => ({
          id: tab.id, index: tab.index, url: tab.url || "", title: tab.title || "",
          active: !!tab.active, windowId: tab.windowId,
        })),
      };
    }
    case "tabs.create": {
      const tab = await createTab(args);
      const done = await waitComplete(tab.id, args.timeoutMs || 30000);
      return { tabId: tab.id, url: done.url || args.url, title: done.title || "", completed: done.completed, timedOut: done.timedOut };
    }
    case "tabs.navigate": {
      const tabId = Number(args.tabId);
      await chrome.tabs.update(tabId, { url: args.url });
      const done = await waitComplete(tabId, args.timeoutMs || 30000);
      // completed/timedOut 供执行器按 onTimeout 策略处理（旧版缺省视为已完成）
      return { tabId: args.tabId, url: done.url || args.url, completed: done.completed, timedOut: done.timedOut };
    }
    case "tabs.history": {
      const tabId = Number(args.tabId);
      if (args.action === "back" || args.action === "forward") {
        // 不用 chrome.tabs.goBack/goForward：实测它对「历史栈明明有上一页」的
        // 标签页也报 "Cannot find a next page in history"（M38 S4.4 真机探针，
        // history.length=2 仍必现）——该 API 在 MV3 下不可靠。改用页内
        // history.back()/forward()：语义一致，且所有 Chromium 版本行为相同。
        const before = await chrome.tabs.get(tabId).catch(() => ({}));
        await chrome.scripting.executeScript({
          target: { tabId },
          world: "MAIN",
          func: (action) => { window.history[action](); },
          args: [args.action],
        });
        // 先等导航**开始**（status 离开 complete 或 url 变化），否则 waitComplete
        // 会在导航还没起步时就看到 complete 而提前返回，把「真的 back 了」误判成
        // 「没有历史」。3s 宽限覆盖 SW/页面调度的正常抖动。
        const moveDeadline = Date.now() + 3000;
        let moving = false;
        while (Date.now() < moveDeadline) {
          const t = await chrome.tabs.get(tabId).catch(() => null);
          if (!t) break;
          if (t.status === "loading" || (t.url && t.url !== (before.url || ""))) {
            moving = true;
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        if (!moving) {
          throw new Error(`no ${args.action} page in history`);
        }
        const done = await waitComplete(tabId, args.timeoutMs || 30000);
        const tab = await chrome.tabs.get(tabId).catch(() => ({}));
        return { tabId: args.tabId, url: tab.url || done.url || "" };
      }
      await chrome.tabs.reload(tabId);
      const done = await waitComplete(tabId, args.timeoutMs || 30000);
      const tab = await chrome.tabs.get(tabId).catch(() => ({}));
      return { tabId: args.tabId, url: tab.url || done.url || "" };
    }
    case "tabs.close":
      await chrome.tabs.remove(Number(args.tabId));
      return { closed: true };
    case "tabs.closeMany": {
      // 批量关闭（M32 S1）：tabIds 显式列表 / all=当前窗口全部标签，二者互斥。
      // 逐个 remove 并**如实分别记账**：一个失败不该让整批静默成功——调用方要的
      // 是「哪些关了、哪些没关」，只有关成功的进 closedTabIds。
      // 注意：chrome.tabs.remove 对最后一个标签的行为——Chrome/Edge 会**关闭整个窗口**
      // （除非是应用窗口），这是浏览器语义，我们不额外造「留一个空白页」的假动作。
      // M37：ignoreBeforeUnload（缺省视为 true，对齐影刀）——remove 前先向目标页
      // 注入 MAIN world 脚本清掉 window.onbeforeunload，避免「离开此页面？」确认框
      // 卡住程序化关闭。best-effort：注入失败（chrome://、已休眠标签等）不阻断
      // remove；addEventListener 形式注册的拦截无法枚举清除，仍可能弹窗 → remove
      // 失败如实进 failedTabIds，调用方看到的就是真实结果。injectImmediately 使
      // 注入不等页面加载完成（永不 idle 的页面不会把整个 op 拖到超时）。
      const tabIds = Array.isArray(args.tabIds) ? args.tabIds : null;
      let targets = [];
      if (tabIds) {
        targets = tabIds.map((id) => Number(id)).filter((id) => Number.isFinite(id));
      } else if (args.all === true) {
        const current = args.windowId == null
          ? await chrome.windows.getCurrent().catch(() => null)
          : { id: Number(args.windowId) };
        const tabs = await chrome.tabs.query(
          current && current.id != null ? { windowId: current.id } : {},
        );
        targets = tabs.map((tab) => tab.id).filter((id) => id != null);
      }
      const ignoreBeforeUnload = args.ignoreBeforeUnload !== false;
      const closedTabIds = [];
      const failedTabIds = [];
      for (const tabId of targets) {
        if (ignoreBeforeUnload) {
          try {
            await chrome.scripting.executeScript({
              target: { tabId },
              world: "MAIN",
              injectImmediately: true,
              func: () => {
                try { window.onbeforeunload = null; } catch (_) { /* 个别页面冻结了 window */ }
                return true;
              },
            });
          } catch (_) {
            // best-effort：注入失败（无 host 权限、已休眠等）不阻断关闭
          }
        }
        try {
          await chrome.tabs.remove(tabId);
          closedTabIds.push(tabId);
        } catch (_) {
          failedTabIds.push(tabId);
        }
      }
      return { closedTabIds, failedTabIds };
    }
    case "tabs.listWindows": {
      // 窗口清单（closeBrowser 的「关窗口」路径用）：只报 id/状态，不报 URL 等页面内容
      const windows = await chrome.windows.getAll({ populate: false });
      return {
        windows: windows.map((win) => ({
          windowId: win.id,
          focused: !!win.focused,
          incognito: !!win.incognito,
          type: win.type || "normal",
          tabCount: Array.isArray(win.tabs) ? win.tabs.length : 0,
        })),
      };
    }
    case "tabs.activate":
      await chrome.tabs.update(Number(args.tabId), { active: true });
      return { tabId: args.tabId };
    case "tabs.stopLoading": {
      const tabId = Number(args.tabId);
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => { window.stop(); return location.href; },
      });
      const tab = await chrome.tabs.get(tabId).catch(() => ({}));
      return { url: tab.url || (results && results[0] && results[0].result) || "" };
    }
    case "tabs.waitLoad": {
      const tabId = Number(args.tabId);
      const done = await waitComplete(tabId, args.timeoutMs || 30000);
      // completed/timedOut 供执行器按 onTimeout 策略处理（旧版缺省视为已完成）
      return { url: done.url || "", completed: done.completed, timedOut: done.timedOut };
    }
    case "screenshot": {
      const tabId = Number(args.tabId);
      const tab = await chrome.tabs.get(tabId);
      if (!tab.windowId) throw new Error("no window for screenshot");
      const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
        format: args.format || "png",
      });
      return { dataUrl: dataUrl || "" };
    }
    case "page.call":
      return await pageCall(args);
    case "page.eval":
      return await pageEval(args);
    case "cookies.getAll": {
      const filter = {};
      for (const key of ["url", "name", "domain", "path"]) {
        if (args[key]) filter[key] = args[key];
      }
      const url = args.url || await tabUrl(args.tabId);
      if (url && !filter.url) filter.url = url;
      return { cookies: await chrome.cookies.getAll(filter) };
    }
    case "cookies.get": {
      const url = args.url || await tabUrl(args.tabId);
      const cookie = await chrome.cookies.get({ url, name: args.name });
      return { value: cookie ? cookie.value : null };
    }
    case "cookies.set": {
      let count = 0;
      const url = args.url || await tabUrl(args.tabId);
      for (const cookie of args.cookies || []) {
        await chrome.cookies.set({ ...cookie, url: cookie.url || url });
        count += 1;
      }
      return { count };
    }
    case "cookies.remove": {
      const url = args.url || await tabUrl(args.tabId);
      const removed = await chrome.cookies.remove({ url, name: args.name });
      return { count: removed ? 1 : 0 };
    }
    default:
      throw new Error(`unsupported op: ${cmd.op}`);
  }
}

// 建标签页：浏览器处于「后台驻留但无窗口」时（Edge/Chrome 的 --no-startup-window，
// 用户把窗口全关了但进程还在、扩展 SW 仍在线），chrome.tabs.create 会抛 Chromium 的
// "No current window"。此时退化为新建窗口，保证「打开网页」在无窗口状态下也能用。
async function createTab(args) {
  const url = args.url;
  const active = args.active !== false;
  try {
    return await chrome.tabs.create({ url, active });
  } catch (err) {
    const message = String((err && err.message) || err);
    if (!/No current window/i.test(message)) throw err;
    const win = await chrome.windows.create({ url, focused: active });
    const tabs = (win && win.tabs) || (await chrome.tabs.query({ windowId: win.id }));
    const tab = tabs && tabs[0];
    if (!tab) throw new Error("no tab after creating window");
    return tab;
  }
}

// 页面 DOM 原语：注入主 world 执行（规避扩展 CSP 对 eval 的限制，不依赖 content script 注入时机）
async function pageCall(args) {
  const tabId = Number(args.tabId);
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: domOp,
    args: [{ selector: args.selector || "", method: args.method, args: args.args || {} }],
  });
  return (results && results[0] && results[0].result) || { matchedCount: 0, result: null };
}

async function domOp(payload) {
  const { selector, method, args } = payload;
  const query = (sel) => (sel ? Array.from(document.querySelectorAll(sel)) : []);
  const fire = (el, type, init) => {
    el.dispatchEvent(new (type.startsWith("key") ? KeyboardEvent : MouseEvent)(type, init));
  };
  // [input-helpers:start]
  // 纯函数区：scripts/check_input_helpers.mjs 按标记抽取求值（标记独占一行便于切片）
  // 输入模式归一化：fill（别名 set）/ type / clipboard；未知值按 fill（与 manifest 默认一致）
  const inputMode = (raw) => {
    const value = String(raw == null ? "" : raw).trim().toLowerCase();
    if (value === "set") return "fill";
    if (["fill", "type", "clipboard"].includes(value)) return value;
    return "fill";
  };
  // 逐字间隔：非数字/负数一律 0（不因脏数据卡死），上限 5s 防误配
  const inputGapMs = (raw) => {
    const value = Number(raw);
    if (!Number.isFinite(value) || value <= 0) return 0;
    return Math.min(value, 5000);
  };
  // [input-helpers:end]
  // [precheck-helpers:start]
  // 纯函数区：scripts/check_precheck_helpers.mjs 按标记抽取求值（标记独占一行便于切片）。
  // M28 S2 执行前预检：命中元素但**不可安全操作**时必须显式失败，绝不静默点到遮罩层上。
  const PRECHECK_MESSAGES = {
    ELEMENT_NOT_VISIBLE: "目标元素不可见（display/visibility/opacity 隐藏、零尺寸或不在视口内）",
    ELEMENT_DISABLED: "目标元素处于禁用状态（disabled / aria-disabled / inert）",
    ELEMENT_COVERED: "目标元素被其它元素遮挡，点击会落在遮挡者身上",
  };
  // 元素可见性判定——**单一事实来源**（M32）。
  //
  // 此前这里是全扩展唯一一份可见性判定，但只查「有 client rects + 非 visibility:hidden/display:none」；
  // 而 `count`（waitFor 用它当命中信号）另有一份更宽松的同名实现。两份口径不一致，于是
  // `opacity: 0` 或落在视口外的元素会让 `waitFor(state=visible)` 判定「等到了」，
  // 紧接着的点击却报 `ELEMENT_NOT_VISIBLE`——**同一个词在两步里意思不同**，是最难排查的一类不一致。
  //
  // 现在统一到这个严格口径（比旧的 count 版严，与旧 precheck 版等价），`count` 复用它：
  // 去掉 `checkOpacity` 以外的项都是在「说可见但其实点不到」，而 waitFor 的全部价值就是
  // 「等到能操作」——宽松判定会让它给出假的绿灯。
  const isElementVisible = (el) => {
    if (!el || el.isConnected !== true) return false;
    const rects = el.getClientRects ? el.getClientRects() : null;
    if (!rects || rects.length === 0) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    if (typeof el.checkVisibility === "function") {
      // checkOpacity 覆盖 `opacity: 0`（以及祖先链上的 opacity:0）；
      // checkVisibilityCSS 覆盖 visibility/display（比上面两行更完整，但保留它们是
      // 为了在旧内核没有 checkVisibility 时仍有兜底）。
      if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    }
    const rect = rects[0];
    return rect.width > 0 && rect.height > 0;
  };
  // 可操作动作：只有这些 method 需要预检（读取类 getText/getPosition 等照旧允许读隐藏元素）
  const precheckRequired = (raw) => {
    const value = String(raw == null ? "" : raw).trim().toLowerCase();
    return ["click", "hover", "input", "select", "check", "drag"].includes(value);
  };
  // 元素是否禁用：原生 disabled + ARIA 语义 + inert 子树（继承祖先可用 closest 判定）
  const isDisabledElement = (el) => {
    if (!el) return false;
    if (el.disabled === true) return true;
    if (String(el.getAttribute && el.getAttribute("aria-disabled") || "").toLowerCase() === "true") {
      return true;
    }
    if (el.closest && el.closest("[inert]")) return true;
    return false;
  };
  // 元素是否被视口内其它元素遮挡：取**即将点击的那个点**做 elementFromPoint 包含性判定。
  // 返回 null 表示未被遮挡；否则返回遮挡者的可读描述（写进 details，便于排查）。
  // `point` 缺省时退回元素中心（保持旧行为）；M29 起 click 会传入实际点击点——
  // 「按中心判遮挡、按随机点落点击」会让遮挡判定形同虚设。
  const coveringElement = (el, rects, point) => {
    if (!el || typeof document.elementFromPoint !== "function") return null;
    const rect = (rects && rects[0]) || (el.getBoundingClientRect ? el.getBoundingClientRect() : null);
    if (!rect || !(rect.width > 0) || !(rect.height > 0)) return null;
    const x = point && Number.isFinite(point.x) ? point.x : rect.left + rect.width / 2;
    const y = point && Number.isFinite(point.y) ? point.y : rect.top + rect.height / 2;
    if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) return null;
    const top = document.elementFromPoint(x, y);
    if (!top || top === el || el.contains(top) || top.contains(el)) return null;
    return {
      tag: String(top.tagName || "").toLowerCase(),
      id: String(top.id || ""),
      className: String((typeof top.className === "string" ? top.className : "") || "").slice(0, 120),
    };
  };
  // 预检总入口：通过 → null；不通过 → { code, message, ... }（code 用稳定错误码）
  const elementPrecheck = (el) => {
    if (!el || el.isConnected !== true) {
      return { code: "ELEMENT_NOT_FOUND", message: "目标元素已从页面移除" };
    }
    if (isDisabledElement(el)) {
      return { code: "ELEMENT_DISABLED", message: PRECHECK_MESSAGES.ELEMENT_DISABLED };
    }
    // 可见性判定复用 `isElementVisible`（M32 统一口径）——预检与 count/waitFor
    // 现在问的是同一个问题，不会再出现「waitFor 说可见、预检说不可见」。
    if (!isElementVisible(el)) {
      return { code: "ELEMENT_NOT_VISIBLE", message: PRECHECK_MESSAGES.ELEMENT_NOT_VISIBLE };
    }
    return null;
  };
  // [precheck-helpers:end]
  // [click-helpers:start]
  // 纯函数区：scripts/check_click_helpers.mjs 按标记抽取求值（标记独占一行便于切片）。
  // M29：`browser.click` 的 `simulateHuman` / `clickPosition` 此前**声明了不生效**；
  // 这里把「点哪个坐标」「要不要走事件链」「修饰键怎么映射」钉死成可测的纯函数。
  // 点击点：center=元素几何中心；random=元素内「偏中心带」随机点（避开 1px 边框与内边距边沿）。
  // 一律裁剪进「元素矩形 ∩ 视口」——两者不相交时返回 null（调用方按不可见报错）。
  // 之所以必须裁剪：scrollIntoView 只保证元素**与视口相交**（可能有一半在视口外），
  // 直接按元素坐标派发事件会把点落在视口外，等于点了个不存在的坐标。
  const CLICK_POINT_BAND = 0.35; // random 落在中心 ±35%（即 0.15~0.85 区间）
  const clampNumber = (value, low, high) => Math.min(Math.max(value, low), high);
  // 位置归一化：manifest 只有 center/random，其余值（含缺省）按 center
  const clickPositionMode = (raw) =>
    String(raw == null ? "" : raw).trim().toLowerCase() === "random" ? "random" : "center";
  const clickPoint = (position, rect, rand, viewport) => {
    if (!rect) return null;
    const maxX = Math.max(Number(viewport && viewport.width ? viewport.width : 0) - 1, 0);
    const maxY = Math.max(Number(viewport && viewport.height ? viewport.height : 0) - 1, 0);
    const left = clampNumber(rect.left, 0, maxX);
    const top = clampNumber(rect.top, 0, maxY);
    const right = clampNumber(rect.right == null ? rect.left + rect.width : rect.right, 0, maxX);
    const bottom = clampNumber(rect.bottom == null ? rect.top + rect.height : rect.bottom, 0, maxY);
    if (!(right > left) || !(bottom > top)) return null;
    const random = clickPositionMode(position) === "random";
    const roll = typeof rand === "function" ? rand : Math.random;
    const ratio = () => {
      if (!random) return 0.5;
      const raw = Number(roll());
      const unit = Number.isFinite(raw) ? clampNumber(raw, 0, 1) : 0;
      return 0.5 - CLICK_POINT_BAND + 2 * CLICK_POINT_BAND * unit;
    };
    return {
      x: Math.round(left + (right - left) * ratio()),
      y: Math.round(top + (bottom - top) * ratio()),
    };
  };
  // 修饰键归一化：manifest 的枚举是 Alt/Ctrl/Shift/Win，而 DOM 事件字段是
  // altKey/ctrlKey/metaKey/shiftKey——"Ctrl"/"Win" 必须映射到 ctrlKey/metaKey，
  // 否则用户勾了「Ctrl」却一个修饰键都没生效（此前的静默漂移）。
  const modifierFlags = (modifiers) => {
    const flags = { altKey: false, ctrlKey: false, metaKey: false, shiftKey: false };
    for (const item of Array.isArray(modifiers) ? modifiers : []) {
      const name = String(item == null ? "" : item).trim().toLowerCase();
      if (name === "alt") flags.altKey = true;
      else if (name === "ctrl" || name === "control") flags.ctrlKey = true;
      else if (name === "win" || name === "meta" || name === "cmd" || name === "command") {
        flags.metaKey = true;
      } else if (name === "shift") flags.shiftKey = true;
    }
    return flags;
  };
  // simulateHuman：manifest 默认 true。只有**显式** false（含字符串 "false"）才走最短路径
  const simulateHumanEnabled = (raw) =>
    String(raw == null ? "" : raw).trim().toLowerCase() !== "false";
  // 是否走最短路径 el.click()：仅当显式关掉「模拟人工」，且参数面上没有 el.click() 表达不了的东西
  // （右键/中键、辅助键、双击）。**这些情况下仍走事件链**——静默忽略参数就是新的漂移。
  const useProgrammaticClick = (simulateHuman, button, modifiers, clickType) =>
    !simulateHumanEnabled(simulateHuman) &&
    String(button == null ? "left" : button).trim().toLowerCase() === "left" &&
    (Array.isArray(modifiers) ? modifiers.length === 0 : true) &&
    String(clickType == null ? "single" : clickType).trim().toLowerCase() === "single";
  // [click-helpers:end]
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const pressEnter = (el) => {
    const enter = { key: "Enter", code: "Enter", keyCode: 13, bubbles: true };
    fire(el, "keydown", enter);
    fire(el, "keypress", enter);
    fire(el, "keyup", enter);
    if (el.form && el.form.requestSubmit) el.form.requestSubmit();
  };
  const clickElement = (el) => {
    const init = { bubbles: true, cancelable: true, view: window, button: 0 };
    fire(el, "mousedown", init);
    if (el.focus) el.focus();
    fire(el, "mouseup", init);
    fire(el, "click", init);
  };
  const selectAll = (el) => {
    if (typeof el.select === "function") {
      el.select();
      return;
    }
    const range = document.createRange();
    range.selectNodeContents(el);
    const selection = window.getSelection();
    if (selection) {
      selection.removeAllRanges();
      selection.addRange(range);
    }
  };
  // 注入粘贴：beforeinput(insertFromPaste)/paste（带 DataTransfer）→ 校验内容是否变化；
  // 未变化再退 execCommand('insertText')；仍未变化返回 false（调用方显式失败）。
  const injectPaste = (el, text) => {
    const snapshot = () => (el.value == null ? el.innerText : String(el.value));
    const before = snapshot();
    let dataTransfer = null;
    try {
      dataTransfer = new DataTransfer();
      dataTransfer.setData("text/plain", text);
    } catch (error) {
      dataTransfer = null;
    }
    if (dataTransfer) {
      try {
        el.dispatchEvent(
          new InputEvent("beforeinput", {
            bubbles: true,
            cancelable: true,
            inputType: "insertFromPaste",
            data: text,
            dataTransfer,
          }),
        );
        el.dispatchEvent(
          new ClipboardEvent("paste", {
            bubbles: true,
            cancelable: true,
            clipboardData: dataTransfer,
          }),
        );
      } catch (error) {
        /* 构造失败（老内核）落回 execCommand 路径 */
      }
      if (snapshot() !== before) {
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }
    }
    try {
      document.execCommand("insertText", false, text);
    } catch (error) {
      return false;
    }
    if (snapshot() !== before) {
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
    return false;
  };

  if (method === "count") {
    let list = query(selector);
    // 复用 `isElementVisible`（M32 统一口径）：这里的计数是 `waitFor(state=visible)`
    // 的命中信号，必须与动作命令预检问同一个问题。
    if (args.visible) list = list.filter(isElementVisible);
    return { matchedCount: list.length, result: list.length };
  }
  if (method === "scroll") {
    const target = selector ? document.querySelector(selector) : null;
    if (selector && !target) return { matchedCount: 0, result: null };
    const behavior = args.smooth ? "smooth" : "auto";
    const position = args.position || "bottom";
    const host = target || window;
    const scroller = target || document.documentElement;
    if (position === "top") host.scrollTo({ top: 0, behavior });
    else if (position === "point") host.scrollTo({ top: args.y || 0, left: args.x || 0, behavior });
    else if (position === "page") host.scrollBy({ top: target ? host.clientHeight : window.innerHeight, behavior });
    else host.scrollTo({ top: scroller.scrollHeight, behavior });
    return {
      matchedCount: target ? 1 : 1,
      result: target ? host.scrollTop : window.scrollY,
    };
  }
  if (method === "queryAll") {
    const list = selector ? Array.from(document.querySelectorAll(selector)) : [];
    const items = list.map((n) => (n.innerText == null ? n.textContent : n.innerText) || "");
    return { matchedCount: items.length, result: items };
  }
  if (method === "getScrollPosition") {
    const target = selector ? document.querySelector(selector) : null;
    if (selector && !target) return { matchedCount: 0, result: null };
    if (target) {
      return { matchedCount: 1, result: { scrollX: target.scrollLeft || 0, scrollY: target.scrollTop || 0 } };
    }
    return { matchedCount: 1, result: { scrollX: window.scrollX, scrollY: window.scrollY } };
  }

  const el = selector ? document.querySelector(selector) : null;
  const matchedCount = selector ? document.querySelectorAll(selector).length : 0;
  if (!el || matchedCount === 0) return { matchedCount: 0, result: null };

  // 可操作动作先让元素进入视口：预检的「在视口内 / 未被遮挡」必须在滚动后再判，
  // 否则元素刚被选中就因落在视口外而误报不可见。滚动后再跑 elementPrecheck。
  // 滚动没把它带进视口（多为祖先容器 overflow 裁剪或滚动被拦截）→ 直接报不可见，
  // 绝不带着错误坐标继续点（否则「点击」会落在视口内的其它元素上，静默点错）。
  // M29：这里同时算出**实际要点击的那个点**（click 按 manifest 的 clickPosition 选点，
  // 其余动作取中心），该点既用于遮挡判定、也用于派发事件坐标——判定与落点必须是同一个点。
  let actionPoint = null;
  if (precheckRequired(method)) {
    el.scrollIntoView({ block: "center", inline: "nearest" });
    const rect = el.getBoundingClientRect();
    actionPoint = clickPoint(
      method === "click" ? args.clickPosition : "center",
      rect,
      Math.random,
      { width: window.innerWidth, height: window.innerHeight },
    );
    if (!actionPoint) {
      return {
        matchedCount,
        result: null,
        precheck: { code: "ELEMENT_NOT_VISIBLE", message: PRECHECK_MESSAGES.ELEMENT_NOT_VISIBLE },
      };
    }
    const failed = elementPrecheck(el);
    if (failed) return { matchedCount, result: null, precheck: failed };
    const blocker = coveringElement(el, el.getClientRects(), actionPoint);
    if (blocker) {
      const label = [
        blocker.tag,
        blocker.id ? `#${blocker.id}` : "",
        blocker.className ? `.${blocker.className.trim().split(/\s+/).join(".")}` : "",
      ].join("");
      return {
        matchedCount,
        result: null,
        precheck: {
          code: "ELEMENT_COVERED",
          message: PRECHECK_MESSAGES.ELEMENT_COVERED,
          blockedBy: blocker,
          blockedByLabel: label,
        },
      };
    }
  }

  switch (method) {
    case "getPosition": {
      const r = el.getBoundingClientRect();
      return { matchedCount, result: { x: r.x, y: r.y, width: r.width, height: r.height } };
    }
    case "getSelectOptions": {
      const options = Array.from(el.options || []).map((o, index) => ({
        index, value: o.value || "", label: o.label || o.text || "", selected: !!o.selected,
      }));
      return { matchedCount, result: { options, count: options.length } };
    }
    case "setValue": {
      const way = args.setWay || "value";
      const value = args.value == null ? "" : String(args.value);
      if (way === "innerText") el.innerText = value;
      else if (way === "innerHTML") el.innerHTML = value;
      else { el.value = value; el.dispatchEvent(new Event("input", { bubbles: true })); }
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { matchedCount, result: true };
    }
    case "setAttribute": {
      el.setAttribute(String(args.name || ""), String(args.value == null ? "" : args.value));
      return { matchedCount, result: true };
    }
    case "drag": {
      const target = document.querySelector(String(args.targetSelector || ""));
      if (!target) return { matchedCount: 0, result: null };
      const srcRect = el.getBoundingClientRect();
      const dstRect = target.getBoundingClientRect();
      const steps = 8;
      const dx = (dstRect.x + dstRect.width / 2 - srcRect.x - srcRect.width / 2) / steps;
      const dy = (dstRect.y + dstRect.height / 2 - srcRect.y - srcRect.height / 2) / steps;
      const base = { bubbles: true, cancelable: true, view: window };
      fire(el, "pointerdown", base);
      fire(el, "mousedown", { ...base, button: 0 });
      const sx = srcRect.x + srcRect.width / 2;
      const sy = srcRect.y + srcRect.height / 2;
      for (let i = 1; i <= steps; i += 1) {
        const evt = new MouseEvent("mousemove", { ...base, clientX: sx + dx * i, clientY: sy + dy * i, button: 0 });
        fire(el, "pointermove", { ...base, clientX: sx + dx * i, clientY: sy + dy * i });
        (document.elementFromPoint(sx + dx * i, sy + dy * i) || el).dispatchEvent(evt);
      }
      fire(target, "pointerup", base);
      fire(target, "mouseup", { ...base, button: 0 });
      fire(target, "drop", { ...base, clientX: dstRect.x + dstRect.width / 2, clientY: dstRect.y + dstRect.height / 2 });
      return { matchedCount, result: true };
    }
    case "click": {
      el.scrollIntoView({ block: "center", inline: "nearest" });
      const button = args.button === "right" ? 2 : (args.button === "middle" ? 1 : 0);
      const modifiers = args.modifiers || [];
      // 坐标与遮挡判定用同一个点（actionPoint 在预检块里算好）；
      // 此前事件完全没有坐标（clientX/clientY 恒为 0），按坐标做区域判断的页面会读到假数据。
      const point = actionPoint || { x: 0, y: 0 };
      if (useProgrammaticClick(args.simulateHuman, args.button, modifiers, args.clickType)) {
        // 最短路径（simulateHuman=false 且是普通左键单击）：直接触发激活行为，不派发 mousedown/mouseup
        el.click();
        return { matchedCount, result: true };
      }
      const init = {
        bubbles: true,
        cancelable: true,
        button,
        view: window,
        clientX: point.x,
        clientY: point.y,
      };
      Object.assign(init, modifierFlags(modifiers));
      if (button === 2) fire(el, "contextmenu", init);
      fire(el, "mousedown", init);
      fire(el, "mouseup", init);
      if (args.clickType === "double") {
        fire(el, "click", init);
        fire(el, "dblclick", init);
      } else {
        fire(el, "click", init);
      }
      return { matchedCount, result: true };
    }
    case "hover": {
      el.scrollIntoView({ block: "center", inline: "nearest" });
      const init = { bubbles: true, cancelable: true, view: window };
      fire(el, "mouseover", init);
      fire(el, "mouseenter", { bubbles: false, view: window });
      fire(el, "mousemove", init);
      return { matchedCount, result: true };
    }
    case "input": {
      el.scrollIntoView({ block: "center", inline: "nearest" });
      const text = args.text == null ? "" : String(args.text);
      const mode = inputMode(args.mode);
      if (args.clickBeforeInput) clickElement(el);
      if (el.focus) el.focus();
      if (mode === "clipboard") {
        // 粘贴语义（不写系统剪贴板；面向只接受 paste 的富文本/受控编辑器）：
        // 构造 DataTransfer 走 beforeinput/paste，失败再退回 execCommand('insertText')，
        // 两者都没改变内容就**显式失败**——绝不静默退化成逐字输入。
        if (!args.append) selectAll(el);
        const accepted = injectPaste(el, text);
        if (!accepted) {
          return {
            matchedCount,
            result: null,
            inputRejected: true,
            message:
              "该元素未接受剪贴板粘贴注入（可能只接受真实粘贴或按键输入）；"
              + "请改用「直接填写」或「逐字模拟人工输入」模式",
          };
        }
        if (args.pressEnter) pressEnter(el);
        return { matchedCount, result: el.value == null ? el.innerText : el.value };
      }
      const previous = args.append ? String(el.value == null ? el.innerText : el.value) : "";
      if (mode === "fill") {
        // 直接填写：赋值 + input/change（最快、最稳，但部分受控组件可能忽略赋值）
        el.value = previous + text;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        // 逐字模拟人工输入：按键事件 + 逐字间隔（keyIntervalMs 必须真正生效——
        // 这是风控敏感场景的核心参数；此前被静默忽略）
        const gap = inputGapMs(args.keyIntervalMs);
        const chars = (previous + text).split("");
        for (let index = 0; index < chars.length; index += 1) {
          const ch = chars[index];
          fire(el, "keydown", { key: ch, bubbles: true });
          el.value = (el.value || "") + ch;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          fire(el, "keyup", { key: ch, bubbles: true });
          if (gap > 0 && index < chars.length - 1) await delay(gap);
        }
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (args.pressEnter) pressEnter(el);
      return { matchedCount, result: el.value == null ? null : el.value };
    }
    case "getText": {
      const info = args.infoType || "text";
      let result = null;
      if (info === "html") result = el.innerHTML;
      else if (info === "outerHTML") result = el.outerHTML;
      else if (info === "value") result = el.value == null ? "" : String(el.value);
      else if (info === "href") result = el.getAttribute("href") || el.href || "";
      else result = el.innerText == null ? el.textContent : el.innerText;
      return { matchedCount, result: result == null ? "" : result };
    }
    case "select": {
      const by = args.selectBy || "value";
      const wanted = String(args.value == null ? "" : args.value);
      let hit = null;
      for (const option of Array.from(el.options || [])) {
        if (by === "label" && option.label === wanted) hit = option;
        else if (by === "text" && option.text === wanted) hit = option;
        else if (by === "index" && String(option.index) === wanted) hit = option;
        else if (by === "value" && option.value === wanted) hit = option;
        if (hit) break;
      }
      if (!hit) return { matchedCount: 0, result: null };
      el.value = hit.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { matchedCount, result: el.value };
    }
    case "check": {
      const operation = args.operation || "check";
      const before = !!el.checked;
      el.checked = operation === "check" ? true : (operation === "uncheck" ? false : !before);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      // 不要再补发 click：合成 click 在 checkbox/radio 上会触发**激活行为**（再切换
      // 一次 checked），把刚设的值翻回去——check/uncheck/toggle 三个操作曾因此全部
      // 反转（M38 S4.3 L2 真机探针实测抓住；L1 桩断言天然看不见这类病）。
      // input+change 是框架监听状态变更的标准事件面，足够了。
      return { matchedCount, result: !!el.checked };
    }
    default:
      throw new Error(`unsupported page method: ${method}`);
  }
}

// 任意 JS（browser.executeScript）：主 world eval；页面 CSP 禁 eval 时明确报错
async function pageEval(args) {
  const tabId = Number(args.tabId);
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: (code, argv) => {
      // eslint-disable-next-line no-new-func
      const fn = new Function("__RPA_ARGS__", `"use strict";\n${code}`);
      return fn(argv);
    },
    args: [String(args.script || ""), args.args || []],
  });
  return { result: results && results[0] ? results[0].result : null };
}

function waitComplete(tabId, timeoutMs) {
  return new Promise((resolve) => {
    const deadline = Date.now() + (timeoutMs || 30000);
    const tick = async () => {
      let tab = null;
      try {
        tab = await chrome.tabs.get(tabId);
      } catch { /* tab 已关闭 */ }
      const timedOut = Date.now() >= deadline;
      // 页面加载完成或到达超时窗口即返回；超时如实上报 completed/timedOut，
      // 供执行器按 onTimeout 策略（报错或停止加载继续）处理。
      if ((tab && tab.status === "complete") || timedOut) {
        resolve({
          id: tabId,
          url: (tab && tab.url) || "",
          title: (tab && tab.title) || "",
          status: tab ? tab.status : "unloaded",
          completed: !!(tab && tab.status === "complete"),
          timedOut,
        });
        return;
      }
      setTimeout(tick, 200);
    };
    tick();
  });
}

// 取 tab 当前 URL：cookie 等浏览器级操作需要 url 做作用域，但会话只存 tabId
async function tabUrl(tabId) {
  if (tabId == null) return "";
  try {
    const tab = await chrome.tabs.get(Number(tabId));
    return tab.url || "";
  } catch {
    return "";
  }
}

// ---------------------------------------------------------------- 焦点上报
chrome.windows.onFocusChanged.addListener(async () => {
  const focus = await focusedState();
  post({ type: "focus", focused: focus.focused, focusedAt: focus.focusedAt });
});

// popup 查询宿主身份与桥接状态（状态展示「扩展装在哪 / bridge 是否连上」）
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "rpa-ext-host-info") {
    sendResponse({
      host: hostInfo(),
      hostName: HOST_NAME,
      connected: port !== null,
      lastError: lastBridgeError,
    });
  }
  return false;
});

// ---------------------------------------------------------------- 启动
chrome.alarms.create(ALARM_NAME, { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) connect();   // SW 被回收后的兜底拉起
});
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);

connect();
