// rpa_core 捕获扩展 content script（零构建 vanilla JS，与 bsk picker 同一协议）
// hover 高亮（elementsFromPoint 变体）→ Ctrl+Click 捕获 → background 回传；Esc 取消。
(() => {
  if (window.__rpaCaptureInstalled) return;
  window.__rpaCaptureInstalled = true;

  let armed = false;
  let box = null;
  let current = null;

  const cssSelectorFor = (el) => {
    if (el.id) return "#" + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift("#" + CSS.escape(node.id)); break; }
      if (node.className && typeof node.className === "string") {
        const cls = node.className.trim().split(/\s+/).filter(Boolean)[0];
        if (cls) part += "." + CSS.escape(cls);
      }
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(node) + 1) + ")";
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(" > ");
  };

  const show = (el) => {
    if (!box) {
      box = document.createElement("div");
      box.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;"
        + "border:2px solid #ff3b30;background:rgba(255,59,48,.12)";
      document.documentElement.appendChild(box);
    }
    const r = el.getBoundingClientRect();
    box.style.left = r.left + "px";
    box.style.top = r.top + "px";
    box.style.width = r.width + "px";
    box.style.height = r.height + "px";
  };

  const hideBox = () => { if (box) { box.remove(); box = null; } };

  const onMove = (e) => {
    if (!armed) return;
    const stack = document.elementsFromPoint(e.clientX, e.clientY);
    const el = stack.find((n) => n !== box);
    if (el) { current = el; show(el); }
  };

  const onClick = (e) => {
    if (!armed || !e.ctrlKey) return;
    const stack = document.elementsFromPoint(e.clientX, e.clientY);
    const el = stack.find((n) => n !== box) || current;
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    const css = cssSelectorFor(el);
    const r = el.getBoundingClientRect();
    const descriptor = {
      kind: "browser",
      selector: { css },
      verifyCount: document.querySelectorAll(css).length,
      metadata: {
        tag: el.tagName.toLowerCase(),
        id: el.id || null,
        classes: (typeof el.className === "string"
          ? el.className.trim().split(/\s+/).filter(Boolean) : []),
        text: (el.textContent || "").trim().slice(0, 80),
        rect: { x: Math.round(r.x), y: Math.round(r.y),
                width: Math.round(r.width), height: Math.round(r.height) },
      },
      url: location.href,
    };
    chrome.runtime.sendMessage({ type: "rpa-capture-result", descriptor });
    hideBox();
  };

  const onKey = (e) => {
    if (e.key === "Escape" && armed) {
      chrome.runtime.sendMessage({ type: "rpa-capture-cancelled" });
      hideBox();
    }
  };

  // 鼠标离开网页区域/窗口失焦/滚动时清掉高亮框（否则红框残留在屏幕上）
  const onLeave = () => { if (armed) hideBox(); };
  document.documentElement.addEventListener("mouseleave", onLeave, true);
  window.addEventListener("blur", onLeave);
  window.addEventListener("scroll", onLeave, true);

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "rpa-capture-arm") {
      armed = msg.armed === true;
      if (!armed) hideBox();
    }
  });

  document.addEventListener("mousemove", onMove, true);
  document.addEventListener("click", onClick, true);
  document.addEventListener("keydown", onKey, true);
})();
