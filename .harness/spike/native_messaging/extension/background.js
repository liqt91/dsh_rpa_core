// S0 spike：验证 MV3 service worker 与长连 native messaging port 的存活关系。
//
// 观测设计：
// - 连上 host 后每 HEARTBEAT_MS 发一条 ping（setInterval 在 SW 内）；
// - 徽标 on/off 直观显示连接状态（无需打开任何面板）；
// - 每次连接尝试 / 断开 / 异常都 POST 给本地观测器（127.0.0.1:8799），
//   使失败原因无需人工从 SW 控制台抄录；
// - SW 若被回收，port 断开 → host 侧被 stdin EOF 回收 → 重新连会拉起新 host 进程；
// - alarm 兜底重连（MV3 alarm 最小 30s），用于测「SW 死后多久恢复」。
const HOST_NAME = "com.rpa_core.spike";
const HEARTBEAT_MS = 15000;
const RECONNECT_MS = 3000;
const ALARM_NAME = "rpa-nm-spike-reconnect";
const REPORTER = "http://127.0.0.1:8799/report";

let port = null;
let seq = 0;
let heartbeat = null;
let cachedInstanceId = "";
let connecting = false;

function report(stage, detail) {
  const payload = {
    stage,
    detail: detail || "",
    at: new Date().toISOString(),
    lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : "",
  };
  console.log("[spike]", stage, JSON.stringify(payload));
  try {
    fetch(REPORTER, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(() => {});
  } catch (err) {
    console.warn("[spike] report failed", err);
  }
}

function badge(text, color) {
  chrome.action.setBadgeText({ text });
  if (color) chrome.action.setBadgeBackgroundColor({ color });
}

function detectBrowser() {
  const ua = navigator.userAgent || "";
  if (/Edg\//.test(ua) || /EdgA\//.test(ua)) return "msedge";
  if (/OPR\//.test(ua) || /Opera/.test(ua)) return "opera";
  if (/Brave/.test(ua)) return "brave";
  if (/Firefox\//.test(ua)) return "firefox";
  if (/Chrome\//.test(ua)) return "chrome";
  return "unknown";
}

async function ensureInstanceId() {
  if (cachedInstanceId) return cachedInstanceId;
  try {
    const stored = (await chrome.storage.local.get("rpaSpikeInstanceId")).rpaSpikeInstanceId;
    if (typeof stored === "string" && stored) {
      cachedInstanceId = stored;
    } else {
      cachedInstanceId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
      await chrome.storage.local.set({ rpaSpikeInstanceId: cachedInstanceId });
    }
  } catch {
    cachedInstanceId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
  }
  return cachedInstanceId;
}

function post(type, extra) {
  if (!port) return false;
  try {
    port.postMessage({ type, seq: ++seq, at: Date.now(), ...(extra || {}) });
    console.log("[spike] ->", type, seq);
    return true;
  } catch (err) {
    report("post-failed", String(err));
    return false;
  }
}

function startHeartbeat() {
  if (heartbeat !== null) clearInterval(heartbeat);
  heartbeat = setInterval(() => {
    console.log("[spike] heartbeat tick", new Date().toISOString());
    post("ping");
  }, HEARTBEAT_MS);
}

async function connect() {
  if (port || connecting) return;
  connecting = true;
  try {
    const instanceId = await ensureInstanceId();
    if (port) return;
    report("connect-attempt", `host=${HOST_NAME} iid=${instanceId}`);
    try {
      port = chrome.runtime.connectNative(HOST_NAME);
    } catch (err) {
      port = null;
      badge("err", "#cf222e");
      report("connect-threw", String(err));
      setTimeout(connect, RECONNECT_MS);
      return;
    }
    badge("on", "#1a7f37");
    port.onMessage.addListener((msg) => {
      console.log("[spike] <-", JSON.stringify(msg));
    });
    port.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError;
      report("disconnect", err ? err.message : "no lastError");
      port = null;
      badge("off", "#cf222e");
      if (heartbeat !== null) {
        clearInterval(heartbeat);
        heartbeat = null;
      }
      setTimeout(connect, RECONNECT_MS);
    });
    post("hello", {
      browser: detectBrowser(),
      instanceId,
      extVersion: chrome.runtime.getManifest().version,
      platform: navigator.platform || "",
      userAgent: navigator.userAgent || "",
    });
    startHeartbeat();
    report("port-opened", `iid=${instanceId}`);
  } finally {
    connecting = false;
  }
}

chrome.alarms.create(ALARM_NAME, { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) {
    report("alarm-wake", "");
    connect();
  }
});

chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);

connect();
