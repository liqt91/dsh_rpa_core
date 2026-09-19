// rpa_core 捕获扩展 content script（零构建 vanilla JS，与 bsk picker 同一协议）
// hover 高亮（elementsFromPoint 变体）→ Ctrl+Click 捕获 → background 回传；Esc 取消。
//
// 描述符形态（M10 元素库契约）：
//   selector.css        —— 必填、第一顺位，语义与本文件升级前完全一致（不破坏既有工作流）
//   selector.candidates —— 新增、可选；CSS 可解析的**备选**定位（不含主 css 本身），
//                          每条带捕获时实测的 matchedCount，供将来元素自愈按稳定性排序
//   metadata            —— 新增语义特征（role/可访问名/placeholder/label/容器文本/页面指纹）
//
// 为什么是"属性"而不是"序号"：序号（第几个节点）只活当次快照，页面一变就指向别的东西；
// 只有页面自身的属性能在站点改版后仍然存在。详见 .harness/adr 与 .workbuddy/memory 记录。
//
// role 归一化与可访问名的取法参照 browser-use/jev-ultrafast 的 snapshot.js（MIT）——
// 该实现把"可访问名算法放在快照侧、不交给模型"当作硬原则；此处沿用同一优先级链。
(() => {
  if (window.__rpaCaptureInstalled) return;
  window.__rpaCaptureInstalled = true;

  let armed = false;
  let box = null;
  let current = null;

  // ---- 纯函数区（scripts/check_capture_helpers.mjs 按首尾锚点切片校验，勿在其中插入副作用） ----
  const ROLE_NAMES = [
    "button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "menuitemradio",
    "option", "gridcell", "combobox", "textbox", "searchbox", "spinbutton",
  ];

  // 归一化 role：显式 role 白名单优先，否则按标签与 input type 推导；推不出返回 null。
  const roleOf = (el) => {
    const explicit = el.getAttribute("role");
    if (ROLE_NAMES.includes(explicit)) return explicit;
    const tag = el.tagName;
    if (tag === "BUTTON" || tag === "SUMMARY") return "button";
    if (tag === "A") return "link";
    if (tag === "SELECT") return "combobox";
    if (tag === "TEXTAREA" || el.isContentEditable) return "textbox";
    if (tag === "INPUT") {
      const type = (el.type || "text").toLowerCase();
      if (type === "checkbox" || type === "radio") return type;
      if (["button", "submit", "reset", "image"].includes(type)) return "button";
      if (type === "search") return "searchbox";
      if (type === "number") return "spinbutton";
      if (["text", "email", "url", "tel"].includes(type)) return "textbox";
    }
    return null;
  };

  // 可访问名：按 ARIA 优先级链取（aria-labelledby → aria-label → <label> → value → alt
  // → 文本内容 → title → placeholder）。对输入框尤其重要——它们的 textContent 是空串。
  const accessibleName = (el, seen = new Set()) => {
    if (!el || seen.has(el)) return "";
    seen.add(el);
    const referenced = (el.getAttribute("aria-labelledby") || "").split(/\s+/)
      .map((id) => (id ? document.getElementById(id) : null))
      .filter(Boolean)
      .map((ref) => accessibleName(ref, seen))
      .filter(Boolean)
      .join(" ");
    if (referenced) return referenced.trim();
    const ariaLabel = el.getAttribute("aria-label");
    if (ariaLabel) return ariaLabel.trim();
    const labelled = [...(el.labels || [])]
      .map((node) => accessibleName(node, seen)).filter(Boolean).join(" ");
    if (labelled) return labelled.trim();
    if (["button", "submit", "reset"].includes((el.type || "").toLowerCase()) && el.value) {
      return String(el.value).trim();
    }
    const alt = el.getAttribute("alt");
    if (alt) return alt.trim();
    if (el.tagName !== "INPUT") {
      const text = [...el.childNodes].map((node) => {
        if (node.nodeType === 3) return node.textContent;
        if (node.nodeType === 1 && node.getAttribute("aria-hidden") !== "true") {
          return accessibleName(node, seen);
        }
        return "";
      }).join(" ").trim();
      if (text) return text;
    }
    return (el.getAttribute("title") || el.getAttribute("placeholder") || "").trim();
  };

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

  // CSS 属性选择器的值需要转义 \ 与 "（否则含引号的 aria-label 会拼出非法选择器）。
  const attrValue = (value) => String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');

  const attrSelectorFor = (el, name) => {
    const raw = el.getAttribute(name);
    if (!raw) return null;
    return el.tagName.toLowerCase() + "[" + name + '="' + attrValue(raw) + '"]';
  };

  // 备选定位：只收 CSS 可解析的形式（将来 resolver 复用现有 querySelectorAll 机制即可），
  // 全部实测 matchedCount；捕获时就不命中或与主 css 重复的不留。
  const CANDIDATE_ATTRS = [
    "data-testid", "data-test", "data-qa", "name", "aria-label", "placeholder", "title",
  ];

  const candidatesFor = (el, primary) => {
    const out = [];
    const seenValues = new Set(primary ? [primary] : []);
    const push = (kind, selector) => {
      if (!selector || seenValues.has(selector)) return;
      let matched = 0;
      try {
        matched = document.querySelectorAll(selector).length;
      } catch {
        return;  // 非法选择器（罕见字符）不值得冒险回传
      }
      if (!matched) return;
      seenValues.add(selector);
      out.push({ kind, selector, matchedCount: matched });
    };
    if (el.id) push("id", "#" + CSS.escape(el.id));
    for (const name of CANDIDATE_ATTRS) push("attribute", attrSelectorFor(el, name));
    return out;
  };

  // 程序化关联的 <label> 文本（比可访问名更"显式"，是跨改版的强信号）。
  const labelTextOf = (el) => {
    const text = [...(el.labels || [])]
      .map((node) => node.innerText || node.textContent || "")
      .join(" ").replace(/\s+/g, " ").trim();
    return text.slice(0, 120);
  };

  // 所属容器文本：同名元素成群时用来消歧（表单/对话框/列表项/表格行）。
  const SCOPE_SELECTOR = 'form,dialog,[role="dialog"],article,li,tr,[role="row"]';

  const containerTextOf = (el) => {
    const scope = el.closest(SCOPE_SELECTOR) || el.parentElement;
    if (!scope) return "";
    const text = String(scope.innerText || scope.textContent || "")
      .replace(/\s+/g, " ").trim();
    return text.slice(0, 200);
  };
  // ---- 纯函数区结束 ----

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

  const buildDescriptor = (el) => {
    const css = cssSelectorFor(el);
    const r = el.getBoundingClientRect();
    return {
      kind: "browser",
      selector: { css, candidates: candidatesFor(el, css) },
      verifyCount: document.querySelectorAll(css).length,
      metadata: {
        tag: el.tagName.toLowerCase(),
        id: el.id || null,
        classes: (typeof el.className === "string"
          ? el.className.trim().split(/\s+/).filter(Boolean) : []),
        text: (el.textContent || "").trim().slice(0, 80),
        rect: { x: Math.round(r.x), y: Math.round(r.y),
                width: Math.round(r.width), height: Math.round(r.height) },
        // 语义特征与页面指纹：放在 metadata 里（selector 之外的顶层键会被契约丢弃）
        role: roleOf(el),
        accessibleName: accessibleName(el) || null,
        placeholder: el.getAttribute("placeholder") || null,
        label: labelTextOf(el) || null,
        containerText: containerTextOf(el) || null,
        url: location.href,
        title: document.title || null,
      },
    };
  };

  const onClick = (e) => {
    if (!armed || !e.ctrlKey) return;
    const stack = document.elementsFromPoint(e.clientX, e.clientY);
    const el = stack.find((n) => n !== box) || current;
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    chrome.runtime.sendMessage({ type: "rpa-capture-result", descriptor: buildDescriptor(el) });
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

  // 启动即同步当前捕获态：推送模型下新页面/新标签页不会自动收到此前的 arm 广播
  // （旧 HTTP 模型每 5s 重复广播，天然覆盖新页面）。
  try {
    chrome.runtime.sendMessage({ type: "rpa-capture-state" })
      .then((reply) => { if (reply && reply.armed) armed = true; })
      .catch(() => { /* SW 重启中：等后续 arm 广播 */ });
  } catch { /* 极端：runtime 不可用 */ }
})();
