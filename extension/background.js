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
      setCaptureArmed(true);
      broadcast(true);
      // ack：让发起方确认 arm 已到达扩展（诊断用，host 会广播给客户端）
      post({ type: "capture_armed", sessionId: captureSessionId });
      break;
    case "capture_disarm":
      captureSessionId = null;
      setCaptureArmed(false);
      broadcast(false);
      post({ type: "capture_disarmed", sessionId: null });
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
async function broadcast(armed) {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    try {
      await chrome.tabs.sendMessage(tab.id, { type: "rpa-capture-arm", armed });
    } catch { /* 页面无 content script（chrome:// 等），忽略 */ }
  }
}

function sendCapture(descriptor) {
  post({
    type: "capture_result",
    sessionId: captureSessionId,
    ...descriptor,
  });
  captureSessionId = null;
  setCaptureArmed(false);
  broadcast(false);
}

// content script 启动时查询当前捕获态（新页面/新标签页无需等下一轮广播）
async function armTab(tabId, armed) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "rpa-capture-arm", armed });
  } catch { /* 页面无 content script（chrome:// 等），忽略 */ }
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
    sendCapture({ descriptor: msg.descriptor });
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

function errorCode(err) {
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
      if (args.action === "back") await chrome.tabs.goBack(tabId);
      else if (args.action === "forward") await chrome.tabs.goForward(tabId);
      else await chrome.tabs.reload(tabId);
      const done = await waitComplete(tabId, args.timeoutMs || 30000);
      const tab = await chrome.tabs.get(tabId).catch(() => ({}));
      return { tabId: args.tabId, url: tab.url || done.url || "" };
    }
    case "tabs.close":
      await chrome.tabs.remove(Number(args.tabId));
      return { closed: true };
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

function domOp(payload) {
  const { selector, method, args } = payload;
  const query = (sel) => (sel ? Array.from(document.querySelectorAll(sel)) : []);
  const isVisible = (el) => {
    if (!el) return false;
    const rects = el.getClientRects();
    if (!rects || rects.length === 0) return false;
    const style = window.getComputedStyle(el);
    return style.visibility !== "hidden" && style.display !== "none";
  };
  const fire = (el, type, init) => {
    el.dispatchEvent(new (type.startsWith("key") ? KeyboardEvent : MouseEvent)(type, init));
  };

  if (method === "count") {
    let list = query(selector);
    if (args.visible) list = list.filter(isVisible);
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
      const init = { bubbles: true, cancelable: true, button, view: window };
      for (const mod of args.modifiers || []) {
        if (mod === "Alt") init.altKey = true;
        if (mod === "Control") init.ctrlKey = true;
        if (mod === "Meta") init.metaKey = true;
        if (mod === "Shift") init.shiftKey = true;
      }
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
      if (el.focus) el.focus();
      const text = args.text == null ? "" : String(args.text);
      const previous = args.append ? String(el.value == null ? el.innerText : el.value) : "";
      if (args.mode === "set") {
        el.value = previous + text;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        for (const ch of (previous + text).split("")) {
          fire(el, "keydown", { key: ch, bubbles: true });
          el.value = (el.value || "") + ch;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          fire(el, "keyup", { key: ch, bubbles: true });
        }
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (args.pressEnter) {
        const enter = { key: "Enter", code: "Enter", keyCode: 13, bubbles: true };
        fire(el, "keydown", enter);
        fire(el, "keypress", enter);
        fire(el, "keyup", enter);
        if (el.form && el.form.requestSubmit) el.form.requestSubmit();
      }
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
      fire(el, "click", { bubbles: true, view: window });
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
