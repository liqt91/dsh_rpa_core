// rpa_core 捕获扩展 content script（零构建 vanilla JS，与 bsk picker 同一协议）
// hover 高亮（elementsFromPoint 变体）→ 捕获手势 → background 回传；Esc 取消。
//
// 捕获手势（三者等价，判定见 isCaptureModifier / isSecondaryClick）：
//   ① ⌘ + 左键单击（macOS）/ Ctrl + 左键单击（Windows/Linux）
//   ② 右键单击（含 macOS 触控板"双指点按"）
//   ③ macOS 的 Ctrl+Click —— 系统层已把它改写成次要点击，因此实际走的是 ② 的路径。
// ③ 是最容易踩的坑：它**不会**派发 ctrlKey===true 的 click，只挂 click 监听必然失灵
// （现象：红框跟着鼠标走，但怎么点都捕获不到）。
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
  let hint = null;
  let current = null;
  let lastCaptureAt = 0;   // 同一次手势的事件去重（见 capture）

  // 平台判定**只影响提示文案**（macOS 上手势是 ⌘ 而不是 Ctrl，理由见 onContextMenu）。
  // 刻意放在纯函数区之外：该区会被 scripts/check_capture_helpers.mjs 整段求值，不该碰 navigator。
  const isMac = /Mac|iPhone|iPad/.test(navigator.userAgent || "");
  const CAPTURE_HINT = (isMac ? "⌘ + 单击" : "Ctrl + 单击") + " 或 右键捕获 · Esc 取消";
  let hintText = CAPTURE_HINT;

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

  // 捕获手势判定。为什么必须收"次要点击（右键）"：
  // macOS 在系统层把 Control+Click 改写成次要点击（Apple 的 secondary click 语义），
  // 浏览器因此只派发 mousedown(button=2) / contextmenu / auxclick，**永远不会**派发
  // ctrlKey===true 的 click —— 只监听 click 的实现在 Mac 上必然"红框在、点了没反应"。
  // 注意：contextmenu 的 button 未必是 2（Mac 的 Ctrl+Click 常见 button=0），
  // 所以判定以事件类型为准，不看 button。
  const isCaptureModifier = (e) => Boolean(e.ctrlKey || e.metaKey);
  const isSecondaryClick = (e) => e.type === "contextmenu" || e.button === 2;
  // ---- 纯函数区结束 ----

  const ensureHint = () => {
    if (!hint) {
      hint = document.createElement("div");
      hint.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;"
        + "font:12px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        + "color:#fff;background:#ff3b30;padding:2px 6px;border-radius:3px;white-space:nowrap;"
        + "box-shadow:0 1px 4px rgba(0,0,0,.35)";
      document.documentElement.appendChild(hint);
    }
    hint.textContent = hintText;
    return hint;
  };

  // 提示条文案由 setHint 维护：捕获态下发生"不生效的单击"时，把系统实际派发的事件回显出来，
  // 用户不必对着"点了没反应"猜系统改写了什么（这正是 Mac 上 Ctrl+Click 的坑）。
  const setHint = (text) => { hintText = text; if (hint) hint.textContent = text; };

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
    const tip = ensureHint();
    tip.style.left = Math.max(0, Math.min(r.left, window.innerWidth - 260)) + "px";
    tip.style.top = (r.top >= 26 ? r.top - 24 : r.top + r.height + 4) + "px";
  };

  const hideOverlay = () => {
    if (box) { box.remove(); box = null; }
    if (hint) { hint.remove(); hint = null; }
  };

  // 命中元素：跳过我们自己的两个覆盖层（它们已是 pointer-events:none，此处再兜一层）
  const topElementAt = (x, y) => document.elementsFromPoint(x, y)
    .find((n) => n !== box && n !== hint) || null;

  const onMove = (e) => {
    if (!armed) return;
    const el = topElementAt(e.clientX, e.clientY);
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

  // 真正落地一次捕获：buildDescriptor → 经 background 回传。
  const capture = (e) => {
    if (!armed) return;
    const now = Date.now();
    // 同一次手势会连发多个事件（macOS 的 mousedown→contextmenu、部分站点的 click→auxclick），
    // 只认第一个；失败路径不写时间戳，所以"通道断了"仍可再点一次重试。
    if (now - lastCaptureAt < 300) return;
    const el = topElementAt(e.clientX, e.clientY) || current;
    if (!el) return;
    // 捕获态是模态的：左键、右键都表示"捕获这个元素"，先挡掉浏览器默认行为
    // （系统右键菜单、链接新开页）
    e.preventDefault();
    e.stopPropagation();
    if (!chrome.runtime || !chrome.runtime.id) {
      // 扩展重载后，已打开页面里的旧脚本与扩展的通道已断，再点也发不出去。
      // 明确提示要刷新页面，而不是静默失败。
      setHint("扩展已重载 · 请刷新本页（⌘/Ctrl+R）后重新捕获");
      return;
    }
    try {
      chrome.runtime.sendMessage({
        type: "rpa-capture-result",
        descriptor: buildDescriptor(el),
      });
    } catch (err) {
      // 通道失效等异常：保留红框与提示，用户可再试；不要静默吞掉
      setHint("捕获失败：" + ((err && err.message) || err));
      return;
    }
    lastCaptureAt = now;
    hideOverlay();
  };

  const onClick = (e) => {
    if (!armed) return;
    if (isCaptureModifier(e)) { capture(e); return; }
    // 诊断回显：捕获态下普通左键单击不生效，把系统实际派发的事件写在提示条上，
    // 用户不必对着"点了没反应"猜系统改写了什么（Mac 的 Ctrl+Click 正是如此）。
    setHint(`未捕获：${e.type} ctrl=${e.ctrlKey} meta=${e.metaKey} button=${e.button}`
      + ` · ${CAPTURE_HINT}`);
  };

  // 次要点击（右键 / macOS 的 Ctrl+Click / 触控板双指点按）→ 捕获并挡掉系统右键菜单。
  // 同时挂 mousedown 与 contextmenu：个别站点会在 contextmenu 之前吞事件，两条路互为兜底，
  // capture 内的去重保证同一次手势只回传一次。
  const onSecondary = (e) => {
    if (!armed || !isSecondaryClick(e)) return;
    e.preventDefault();
    capture(e);
  };

  const onKey = (e) => {
    if (e.key === "Escape" && armed) {
      chrome.runtime.sendMessage({ type: "rpa-capture-cancelled" });
      hideOverlay();
    }
  };

  // 鼠标离开网页区域/窗口失焦/滚动时清掉高亮框（否则红框残留在屏幕上）
  const onLeave = () => { if (armed) hideOverlay(); };
  document.documentElement.addEventListener("mouseleave", onLeave, true);
  window.addEventListener("blur", onLeave);
  window.addEventListener("scroll", onLeave, true);

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "rpa-capture-arm") {
      armed = msg.armed === true;
      if (armed) setHint(CAPTURE_HINT); else hideOverlay();
    }
  });

  document.addEventListener("mousemove", onMove, true);
  document.addEventListener("click", onClick, true);
  document.addEventListener("mousedown", onSecondary, true);
  document.addEventListener("contextmenu", onSecondary, true);
  document.addEventListener("keydown", onKey, true);

  // 启动即同步当前捕获态：推送模型下新页面/新标签页不会自动收到此前的 arm 广播
  // （旧 HTTP 模型每 5s 重复广播，天然覆盖新页面）。
  try {
    chrome.runtime.sendMessage({ type: "rpa-capture-state" })
      .then((reply) => { if (reply && reply.armed) armed = true; })
      .catch(() => { /* SW 重启中：等后续 arm 广播 */ });
  } catch { /* 极端：runtime 不可用 */ }
})();
