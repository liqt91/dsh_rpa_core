// rpa_core 扩展 background service worker
// ① 捕获通道：短轮询 devserver pending 状态（捕获期间激活），把 content script 的
//    捕获结果 POST 回 devserver。无鉴权：devserver 仅绑定 127.0.0.1。
// ② 执行通道（M15，一等公民）：长轮询 /api/ext/command/next 领命令 → 本地执行
//    （tabs/scripting/cookies）→ /api/ext/command/result 回结果。
// 权限：默认「整个浏览器」（全部窗口/标签页/Cookie）；可用 chrome.storage.local 的
//    rpaExecPermission 收窄为 {mode:"tabs",tabIds:[...]} 或 {mode:"origins",allow:[...]}。
const DEVSERVER = "http://127.0.0.1:8765";
const POLL_ACTIVE_MS = 1200;   // 有待捕获会话时
const POLL_IDLE_MS = 5000;     // 空闲时
const EXEC_HOLD_S = 20;        // 命令长轮询保持（秒）
const PERMISSION_KEY = "rpaExecPermission";
const INSTANCE_KEY = "rpaInstanceId";
let pollMs = POLL_IDLE_MS;
let execRunning = false;
let cachedInstanceId = "";
// 持久 per-profile 实例 id：同一 profile 的多个窗口/页签（同一 storage）共用同一 id，
// 不同用户数据目录各自生成 —— 正好区分"同浏览器不同实例"（多 profile）。
async function ensureInstanceId() {
  if (cachedInstanceId) return cachedInstanceId;
  try {
    const { [INSTANCE_KEY]: stored } = await chrome.storage.local.get(INSTANCE_KEY);
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

// ---------------------------------------------------------------- 宿主身份
// 扩展装在哪个浏览器里，执行通道就用哪个浏览器（扩展通道没有"启动浏览器"概念）。
// 宿主身份经长轮询 query 上报 devserver，供「打开网页」校验执行宿主。
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
  return {
    browser: detectHostBrowser(),
    instanceId: cachedInstanceId || "",
    version: navigator.userAgentData && navigator.userAgentData.brands
      ? (navigator.userAgentData.brands.find((b) => /Chromium/.test(b.brand)) || {}).version || ""
      : "",
    platform: navigator.platform || "",
    userAgent: navigator.userAgent || "",
  };
}

function hostQuery() {
  const info = hostInfo();
  return `&host=${encodeURIComponent(info.browser)}&iid=${encodeURIComponent(info.instanceId)}`
    + `&ver=${encodeURIComponent(info.version)}`
    + `&platform=${encodeURIComponent(info.platform)}&ua=${encodeURIComponent(info.userAgent)}`;
}

// ---------------------------------------------------------------- 捕获通道

async function poll() {
  try {
    const resp = await fetch(`${DEVSERVER}/api/capture/extension/pending`);
    if (!resp.ok) { schedule(); return; }
    const data = await resp.json();
    const pending = data.pending === true;
    await broadcast(pending);
    pollMs = pending ? POLL_ACTIVE_MS : POLL_IDLE_MS;
  } catch {
    // devserver 不在线：空闲节奏重试
    pollMs = POLL_IDLE_MS;
  }
  schedule();
}

async function broadcast(armed) {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    try {
      await chrome.tabs.sendMessage(tab.id, { type: "rpa-capture-arm", armed });
    } catch { /* 页面无 content script（chrome:// 等），忽略 */ }
  }
}

function schedule() {
  chrome.alarms.create("rpa-poll", { delayInMinutes: pollMs / 60000 });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "rpa-poll") poll();
  if (alarm.name === "rpa-exec") startExecLoop();   // SW 被回收后的兜底拉起
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "rpa-capture-result") {
    postResult(msg.descriptor).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg && msg.type === "rpa-capture-cancelled") {
    postResult({ cancelled: true }).then(() => sendResponse({ ok: true }));
    return true;
  }
});

async function postResult(descriptor) {
  try {
    await fetch(`${DEVSERVER}/api/capture/extension/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(descriptor),
    });
    pollMs = POLL_IDLE_MS;
    await broadcast(false);
  } catch { /* devserver 不在线，丢弃 */ }
}

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

function startExecLoop() {
  if (execRunning) return;
  execRunning = true;
  execLoop().catch(() => { /* 出错由下一轮重启 */ }).finally(() => { execRunning = false; });
  // 兜底：SW 回收后由 alarm 重新拉起（MV3 alarm 最小间隔 30s）
  chrome.alarms.create("rpa-exec", { delayInMinutes: 0.5 });
}

async function execLoop() {
  for (;;) {
    let resp;
    try {
      resp = await fetch(
        `${DEVSERVER}/api/ext/command/next?wait=${EXEC_HOLD_S}${hostQuery()}`,
      );
    } catch {
      await sleep(3000);   // devserver 不在线：退避重试
      continue;
    }
    if (!resp.ok) { await sleep(3000); continue; }
    const data = await resp.json();
    const cmd = data.command;
    if (!cmd) continue;    // 长轮询空转，直接续下一轮（同时充当心跳）
    await runCommand(cmd);
  }
}

async function runCommand(cmd) {
  let payload;
  try {
    await assertAllowed(cmd);
    const value = await executeCommand(cmd);
    payload = { id: cmd.id, ok: true, value: value || {} };
  } catch (err) {
    payload = {
      id: cmd.id,
      ok: false,
      error: { code: errorCode(err), message: String(err && err.message || err) },
    };
  }
  try {
    await fetch(`${DEVSERVER}/api/ext/command/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch { /* devserver 掉线：结果丢弃，宿主侧按超时处理 */ }
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
      const tab = await chrome.tabs.create({ url: args.url, active: args.active !== false });
      const done = await waitComplete(tab.id, args.timeoutMs || 30000);
      return { tabId: tab.id, url: done.url || args.url, title: done.title || "", completed: done.completed, timedOut: done.timedOut };
    }
    case "tabs.navigate": {
      const tabId = Number(args.tabId);
      await chrome.tabs.update(tabId, { url: args.url });
      const done = await waitComplete(tabId, args.timeoutMs || 30000);
      return { tabId: args.tabId, url: done.url || args.url };
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
      return { url: done.url || "" };
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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

// popup 查询宿主身份（状态展示「扩展装在哪」）
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "rpa-ext-host-info") {
    sendResponse({ host: hostInfo(), devserver: DEVSERVER });
  }
  return false;
});

// 启动即开始：捕获轮询 + 执行长轮询（先确保实例 id 落位，随首个心跳/targetHost 一起上报）
poll();
ensureInstanceId().then(startExecLoop);
