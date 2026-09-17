// popup：宿主身份 + bridge 连接状态 + 执行权限
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
  // 收窄模式的 tabIds/allow 列表由宿主（bridge host 命令通道）或人工写入
  await chrome.storage.local.set({ rpaExecPermission: { mode } });
  loadPermission();
});

loadPermission();

// 执行通道：宿主浏览器（= 执行通道实际使用的浏览器）+ Native Messaging bridge 连接状态
const HOST_LABELS = {
  msedge: "Edge", chrome: "Chrome", chromium: "Chromium", brave: "Brave",
  opera: "Opera", vivaldi: "Vivaldi", firefox: "Firefox", safari: "Safari",
};

async function loadHost() {
  const infoEl = document.getElementById("host-info");
  const hubEl = document.getElementById("hub-status");
  let reply = null;
  try {
    reply = await chrome.runtime.sendMessage({ type: "rpa-ext-host-info" });
  } catch { /* SW 重启中：忽略 */ }
  const host = reply && reply.host ? reply.host : null;
  const name = host ? (HOST_LABELS[host.browser] || host.browser || "未知") : "未知";
  infoEl.textContent = `宿主浏览器：${name}（执行宿主）`;
  if (reply && reply.connected) {
    hubEl.textContent = "bridge 已连接：浏览器指令将走本扩展";
    hubEl.className = "ok";
  } else {
    const detail = reply && reply.lastError ? `（${reply.lastError}）` : "";
    hubEl.textContent = "bridge 未连接：请确认已注册 host（rpa-core install-extension）"
      + `并重载本扩展${detail}`;
    hubEl.className = "bad";
  }
}

loadHost();
