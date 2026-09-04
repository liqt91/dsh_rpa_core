// rpa_core 捕获扩展 background service worker
// 短轮询 devserver pending 状态（捕获期间激活），把 content script 的捕获结果
// POST 回 devserver。token 配对一次（popup 输入，chrome.storage.local 持久）。
const DEVSERVER = "http://127.0.0.1:8765";
const POLL_ACTIVE_MS = 1200;   // 有待捕获会话时
const POLL_IDLE_MS = 5000;     // 空闲时
let pollMs = POLL_IDLE_MS;

async function getToken() {
  const data = await chrome.storage.local.get("rpaCaptureToken");
  return data.rpaCaptureToken || "";
}

async function poll() {
  const token = await getToken();
  if (!token) { schedule(); return; }
  try {
    const resp = await fetch(`${DEVSERVER}/api/capture/extension/pending`, {
      headers: { "X-Capture-Token": token },
    });
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
  const token = await getToken();
  if (!token) return;
  try {
    await fetch(`${DEVSERVER}/api/capture/extension/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Capture-Token": token },
      body: JSON.stringify(descriptor),
    });
    pollMs = POLL_IDLE_MS;
    await broadcast(false);
  } catch { /* devserver 不在线，丢弃 */ }
}

// 启动即开始轮询
poll();
