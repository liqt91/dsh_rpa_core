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
  const EXT_BUILD = "0.11.0";

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

  // 主 css 的深度上限：与升级前的 cssSelectorFor 一致（超过就不再上溯）。
  const MAX_PATH_DEPTH = 6;
  // 编辑器节点树的深度上限：维护者要「目标 → 页面根」的**完整**祖先链（M69），
  // 40 级对真实页面足够；主 css 不受它影响（仍走短链）。
  const MAX_TREE_DEPTH = 40;

  /** 单级定位片段：给一个节点，返回它在选择器里怎么写 + 供节点树展示的原始属性。
   *
   * 这是**唯一**的片段生成处——主选择器（cssSelectorFor）与节点树（selector.path）
   * 都从这里取。两处各写一遍必然漂移，而漂移的后果是「节点树高亮的路径与真正
   * 下发执行的选择器不是同一条」。
   */
  // 节点自身的 class 列表（SVG 元素的 className 不是字符串，按「无 class」处理）。
  const classListOf = (node) => (typeof node.className === "string"
    ? node.className.trim().split(/\s+/).filter(Boolean)
    : []);

  const pathEntryFor = (node) => {
    // M105：id 只有「稳定」（影刀 isStableValue）才当锚点；含数字/哈希的 id 与构建哈希
    // class 一样属于运行期值，改走 class 判别。等价影刀「临时摘掉不稳定 id 再构建」，
    // 但不真改 DOM（真改会踩到站点自己的脚本）。
    const id = stableIdOf(node);
    const classes = classListOf(node);
    let nthOfType = null;
    let fragment;
    const usedClasses = [];
    if (id) {
      fragment = "#" + CSS.escape(id);
    } else {
      fragment = node.tagName.toLowerCase();
      const parent = node.parentElement;
      const same = parent
        ? Array.from(parent.children).filter((c) => c.tagName === node.tagName) : [];
      // 影刀 handleClassAttr：剔状态类/哈希后缀后逐个 class 累加，先看哪个组合把本层
      // 从同标签兄弟里分出来；不再固定「首类」。裁过哈希的 class 用 [class*="base"]。
      let ambiguous = true;
      for (const cls of stableClassesOf(node)) {
        const piece = cls.trimmed
          ? `[class*="${attrValue(cls.value)}"]` : "." + CSS.escape(cls.value);
        fragment += piece;
        // 勾选态用原始 class 名（哈希被裁掉时编辑器仍勾得到那一类）；fragment 用 base。
        usedClasses.push(cls.original);
        const hits = same.filter((c) => cls.trimmed
          ? String(c.className || "").includes(cls.value)
          : classListOf(c).includes(cls.value)).length;
        if (hits <= 1) { ambiguous = false; break; }
      }
      // 只有 class 组合仍分不开时才补位置序号（同构列表靠它区分，特征 class 不再被塞号）。
      if (same.length > 1 && ambiguous) {
        nthOfType = same.indexOf(node) + 1;
        fragment += ":nth-of-type(" + nthOfType + ")";
      }
    }
    return {
      tag: node.tagName.toLowerCase(), id: id || null, classes, usedClasses, nthOfType, fragment,
    };
  };

  /** 目标 → 页面根的**完整**祖先链（每级一个 pathEntryFor 结果）。
   *
   * 这是编辑器节点树的素材（M69：用户要看全部层级、决定哪些参与定位），
   * **不再**到 id / 6 级就停——主 css 的短链由 cssChainFor 单独取，两者共享
   * 同一条全链（见 buildDescriptor 的 inCss 标记），不会出现「树上路径与
   * 真正下发的选择器对不上」。
   */
  const pathFor = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < MAX_TREE_DEPTH) {
      parts.unshift(pathEntryFor(node));
      node = node.parentElement;
    }
    return parts;
  };

  /** 主 css 用链的起点：从链尾（目标）往根数，带 id 即停、最多 MAX_PATH_DEPTH 级。 */
  const cssStartFor = (chain) => {
    let count = 0;
    for (let index = chain.length - 1; index >= 0; index--) {
      count += 1;
      if (chain[index].id || count >= MAX_PATH_DEPTH) return index;
    }
    return 0;
  };

  /** 主 css 用的短链（带 id 即停 / 最多 6 级），candidates 的「更短写法」也从它截。 */
  const cssChainFor = (el) => {
    if (el.id) return [pathEntryFor(el)];
    const chain = pathFor(el);
    return chain.slice(cssStartFor(chain));
  };

  const cssSelectorFor = (el) => cssChainFor(el).map((entry) => entry.fragment).join(" > ");

  // CSS 属性选择器的值需要转义 \ 与 "（否则含引号的 aria-label 会拼出非法选择器）。
  const attrValue = (value) => String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');

  const attrSelectorFor = (el, name) => {
    const raw = el.getAttribute(name);
    if (!raw) return null;
    return el.tagName.toLowerCase() + "[" + name + '="' + attrValue(raw) + '"]';
  };

  // ---- M105：影刀式筛选器优化（纯函数层） -----------------------------------
  // 信源：影刀网页元素筛选器优化报告 §1~§6（ADR 0023）。原则：过滤易变原料 →
  // 按属性优先级贪心试到唯一 → 删冗余节点 → 清洗哈希/状态值。所有新写法都实测
  // matchedCount，交给 M52 的排序引擎择优；主 CSS 仍由 pathEntryFor 生成，保证
  // 编辑器层级勾选模型可表达（XPath / shadow 只做候选）。
  const HTML_NS = "http://www.w3.org/1999/xhtml";
  const ATTR_NAME_RE = /^[A-Za-z_][A-Za-z0-9_.-]*$/;
  const STATE_CLASS_RE = /hover|[^a-zA-Z]+(?:on|open|active)|active/i;
  const HASH_SUFFIX_RE = /(-{2,}|_{2,})[a-zA-Z\d]{5,}$/;

  // 影刀 isStableValue：按 -/_ 切分后每段必须全字母；含数字即视为运行期生成。
  const isStableValue = (value) => {
    const text = String(value || "").trim();
    if (!text) return true;
    return text.split(/[-_]+/).every((token) => /^[a-zA-Z]+$/.test(token));
  };

  // 不稳定 id 不进候选、也不当主锚点（等价影刀「临时摘掉再构建」，但不真改 DOM）。
  const stableIdOf = (node) => {
    const id = node.id || "";
    return id && isStableValue(id) ? id : "";
  };

  // 去状态 class 与构建哈希后缀；哈希被裁掉的 class 改用 [class*="base"] 描述。
  const stableClassesOf = (node) => {
    const out = [];
    for (const cls of classListOf(node)) {
      if (STATE_CLASS_RE.test(cls)) continue;
      const base = cls.replace(HASH_SUFFIX_RE, "");
      if (!base || out.some((entry) => entry.value === base)) continue;
      out.push({ value: base, original: cls, trimmed: base !== cls });
    }
    return out.sort((a, b) => a.value.localeCompare(b.value));
  };

  // 影刀 extractAttributes：style / 纯数字 / 超长 / 双引号混用 / 内部 uid 全剔。
  const extractAttributes = (el) => {
    const out = [];
    const names = typeof el.getAttributeNames === "function"
      ? el.getAttributeNames()
      : Object.keys(el.attributes || {});
    for (const name of names) {
      if (name === "style" || name === "uia-uid" || !ATTR_NAME_RE.test(name)) continue;
      const value = el.getAttribute(name);
      if (!value || (value.includes('"') && value.includes("'"))) continue;
      if (/^\s*\d+\s*$/.test(value) || value.length >= 50) continue;
      out.push({ name, value });
    }
    return out;
  };

  // 影刀 getTagNameOrder：测试属性在前、id 中后、位置兜底；按标签/角色调整。
  const DEFAULT_ATTR_ORDER = [
    "type", "data-cy", "data-testid", "data-test-id", "data-e2e", "data-test",
    "aria-label", "name", "id", "title", "innerText", "class", "index-of-type",
  ];
  const TEXT_ATTR_ORDER = [
    "data-cy", "data-testid", "data-test-id", "data-e2e", "data-test", "aria-label",
    "innerText", "title", "type", "name", "id", "class", "index-of-type",
  ];
  const TAG_ATTR_ORDER = {
    iframe: ["src", ...DEFAULT_ATTR_ORDER],
    frame: ["src", ...DEFAULT_ATTR_ORDER],
    svg: ["viewBox", ...TEXT_ATTR_ORDER],
    a: ["href", ...TEXT_ATTR_ORDER],
    label: TEXT_ATTR_ORDER,
    button: TEXT_ATTR_ORDER,
    th: TEXT_ATTR_ORDER,
    li: TEXT_ATTR_ORDER,
  };
  const attributeOrderFor = (el) => {
    const tag = el.tagName.toLowerCase();
    if (TAG_ATTR_ORDER[tag]) return TAG_ATTR_ORDER[tag];
    if (el.getAttribute && el.getAttribute("role")) return ["role", ...TEXT_ATTR_ORDER];
    return DEFAULT_ATTR_ORDER;
  };

  // 影刀 liftFromNamespace：SVG 等非 HTML 命名空间上溯到 HTML 祖先再产 XPath。
  const liftFromNamespace = (el) => {
    let node = el;
    while (node && node.namespaceURI && node.namespaceURI !== HTML_NS) node = node.parentElement;
    return node || el;
  };

  const xpathLiteral = (value) => "'" + String(value).replace(/'/g, `', "'", '`) + "'";

  // XPath 1.0（Chrome document.evaluate）没有 ends-with：用 substring 等价式。
  const textXpathFor = (el) => {
    const raw = String(el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    if (!raw || raw.length >= 50) return null;
    const leading = raw.match(/^([\d., ()]+)[\s\S]+/);
    const trailing = raw.match(/[\s\S]+?([\d., ()]+)$/);
    let value = raw;
    let op = "contains";
    if (leading) {
      value = value.slice(leading[1].length);
      op = "ends";
    }
    if (trailing) {
      value = value.slice(0, Math.max(0, value.length - trailing[1].length));
      if (!leading) op = "starts";
    }
    value = value.trim();
    if (!value || /\d/.test(value)) return null;  // 动态数字没剥干净 ⇒ 不拿它当条件
    const target = "normalize-space(.)";
    let predicate;
    if (op === "starts") predicate = `starts-with(${target}, ${xpathLiteral(value)})`;
    else if (op === "ends") {
      predicate = `substring(${target}, string-length(${target}) - ${value.length - 1}) = ${xpathLiteral(value)}`;
    } else predicate = `contains(${target}, ${xpathLiteral(value)})`;
    // 前缀是协议的一部分：运行期 domOp 按它分派到 document.evaluate，不碰 page.call 签名。
    return "xpath://" + liftFromNamespace(el).tagName.toLowerCase() + "[" + predicate + "]";
  };

  // href/src 去 query / fragment，用 CSS 子串匹配（影刀 handleLinkAttr）。
  const linkFragmentFor = (el, name) => {
    const raw = el.getAttribute(name);
    if (!raw) return null;
    const path = raw.split(/[?#]/)[0];
    return path ? el.tagName.toLowerCase() + `[${name}*="${attrValue(path)}"]` : null;
  };

  const countFor = (kind, selector, root = document) => {
    try {
      if (kind === "xpath") {
        if (!root.evaluate) return 0;
        const expression = selector.replace(/^xpath:/, "");
        return root.evaluate(expression, root, null, 5 /* ORDERED_NODE_SNAPSHOT_TYPE */, null).snapshotLength;
      }
      if (kind === "shadow") {
        const parts = selector.replace(/^shadow:/, "").split("::shadow")
          .map((part) => part.trim()).filter(Boolean);
        let roots = [root];
        for (let i = 0; i < parts.length; i += 1) {
          const found = [];
          for (const scope of roots) {
            for (const node of scope.querySelectorAll(parts[i])) {
              // 最后一段取目标本身；前面各段只负责穿透到下一层 shadowRoot。
              if (i === parts.length - 1 || node.shadowRoot) {
                found.push(i === parts.length - 1 ? node : node.shadowRoot);
              }
            }
          }
          roots = found;
          if (!roots.length) return 0;
        }
        return roots.length;
      }
      return root.querySelectorAll(selector).length;
    } catch {
      return 0;
    }
  };

  // 影刀 handleDefaultAttr：完整「名=值」过度收窄（连本元素都命中不了）时，
  // 降级为「属性名存在」再试；两者都落空就不产候选，不往自愈列表里塞噪声。
  const defaultAttrVariantsFor = (el, name, attr) => {
    const value = attr ? attr.value : (el.getAttribute && el.getAttribute(name));
    if (!value || (name === "id" && !isStableValue(value))) return [];
    const tag = el.tagName.toLowerCase();
    const full = tag + "[" + name + '="' + attrValue(value) + '"]';
    const out = [full];
    const presence = tag + "[" + name + "]";
    if (!countFor("css", full) && countFor("css", presence)) out.push(presence);
    return out;
  };

  // 影刀 getRoleIndex：同类兄弟序号，作为合成属性候选。不替代主 css 的
  // nth-of-type 兜底——主 css 是层级勾选模型，这里是运行期自愈的备选写法。
  const indexOfTypeFor = (el) => {
    const parent = el.parentElement;
    if (!parent || !parent.children) return null;
    const same = Array.from(parent.children).filter((c) => c.tagName === el.tagName);
    const index = same.indexOf(el) + 1;
    return index ? el.tagName.toLowerCase() + ":nth-of-type(" + index + ")" : null;
  };

  const uniqueFor = (selector, target, root = document) => {
    const found = [];
    try {
      if (selector.startsWith("xpath:")) {
        if (!root.evaluate) return false;
        const snapshot = root.evaluate(selector.replace(/^xpath:/, ""), root, null, 5, null);
        for (let i = 0; i < snapshot.snapshotLength; i += 1) found.push(snapshot.snapshotItem(i));
      } else found.push(...root.querySelectorAll(selector));
    } catch {
      return false;
    }
    return found.length === 1 && found[0] === target;
  };

  // class 特化：剔状态类/哈希后逐个累加，命中唯一即停（影刀 handleClassAttr）。
  const classFragmentsFor = (node) => {
    const tag = node.tagName.toLowerCase();
    const classes = stableClassesOf(node);
    const out = [];
    let exact = tag;
    let substring = "";
    for (const cls of classes) {
      exact += "." + CSS.escape(cls.value);
      out.push(exact);
      substring += `[class*="${attrValue(cls.value)}"]`;
      if (cls.trimmed) out.push(tag + substring);
    }
    return out;
  };

  const nodeChainOf = (el) => {
    const out = [];
    let node = el;
    while (node && node.nodeType === 1 && out.length < MAX_TREE_DEPTH) {
      out.unshift(node);
      node = node.parentElement;
    }
    return out;
  };

  // 从叶到根逐节点按优先级加属性，命中唯一即返回（影刀 makePathUnique）。
  const greedyCssFor = (el) => {
    const chain = nodeChainOf(el);
    let tail = "";
    for (let i = chain.length - 1; i >= 0; i -= 1) {
      const node = chain[i];
      const tag = node.tagName.toLowerCase();
      const base = tag;
      const head = tail ? base + " > " + tail : base;
      if (uniqueFor(head, el)) return head;
      const id = stableIdOf(node);
      const attrs = extractAttributes(node);
      const names = attributeOrderFor(node);
      for (const name of names) {
        const variants = [];
        if (name === "class") variants.push(...classFragmentsFor(node));
        else if (name === "id" && id) variants.push("#" + CSS.escape(id));
        else if (name === "innerText") {
          const xp = textXpathFor(node);
          if (xp && uniqueFor(xp, node)) return xp;
          continue;
        } else if (name === "href" || name === "src") {
          const link = linkFragmentFor(node, name);
          if (link) variants.push(link);
        } else {
          const attr = attrs.find((entry) => entry.name === name);
          for (const variant of defaultAttrVariantsFor(node, name, attr)) variants.push(variant);
        }
        for (const variant of variants) {
          const candidate = tail ? variant + " > " + tail : variant;
          if (uniqueFor(candidate, el)) return candidate;
        }
      }
      tail = head;
    }
    return cssSelectorFor(el);
  };

  // 影刀 handleSkipNodes：唯一后逐个删中间层，命中集不变就永久删（语义标签保留）。
  const ARIA_TAG_NAMES = new Set([
    "table", "form", "button", "tr", "td", "ul", "li", "img", "a", "textarea", "select", "input",
  ]);
  const pruneCssFor = (css, target) => {
    if (css.startsWith("xpath:") || css.startsWith("shadow:") || !css.includes(" > ")) return css;
    const parts = css.split(" > ");
    let removed = false;
    for (let i = 1; i < parts.length - 1; i += 1) {
      const tag = parts[i].match(/^([a-z0-9]+)/i)?.[1]?.toLowerCase() || "";
      if (ARIA_TAG_NAMES.has(tag)) continue;
      // 删中间层后剩下两级不再是父子：必须用后代连接符（与 selector_tree.compose 同口径）。
      const trial = parts.filter((_, index) => index !== i).join(" ");
      if (uniqueFor(trial, target)) {
        parts.splice(i, 1);
        removed = true;
        i -= 1;
      }
    }
    return removed ? parts.join(" ") : css;
  };

  const shadowHostsOf = (el) => {
    const out = [];
    let node = el;
    while (node) {
      const root = typeof node.getRootNode === "function" ? node.getRootNode() : document;
      if (root && root !== document && root.host) {
        out.push(root.host);
        node = root.host;
      } else node = node.parentElement;
    }
    return out;
  };

  // shadow DOM 分段：每段单独生成 CSS，用 ::shadow 串接（运行期按段逐 shadowRoot 查）。
  const shadowCssFor = (el) => {
    const hosts = shadowHostsOf(el);
    if (!hosts.length) return null;
    const fragmentOf = (node) => {
      const id = stableIdOf(node);
      if (id) return "#" + CSS.escape(id);
      const classes = stableClassesOf(node);
      return node.tagName.toLowerCase() + (classes.length ? "." + CSS.escape(classes[0].value) : "");
    };
    // 从内到外收集「shadowRoot 内部」的段；每段不含 host 本身。
    const inner = [];
    let node = el;
    for (const host of hosts) {
      const local = [];
      let current = node;
      while (current && current !== host && current.nodeType === 1) {
        local.unshift(fragmentOf(current));
        current = current.parentElement;
      }
      inner.unshift(local.join(" > ") || fragmentOf(host));
      node = host;
    }
    // 最外层 host 本身在主文档里，作为第一段；后续每段在上一层 shadowRoot 里查。
    const outer = hosts[hosts.length - 1];
    return "shadow:" + [fragmentOf(outer), ...inner].join(" ::shadow ");
  };

  const optimizedCandidatesFor = (el, primary) => {
    const out = [];
    // 稳定 id 已唯一时主 css 就是最优解：优化器再产一条裸 tag 只会污染候选列表。
    if (primary && primary.startsWith("#") && countFor("css", primary) === 1) return out;
    const push = (kind, selector) => {
      if (!selector || selector === primary) return;
      const matched = countFor(kind, selector);
      if (!matched) return;
      out.push({ kind, selector, matchedCount: matched });
    };
    const greedy = greedyCssFor(el);
    push(greedy.startsWith("xpath:") ? "xpath" : "css-optimized", greedy);
    if (!greedy.startsWith("xpath:")) push("pruned", pruneCssFor(greedy, el));
    const text = textXpathFor(el);
    if (text) push("xpath", text);
    for (const name of attributeOrderFor(el)) {
      if (name === "href" || name === "src") {
        push("link", linkFragmentFor(el, name));
      } else if (name === "innerText" || name === "class" || name === "index-of-type") {
        continue;
      } else {
        for (const variant of defaultAttrVariantsFor(el, name)) push("attribute", variant);
      }
    }
    const index = indexOfTypeFor(el);
    if (index) push("attribute", index);
    const shadow = shadowCssFor(el);
    if (shadow) push("shadow", shadow);
    return out;
  };

  // 备选定位：只收 CSS 可解析的形式（将来 resolver 复用现有 querySelectorAll 机制即可），
  // 全部实测 matchedCount；捕获时就不命中或与主 css 重复的不留。
  const CANDIDATE_ATTRS = [
    "data-testid", "data-test", "data-qa", "name", "aria-label", "placeholder", "title",
  ];

  // 祖先链收缩候选（M51-B「候选生成对齐主 css class 路径」）：主 css 是「根 → 目标」的
  // 完整路径（见 pathFor），**逐级丢掉最外层**即得更短的写法；每条都实测命中数，合起来
  // 正好回答「收到哪一级才唯一命中」——这正是维护者要 debug 的那件事。
  //
  // 为什么必须有这个来源：id/属性两类候选只能覆盖「有 id 或 data-*/name/aria-label 等属性」
  // 的元素。**只有 class 的普通元素**（小红书 `a.cover`、百度热搜 `a.title-content` 这类
  // 列表项链接）一条属性都不占 ⇒ 属性候选恒空。M52 S3 实测：元素库里已保存的元素
  // **无一含候选**，确认框因此一直是空的。祖先链收缩是这类元素**唯一**可用的定位素材。
  //
  // 每级再给「去掉 :nth-of-type」的变体（位置序号最脆，@medv/finder 给 10、Robula+ /
  // Playwright 都把它排最后）。级数上限 MAX_PATH_SUFFIX、每级至多 2 条，避免候选爆炸
  // ——候选越多，运行期自愈逐个 try 的开销越大。
  const MAX_PATH_SUFFIX = 3;
  const pathSuffixSelectorsFor = (el) => {
    // 收缩候选从主 css 的短链截（与升级前逐字一致）；全链只服务编辑器节点树。
    const fragments = cssChainFor(el).map((entry) => entry.fragment);
    if (fragments.length < 2) return [];  // 单级链（元素自带 id）没有「更短写法」可给
    const top = Math.min(fragments.length - 1, MAX_PATH_SUFFIX);
    const out = [];
    for (let take = top; take >= 1; take -= 1) {  // 长的（更具体）在前
      const tail = fragments.slice(-take).join(" > ");
      out.push(tail);
      const stripped = fragments
        .slice(-take)
        .map((fragment) => fragment.replace(/:nth-of-type\(\d+\)/g, ""))
        .join(" > ");
      if (stripped !== tail) out.push(stripped);
    }
    return out;
  };

  const candidatesFor = (el, primary) => {
    const out = [];
    const seenValues = new Set(primary ? [primary] : []);
    const push = (kind, selector) => {
      if (!selector || seenValues.has(selector)) return;
      // xpath / shadow 不能交给 querySelectorAll：按 kind 分派实测（M105）。
      const matched = countFor(kind, selector);
      if (!matched) return;
      seenValues.add(selector);
      out.push({ kind, selector, matchedCount: matched });
    };
    if (el.id) push("id", "#" + CSS.escape(el.id));
    for (const name of CANDIDATE_ATTRS) push("attribute", attrSelectorFor(el, name));
    for (const selector of pathSuffixSelectorsFor(el)) push("path", selector);
    // 影刀式优化器产出的候选（贪心唯一化 / class 累加 / 文本 / 链接 / 剪枝 / shadow）。
    for (const candidate of optimizedCandidatesFor(el, primary)) {
      push(candidate.kind, candidate.selector);
    }
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
  // 首个命中元素的**视口矩形**（预览页签要按它画红框；M47.11）。
  // 为什么是视口坐标而不是文档坐标：截图来自 `captureVisibleTab`，它拍的就是**当前
  // 视口**——GUI 把图按图片像素展示，红框必须与「截图那一瞬的视口」对齐，故这里
  // 一并回传 viewport 尺寸与滚动偏移，GUI 侧把文档坐标换算成图内像素（见
  // `preview_box_in_image`）。取不到（count=0）返回 null，GUI 就不画框。
  const firstHitRect = (doc, css) => {
    let el = null;
    try {
      el = doc.querySelector(css);
    } catch {
      return null;
    }
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height };
  };
  const viewportInfo = () => ({
    width: window.innerWidth || 0,
    height: window.innerHeight || 0,
    // devicePixelRatio：截图是**物理像素**，视口尺寸是 CSS 像素，两者差 dpr 倍
    // （Retina/125% 缩放)。画框时按 dpr 缩放才对齐。
    dpr: window.devicePixelRatio || 1,
    scrollX: window.scrollX || window.pageXOffset || 0,
    scrollY: window.scrollY || window.pageYOffset || 0,
    // M47.12：统一改走**桌面坐标截屏**（ImageGrab 截窗口那块屏幕），图左上角 =
    // 窗口左上角。所以要把视口 CSS 像素换算成图内像素，还差「视口左上角相对窗口
    // 左上角的偏移」——**只取决于窗口内部布局，与窗口在屏幕上的位置无关**：
    //   border  = (outerWidth  - width) / 2左右边框，真机实测每侧 8px
    //   originY = (outerHeight - height - border)   视口底紧邻窗口下边框
    // **别用 screenX/screenY 推视口位置**（2026-10-09 真机 bug）：它们是**窗口**
    // 左上角的屏幕坐标，不是视口的——实测 screenY 与 GetWindowRect 的 top 完全
    // 相等，那个减法恒等于 0，于是少算了标签栏+地址栏整段（真机 151px），裁剪区
    // 偏上约 143px。下面这两个字段**保留但换算不读**，只作调试对照。
    screenX: window.screenX,
    screenY: window.screenY,
    // 窗口（含标签栏/地址栏/边框）的 CSS 尺寸：算视口在窗口内偏移的**必需量**。
    // 缺了它们 host 宁可不出裁剪区，也不给一个偏上百像素的（口径见 host 侧
    // browser_viewport_in_window 的 docstring）。
    outerWidth: window.outerWidth || 0,
    outerHeight: window.outerHeight || 0,
  });
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

  // [capture-descriptor-rect:start]
  // 元素矩形 → 元数据。**键名必须是 left/top**（真机 2026-10-09 踩过：原写成
  // {x, y}，而 host 的 screen_shot.browser_box_in_window 只读 left/top ⇒
  // 捕获时那张截图恒算不出红框，KeyError 被 except 吞掉、只表现为"有图没框"）。
  // 提取成独立函数是为了能按锚点切片、**真跑一次求值**（见
  // scripts/check_capture_descriptor_rect.mjs）——读源码只能证明"写了 left"，
  // 证明不了"键名与 host 换算函数对得上"，而这次错的就是后者。
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return {
      left: Math.round(r.left),
      top: Math.round(r.top),
      width: Math.round(r.width),
      height: Math.round(r.height),
    };
  };
  // [capture-descriptor-rect:end]

  const buildDescriptor = (el) => {
    // path = **完整**祖先链（M69：目标 → 页面根，每级 {tag, id, classes, nthOfType,
    // fragment, inCss}）。inCss 标记哪些层组成了主 css——编辑器据它初始化勾选，
    // 所以树上初始高亮的路径与真正下发执行的选择器必然是同一条。
    const chain = pathFor(el);
    const cssStart = el.id ? chain.length - 1 : cssStartFor(chain);
    const marked = chain.map((entry, index) => ({ ...entry, inCss: index >= cssStart }));
    const css = marked.filter((entry) => entry.inCss).map((entry) => entry.fragment)
      .join(" > ");
    return {
      kind: "browser",
      selector: { css, path: marked, candidates: candidatesFor(el, css) },
      verifyCount: document.querySelectorAll(css).length,
      // M47.12 真机反馈（预览要的是「捕获时的截图」）：把视口几何一并回传，
      // host 拿它把 metadata.rect 换算成图内红框。**不依赖扩展给窗口句柄**
      // （真实环境里 nativeWindowHandle 未必给得出，见 host侧capture_shot 注释）。
      viewport: viewportInfo(),
      metadata: {
        tag: el.tagName.toLowerCase(),
        id: el.id || null,
        classes: (typeof el.className === "string"
          ? el.className.trim().split(/\s+/).filter(Boolean) : []),
        text: (el.textContent || "").trim().slice(0, 80),
        // 键名与 rectOf 一致（left/top）：桌面腿 desktop_agent 回的也是 left/top、
        // 同文件 firstHitRect 也是 left/top，host 换算函数只读这一套。
        // **别与「输出 box 用 x/y」搞混**：那个 x/y 是 screen_shot 内部产物的口径，
        // GUI 画框也只读它，与这里的输入契约无关。
        rect: rectOf(el),
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

  // 视口截图（M102 S2b）：缩略图的权威底图。content script 没有截图 API，只能
  // 向 background 要一次；失败（扩展刚重载、页面不可见、配额）一律给 null——
  // 缩略图是增强，不能把一次成功的捕获拖成失败。回调里必须读 lastError，
  // 否则扩展重载后这里会抛「Unchecked runtime.lastError」。
  // ★ 超时兜底不可省：background 有「收到却不回」的形态（service worker 正在重启、
  //   消息通道半开），没有它这次捕获会**永远**停在 await 上，用户看到的是「点了没反应」。
  const SHOT_REQUEST_TIMEOUT_MS = 800;
  const requestVisibleShot = () => new Promise((resolve) => {
    let done = false;
    const finish = (value) => { if (!done) { done = true; resolve(value); } };
    const timer = setTimeout(() => finish(null), SHOT_REQUEST_TIMEOUT_MS);
    try {
      chrome.runtime.sendMessage({ type: "rpa-shot-request" }, (reply) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) { finish(null); return; }
        finish(reply && reply.ok && typeof reply.dataUrl === "string" ? reply.dataUrl : null);
      });
    } catch {
      clearTimeout(timer);
      finish(null);
    }
  });

  // 真正落地一次捕获：buildDescriptor → 视口截图 → 经 background 回传。
  const capture = async (e) => {
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
    const descriptor = buildDescriptor(el);
    // ★ 先**收掉覆盖层再截图**：视口图会被裁成元素缩略图，顶着红框/hint 条拍进去
    //   等于把「选中态」存进资产。rect 已在上一步取好，overlay 是 fixed 定位，
    //   移除它不改任何布局。
    hideOverlay();
    const viewportShot = await requestVisibleShot();
    try {
      chrome.runtime.sendMessage({
        type: "rpa-capture-result",
        descriptor,
        contentBuild: EXT_BUILD,
        viewportShot: viewportShot || undefined,
      });
    } catch (err) {
      // 通道失效等异常：同上下——红框不能赖着不走（它已经不会被任何人清掉），
      // 提示条保留原因，用户可再试；不要静默吞掉
      hideBox();
      clearCaptureCursor();
      setHint("捕获失败：" + ((err && err.message) || err));
      return;
    }
    lastCaptureAt = now;
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
  // 截图用的一次性高亮：画好 → 等 background 拍完（≈200ms）→ 自己收走。
  // 不能复用 persist=true 就走人：那样框会一直留到下一次预览，用户关掉编辑器才消失。
  const flashForShot = (els) => {
    flashElements(els, true);
    setTimeout(clearVerifyFlash, 700);
  };
  // M48：mode 决定高亮的「寿命」。flash 闪 1.6s 自清；preview 驻留（persist），
  // **count=0 也必须先清场**——选择器改到不再命中的瞬间，上一轮的黄框若还赖着，
  // 用户会把「旧框」读成「新选择器命中了」，这是预览最危险的静默误导；clear 只清场。
  //
  // `silent`（2026-10-08 双浏览器报障）：本页所在浏览器**不在前台**时 background 会带它下来
  // ——只回命中数、**绝不画黄框**（否则 Chrome 与 Edge 各开一页时两边都闪，而用户只该看到
  // 眼前那个）。silent 仍要回 count：host 认首个回传，全都不在前台时它就是计数兜底。
  //
  // M47.11：回传里带上首个命中的视口 rect 与视口信息，供 GUI 的「预览」页签在截图上
  // 画红框。**截图前必须先把黄框画好**（background 侧先 sendMessage 拿到应答、再截图），
  // 所以 `flash` 模式在需要截图时改成 `persist=true`——否则 1.6s 后框自清、截了个空。
  const runVerify = (css, mode, silent, keepFlash) => {
    mode = normalizeVerifyMode(mode);
    const reply = verifyReplyFor(document, css, mode);
    if (mode === "clear") {
      clearVerifyFlash();
      return reply;
    }
    if (mode === "preview") clearVerifyFlash();
    if (!silent && !reply.error && reply.count > 0) {
      const els = Array.from(document.querySelectorAll(css)).slice(0, VERIFY_FLASH_LIMIT);
      if (keepFlash === true && mode !== "preview") {
        // 截图模式：画好框等 background 拍照，拍完自清（不能像 preview 那样长驻）
        flashForShot(els);
      } else {
        flashElements(els, mode === "preview");
      }
    }
    return {
      ...reply,
      rect: reply.error ? null : firstHitRect(document, css),
      viewport: viewportInfo(),
    };
  };

  const onRuntimeMessage = (msg, _sender, sendResponse) => {
    if (msg && msg.type === "rpa-capture-verify") {
      // 同步应答：querySelectorAll 是同步的，无需 return true（那是异步应答的写法）
      sendResponse({
        contentBuild: EXT_BUILD,
        ...runVerify(
          String(msg.css || ""), msg.mode, msg.silent === true, msg.keepFlash === true
        ),
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
