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
  // 构建标识：与 background.js 的 EXT_BUILD、manifest.json 的 version 三方一致（契约测试钉住）。
  // 随捕获结果回传——诊断「页面里跑的脚本是哪个年代的」（Load unpacked 不自动重载，
  // 补注入前已开页面里的可能还是旧快照；见 background.js 顶部的完整说明）。
  const EXT_BUILD = "0.6.2";

  // ---- 实例接管守卫（M42）----------------------------------------------------
  // 声明式 content_scripts **只在页面加载时**注入：扩展装载/重载后，已经打开的标签页
  // 拿不到新脚本，页面里那个旧脚本与扩展的通道也已断开（`sendMessage` 抛
  // 「Extension context invalidated」）。维护者 2026-09-28 报障的两个现象**同源于此**：
  //   「不会在已经打开的网页上生效」—— arm 广播送不进这个页面（background 侧现在会对
  //   这类标签页补注入本脚本，见 background.js::ensureContentScript）；
  //   「捕获后网页上红框还在」—— 旧脚本收不到 capture_disarm，而它的失败分支原先是
  //   「保留红框 + 提示」（见 capture），于是红框永久赖在页面上。
  // 所以守卫不能是「装过就一律拒绝」：**只有活着的实例才拦人**（同一上下文里重复注入
  // 必须幂等——补注入会与 onUpdated 的补发、以及后续多次 arm 撞车），僵尸实例必须先
  // 拆干净（含它留在页面上的红框与监听器）再让新实例接管。
  const INSTANCE_KEY = "__rpaCaptureInstance";
  const previous = window[INSTANCE_KEY];
  if (previous) {
    if (previous.alive()) return;
    try {
      previous.teardown();
    } catch { /* 僵尸实例拆不动（上下文已失效）：继续接管，至少新实例能工作 */ }
  }

  let armed = false;
  let box = null;
  let hint = null;
  let current = null;
  let lastCaptureAt = 0;   // 同一次手势的事件去重（见 capture）
  let live = true;         // 本实例是否仍连着当前扩展（见 isAlive）
  const bound = [];        // 可回收监听器 [target, type, handler, options]，teardown 用

  /** 本实例是否仍连着**当前**扩展：扩展重载后 chrome.runtime.id 会消失。 */
  const isAlive = () => live && Boolean(chrome.runtime && chrome.runtime.id);

  /** 注册**可回收**监听器：僵尸实例不能留下监听器继续画框/抢手势。 */
  const on = (target, type, handler, options) => {
    target.addEventListener(type, handler, options);
    bound.push([target, type, handler, options]);
  };

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

  // 祖先链深度上限：与升级前的 cssSelectorFor 一致（超过就不再上溯）。
  const MAX_PATH_DEPTH = 6;

  /** 单级定位片段：给一个节点，返回它在选择器里怎么写 + 供节点树展示的原始属性。
   *
   * 这是**唯一**的片段生成处——主选择器（cssSelectorFor）与节点树（selector.path）
   * 都从这里取。两处各写一遍必然漂移，而漂移的后果是「节点树高亮的路径与真正
   * 下发执行的选择器不是同一条」。
   */
  const pathEntryFor = (node) => {
    const id = node.id || "";
    const classes = typeof node.className === "string"
      ? node.className.trim().split(/\s+/).filter(Boolean)
      : [];
    let nthOfType = null;
    let fragment;
    if (id) {
      // 带 id 即终止上溯（唯一标识，再往上写路径只会变脆）——与升级前同口径
      fragment = "#" + CSS.escape(id);
    } else {
      fragment = node.tagName.toLowerCase();
      if (classes.length) fragment += "." + CSS.escape(classes[0]);
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (same.length > 1) {
          nthOfType = same.indexOf(node) + 1;
          fragment += ":nth-of-type(" + nthOfType + ")";
        }
      }
    }
    return { tag: node.tagName.toLowerCase(), id: id || null, classes, nthOfType, fragment };
  };

  /** 根 → 目标的祖先链（每级一个 pathEntryFor 结果）。节点树用它，勾选后拼回主选择器。 */
  const pathFor = (el) => {
    if (el.id) return [pathEntryFor(el)];
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < MAX_PATH_DEPTH) {
      const entry = pathEntryFor(node);
      parts.unshift(entry);
      if (entry.id) break;
      node = node.parentElement;
    }
    return parts;
  };

  const cssSelectorFor = (el) => pathFor(el).map((entry) => entry.fragment).join(" > ");

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

  // ---- 捕获光标：按下修饰键（Ctrl/⌘）但**尚未点击**时的「就绪」视觉反馈 ----
  /** 光标样式的元素 id：既做自己的引用键，也让接管僵尸实例时能按 id 清掉残留。 */
  const CURSOR_STYLE_ID = "rpa-capture-cursor";
  /** 蓝色箭头（与手势同色系）：viewBox 24×24，热点取箭头尖 (4,2)。 */
  const CAPTURE_CURSOR_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
    + ' viewBox="0 0 24 24"><path d="M5 2 L5 19 L9.5 14.8 L12.3 21.5 L14.8 20.4 L12 13.9 L18 13.6 Z"'
    + ' fill="%232F6BFF" stroke="%23ffffff" stroke-width="1.2" stroke-linejoin="round"/></svg>';
  /** 该按键是否代表「捕获修饰键已按下」——与 isCaptureModifier 同口径（Ctrl 或 ⌘）。 */
  const cursorKeyOf = (e) => {
    const key = e && e.key;
    if (key === "Control") return "ctrl";
    if (key === "Meta") return "meta";
    return null;
  };
  /** 光标样式文本。`!important` 压过站点自己的 cursor 规则（含 `*` 兜底）。 */
  const cursorStyleText = () => "* { cursor: url('data:image/svg+xml,"
    + CAPTURE_CURSOR_SVG + "') 4 2, auto !important; }";

  // [capture-overlay-geometry:start]
  // 捕获高亮框几何（M41）：框线躲开鼠标指针。
  // 维护者 2026-09-28：「捕获元素的框跟影刀的一样，躲着鼠标，否则影响其他元素的捕捉」。
  // 旧实现是 border:2px 紧贴元素 + 12% 红填充——框线落在元素边界上、填充又把元素内容
  // 整片染红；桌面侧同款几何实测 6/13 个采样点的指针被框线像素覆盖（最近距离 0）。
  // 现行几何：**框线带完全位于元素之外** [OUTSET-BORDER, OUTSET) = [2,5)，元素内容与
  // 鼠标指针都不再被覆盖，且去掉填充。两个通道几何一致（桌面 overlay_bounds 同款）。
  // 本区由 scripts/check_capture_overlay_geometry.mjs 按锚点切片求值校验。
  const OVERLAY_BORDER = 3;
  const OVERLAY_OUTSET = 5;

  // 元素 rect → 高亮框的 CSS 盒（border-box）：外扩 OUTSET 后 3px 边框带恰好落在
  // 元素外 [2,5)，指针贴边时与框线仍隔 2px（= 指针热区）。
  const overlayBoxRect = (r) => ({
    left: r.left - OVERLAY_OUTSET,
    top: r.top - OVERLAY_OUTSET,
    width: r.width + OVERLAY_OUTSET * 2,
    height: r.height + OVERLAY_OUTSET * 2,
  });
  // [capture-overlay-geometry:end]
  // [verify-helpers:start]
  // 活体校验（M47）与编辑预览（M48）的纯求值部分：拿文档与 css 算出「命中几个、
  // 该闪几个 / 该驻留几个」。校验**不依赖 armed 态**——捕获完成后页面已撤防，
  // 而校验只需要 content script 可达。mode：flash（默认）/ preview / clear。
  const VERIFY_FLASH_LIMIT = 20;
  const VERIFY_MODES = ["flash", "preview", "clear"];
  const normalizeVerifyMode = (mode) =>
    VERIFY_MODES.includes(mode) ? mode : "flash";
  const matchCountFor = (doc, css) => {
    try {
      return { count: doc.querySelectorAll(css).length };
    } catch {
      return { error: "invalid-selector" };
    }
  };
  const verifyReplyFor = (doc, css, mode) => {
    mode = normalizeVerifyMode(mode);
    if (mode === "clear") return { count: 0 };
    const result = matchCountFor(doc, css);
    if (result.error) return result;
    if (mode === "preview") return result;
    return { ...result, flash: Math.min(result.count, VERIFY_FLASH_LIMIT) };
  };
  // [verify-helpers:end]
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
      // 只画框线、不填充（M41）：填充会把元素内容整片染红，指针下的东西看不真切。
      box.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;"
        + `box-sizing:border-box;border:${OVERLAY_BORDER}px solid #ff3b30`;
      document.documentElement.appendChild(box);
    }
    const r = el.getBoundingClientRect();
    const frame = overlayBoxRect(r);   // 外扩：框线带落在元素之外，不压指针
    box.style.left = frame.left + "px";
    box.style.top = frame.top + "px";
    box.style.width = frame.width + "px";
    box.style.height = frame.height + "px";
    // 提示条留在元素**外侧**（上方 24px，顶不下时翻到下方）：指针在元素内时它天然
    // 压不到指针，因此不参与「躲鼠标」的外扩（只跟随元素，不跟随鼠标）。
    const tip = ensureHint();
    tip.style.left = Math.max(0, Math.min(r.left, window.innerWidth - 260)) + "px";
    tip.style.top = (r.top >= 26 ? r.top - 24 : r.top + r.height + 4) + "px";
  };

  /** 只收红框、保留提示条：失败路径用（用户要看得到原因，但红框不能赖着不走）。 */
  const hideBox = () => { if (box) { box.remove(); box = null; } };

  const hideOverlay = () => {
    hideBox();
    if (hint) { hint.remove(); hint = null; }
  };

  // 捕获光标：armed 期间按下 Ctrl/⌘ 就变蓝，松开/撤防/收摊即还原。
  // **不写进页面自己的样式**（只加一个 <style> 元素），移除即完全还原——页面站的 cursor
  // 规则一条都不动。三个出口收口在这里，别处只管调。
  let cursorStyle = null;
  const showCaptureCursor = () => {
    if (cursorStyle || !isAlive()) return;
    const node = document.createElement("style");
    node.id = CURSOR_STYLE_ID;
    node.textContent = cursorStyleText();
    document.documentElement.appendChild(node);
    cursorStyle = node;
  };
  const clearCaptureCursor = () => {
    // 先用**自己的引用**移除（与 hideBox 同款）：不能只靠 getElementById——那一步
    // 一旦不可用（桩环境/异常时机），引用被清空而节点留在页面上，光标就赖着不走了。
    if (cursorStyle) { cursorStyle.remove(); cursorStyle = null; }
    // 再按 id 兜一次：接管僵尸实例时，旧实例留下的那个 <style> 引用已不可达
    const stale = document.getElementById(CURSOR_STYLE_ID);
    if (stale) stale.remove();
  };

  // 收摊：清覆盖层 + 摘掉全部监听 + 让出实例位（补注入的新实例据此接管）。
  // 由两条路径调用：① 新实例发现本实例已是僵尸（见文件顶部守卫）；② 本实例自己发现
  // 上下文失效（见 onMove）。`instance` / `onRuntimeMessage` 定义在下方，teardown 只在
  // 接管或事件里求值，那时两者都已初始化。
  const teardown = () => {
    if (!live) return;
    live = false;
    hideOverlay();
    clearCaptureCursor();
    for (const [target, type, handler, options] of bound.splice(0)) {
      try {
        target.removeEventListener(type, handler, options);
      } catch { /* 页面已卸载：摘不掉也无所谓 */ }
    }
    try {
      chrome.runtime.onMessage.removeListener(onRuntimeMessage);
    } catch { /* 上下文已失效：监听器随上下文一起废弃 */ }
    if (window[INSTANCE_KEY] === instance) delete window[INSTANCE_KEY];
  };

  // 命中元素：跳过我们自己的两个覆盖层（它们已是 pointer-events:none，此处再兜一层）
  const topElementAt = (x, y) => document.elementsFromPoint(x, y)
    .find((n) => n !== box && n !== hint) || null;

  const onMove = (e) => {
    if (!armed) return;
    // 扩展重载后本实例已是僵尸：画了框也捕获不到，只会留下清不掉的残留（M42 报障
    // 「捕获后网页上红框还在」就是这个形状）。不再画，等补注入的新实例接管。
    if (!isAlive()) { hideOverlay(); return; }
    const el = topElementAt(e.clientX, e.clientY);
    if (el) { current = el; show(el); }
  };

  const buildDescriptor = (el) => {
    const css = cssSelectorFor(el);
    const r = el.getBoundingClientRect();
    return {
      kind: "browser",
      // path = 祖先链（每级 {tag, id, classes, nthOfType, fragment}）：元素编辑器据它画
      // 节点树，用户勾选层级后按 fragment join(" > ") 拼回 css。与 css **同源**
      // （都出自 pathFor），所以树上高亮的路径与真正下发执行的选择器必然是同一条。
      selector: { css, path: pathFor(el), candidates: candidatesFor(el, css) },
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
      // **红框必须收掉**：它不会再有 disarm 来清（通道断了），留着就是永久残留，
      // 用户会以为还在捕获态（M42 报障「捕获后网页上红框还在」）。提示条留着说明
      // 原因——下次 arm 广播时 background 会补注入新脚本接管本页，用户无需做任何事。
      hideBox();
      setHint("扩展已重载 · 请刷新本页（⌘/Ctrl+R）后重新捕获");
      return;
    }
    try {
      chrome.runtime.sendMessage({
        type: "rpa-capture-result",
        descriptor: buildDescriptor(el),
        contentBuild: EXT_BUILD,
      });
    } catch (err) {
      // 通道失效等异常：同上下——红框不能赖着不走（它已经不会被任何人清掉），
      // 提示条保留原因，用户可再试；不要静默吞掉
      hideBox();
      setHint("捕获失败：" + ((err && err.message) || err));
      return;
    }
    lastCaptureAt = now;
    hideOverlay();
    clearCaptureCursor();   // 已捕获：不再处于「就绪待点」状态
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
    // 按下 Ctrl/⌘（尚未点击）→ 光标变蓝：这是「现在点下去就捕获」的就绪反馈。
    if (armed && cursorKeyOf(e)) showCaptureCursor();
    if (e.key === "Escape" && armed) {
      hideOverlay();   // 先收覆盖层：掉线时 sendMessage 会抛，否则红框会卡在屏幕上
      clearCaptureCursor();
      try {
        chrome.runtime.sendMessage({ type: "rpa-capture-cancelled" });
      } catch {
        /* 扩展重载后旧脚本孤立：本地已收场，无需上报 */
      }
    }
  };

  // 松开修饰键即还原光标。**无条件清**（不判 armed）：arm 在按着键的过程中被撤掉时，
  // keyup 是最后一个能把光标收回去的时机。
  const onKeyUp = (e) => {
    if (cursorKeyOf(e)) clearCaptureCursor();
  };

  // 鼠标离开网页区域/窗口失焦/滚动时清掉高亮框（否则红框残留在屏幕上）
  const onLeave = () => { if (armed) hideOverlay(); };
  on(document.documentElement, "mouseleave", onLeave, true);
  on(window, "blur", onLeave);
  on(window, "scroll", onLeave, true);

  // 活体校验（M47）：按 css 现场查找并**黄框**闪烁命中元素（与捕获红框区分：
  // 黄色 + 更低 z-index，捕获态的红框永远压在校验框上面）。独立于 armed 态、
  // 独立于捕获覆盖层（自己的元素数组 + 自己的清除），超时自动消失。
  let verifyBoxes = [];
  const clearVerifyFlash = () => {
    for (const node of verifyBoxes) {
      try { node.remove(); } catch { /* 页面已卸载 */ }
    }
    verifyBoxes = [];
  };
  const flashElements = (els, persist) => {
    clearVerifyFlash();
    // absolute + 文档坐标（视口坐标 + 滚动偏移）：滚动/滚轮时黄框跟着内容走。
    // 旧实现 position:fixed + 视口坐标，滚动后框钉在屏幕上、元素却滚走了
    // （维护者实测「网页滚动时黄框不跟随」，很别扭）。
    const sx = window.scrollX || window.pageXOffset || 0;
    const sy = window.scrollY || window.pageYOffset || 0;
    for (const el of els) {
      const node = document.createElement("div");
      node.style.cssText = "position:absolute;z-index:2147483646;pointer-events:none;"
        + "box-sizing:border-box;border:3px solid #d4a017;background:rgba(255,215,0,.15)";
      const r = el.getBoundingClientRect();
      node.style.left = (r.left + sx - 2) + "px";
      node.style.top = (r.top + sy - 2) + "px";
      node.style.width = (r.width + 4) + "px";
      node.style.height = (r.height + 4) + "px";
      document.documentElement.appendChild(node);
      verifyBoxes.push(node);
    }
    if (!persist) setTimeout(clearVerifyFlash, 1600);
  };
  // M48：mode 决定高亮的「寿命」。flash 闪 1.6s 自清；preview 驻留（persist），
  // **count=0 也必须先清场**——选择器改到不再命中的瞬间，上一轮的黄框若还赖着，
  // 用户会把「旧框」读成「新选择器命中了」，这是预览最危险的静默误导；clear 只清场。
  const runVerify = (css, mode) => {
    mode = normalizeVerifyMode(mode);
    const reply = verifyReplyFor(document, css, mode);
    if (mode === "clear") {
      clearVerifyFlash();
      return reply;
    }
    if (mode === "preview") clearVerifyFlash();
    if (!reply.error && reply.count > 0) {
      flashElements(
        Array.from(document.querySelectorAll(css)).slice(0, VERIFY_FLASH_LIMIT),
        mode === "preview"
      );
    }
    return reply;
  };

  const onRuntimeMessage = (msg, _sender, sendResponse) => {
    if (msg && msg.type === "rpa-capture-verify") {
      // 同步应答：querySelectorAll 是同步的，无需 return true（那是异步应答的写法）
      sendResponse({
        contentBuild: EXT_BUILD,
        ...runVerify(String(msg.css || ""), msg.mode),
      });
      return false;
    }
    if (msg && msg.type === "rpa-capture-arm") {
      armed = msg.armed === true;
      if (armed) {
        // 进捕获态先清掉遗留的预览黄框：捕获红框下面压着上一轮的黄框，
        // 用户分不清哪些框是「这次框选的」哪些是「刚才预览剩的」。
        clearVerifyFlash();
        setHint(CAPTURE_HINT);
      } else {
        hideOverlay();
        clearCaptureCursor();   // 撤防：就绪光标必须跟着走，否则它会赖在整个页面上
      }
    }
  };
  chrome.runtime.onMessage.addListener(onRuntimeMessage);

  on(document, "mousemove", onMove, true);
  on(document, "click", onClick, true);
  on(document, "mousedown", onSecondary, true);
  on(document, "contextmenu", onSecondary, true);
  on(document, "keydown", onKey, true);
  on(document, "keyup", onKeyUp, true);

  // 登记本实例：补注入的新脚本据此判断「拦下还是接管」（见文件顶部守卫）。
  const instance = { alive: isAlive, teardown };
  window[INSTANCE_KEY] = instance;

  // 启动即同步当前捕获态：推送模型下新页面/新标签页不会自动收到此前的 arm 广播
  // （旧 HTTP 模型每 5s 重复广播，天然覆盖新页面）。补注入的脚本也靠这一步立刻
  // 跟上「此刻是否在捕获态」，不必等下一次 arm。
  try {
    chrome.runtime.sendMessage({ type: "rpa-capture-state" })
      .then((reply) => { if (reply && reply.armed) armed = true; })
      .catch(() => { /* SW 重启中：等后续 arm 广播 */ });
  } catch { /* 极端：runtime 不可用 */ }
})();
