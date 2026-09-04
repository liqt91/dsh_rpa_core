// popup：token 配对（一次性）
const tokenInput = document.getElementById("token");
const statusEl = document.getElementById("status");

async function load() {
  const data = await chrome.storage.local.get("rpaCaptureToken");
  tokenInput.value = data.rpaCaptureToken || "";
  if (tokenInput.value) status("已配对", true);
}

function status(text, ok) {
  statusEl.textContent = text;
  statusEl.className = ok ? "ok" : "bad";
}

document.getElementById("save").addEventListener("click", async () => {
  const token = tokenInput.value.trim();
  if (!token) { status("token 不能为空", false); return; }
  await chrome.storage.local.set({ rpaCaptureToken: token });
  status("已配对", true);
});

document.getElementById("gen").addEventListener("click", async () => {
  const token = crypto.randomUUID();
  tokenInput.value = token;
  await chrome.storage.local.set({ rpaCaptureToken: token });
  status("已生成并保存", true);
});

load();
