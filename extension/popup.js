// popup：token 展示（自动配对后只读查看；重新生成用于轮换）
const tokenInput = document.getElementById("token");
const statusEl = document.getElementById("status");

async function load() {
  const data = await chrome.storage.local.get("rpaCaptureToken");
  tokenInput.value = data.rpaCaptureToken || "（尚未生成，捕获时自动配对）";
  if (data.rpaCaptureToken) status("已配对", true);
}

function status(text, ok) {
  statusEl.textContent = text;
  statusEl.className = ok ? "ok" : "bad";
}

document.getElementById("gen").addEventListener("click", async () => {
  const token = crypto.randomUUID();
  tokenInput.value = token;
  await chrome.storage.local.set({ rpaCaptureToken: token });
  status("已重新生成（devserver 侧需删除 workflows/.capture-extension-token 后轮换生效）", true);
});

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

load();
loadPermission();
