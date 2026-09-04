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

load();
