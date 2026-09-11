// popup：宿主身份 + 执行权限（配对机制已移除，见 background.js 顶部注释）
// 执行权限（默认整个浏览器；tabs/origins 为预留收窄模式，需配合写入范围字段）
const permSelect = document.getElementById("perm");
const permStatus = document.getElementById("perm-status");

async function loadPermission() {
  const data = await chrome.storage.local.get("rpaExecPermission");
  const perm = data.rpaExecPermission || { mode: "browser" };
  permSelect.value = perm.mode || "browser";
  permStatus.textContent = perm.mode === "browser" || !perm.mode
    ? "范围：全部窗口 / 全部标签页 / 全部 Cookie"
    : `范围：${JSON.stringify(perm)}（收窄模式，由宿主配置下发）`;
}

permSelect.addEventListener("change", async () => {
  const mode = permSelect.value;
  // 收窄模式的 tabIds/allow 列表由宿主（devserver /api/ext/permissions）或人工写入
  await chrome.storage.local.set({ rpaExecPermission: { mode } });
  loadPermission();
});

loadPermission();

// 执行通道：宿主浏览器（= 执行通道实际使用的浏览器）+ devserver 识别状态
const HOST_LABELS = {
  msedge: "Edge", chrome: "Chrome", chromium: "Chromium", brave: "Brave",
  opera: "Opera", vivaldi: "Vivaldi", firefox: "Firefox", safari: "Safari",
};

async function loadHost() {
  const infoEl = document.getElementById("host-info");
  const hubEl = document.getElementById("hub-status");
  let host = null;
  let devserverUrl = "http://127.0.0.1:8765";
  try {
    const reply = await chrome.runtime.sendMessage({ type: "rpa-ext-host-info" });
    if (reply) {
      host = reply.host || null;
      devserverUrl = reply.devserver || devserverUrl;
    }
  } catch { /* SW 重启中：忽略 */ }
  const name = host ? (HOST_LABELS[host.browser] || host.browser || "未知") : "未知";
  infoEl.textContent = `宿主浏览器：${name}（channel 校验依据）`;
  try {
    const resp = await fetch(`${devserverUrl}/api/ext/status`);
    const data = await resp.json();
    hubEl.textContent = data.online
      ? `devserver 已识别：在线（浏览器指令将走本扩展）`
      : `devserver 已连上，但尚未识别本扩展：保持本页/devserver 存活几秒后重开`;
    hubEl.className = data.online ? "ok" : "bad";
  } catch {
    hubEl.textContent = "devserver 未运行：编辑器「▶ 运行」时会自动拉起；浏览器指令暂回退 playwright";
    hubEl.className = "bad";
  }
}

loadHost();
