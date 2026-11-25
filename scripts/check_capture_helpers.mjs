// 校验捕获扩展的纯函数区（role 归一化 / 可访问名 / 备选候选 / 容器文本）。
// 做法与 check_param_groups.mjs 一致：从 content.js 按首尾锚点切片，用桩 DOM 求值后断言。
// 用法：node scripts/check_capture_helpers.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "extension", "content.js"), "utf8");

// 0) 整文件语法校验（new Function 只编译不执行；body 传 undefined 亦不会跑）
try {
  new Function(source);
} catch (err) {
  console.error(`FAIL: content.js 语法错误 → ${err.message}`);
  process.exit(1);
}

const start = source.indexOf("const ROLE_NAMES");
const end = source.indexOf("const ensureHint = (");
if (start < 0 || end < 0 || end <= start) {
  console.error("FAIL: 未能从 content.js 定位纯函数区（锚点 const ROLE_NAMES / const ensureHint =）");
  process.exit(1);
}
const slice = source.slice(start, end);

// ---- 桩 DOM -----------------------------------------------------------------
const CSS = {
  escape: (value) => String(value).replace(/[^A-Za-z0-9_-]/g, (ch) => `\\${ch}`),
};
const unescape = (value) => String(value).replace(/\\(.)/g, "$1");

const registry = new Map();
let page = [];

const unquoteXPath = (literal) => String(literal).replace(/^'|'$/g, "").replace(/', "'", '/g, "'");

// M105：桩 XPath 只覆盖我们生成的三种谓词（contains / starts-with / substring 等价 ends-with）。
const document = {
  getElementById: (id) => registry.get(id) || null,
  querySelectorAll: (selector) => page.filter((node) => matches(node, selector)),
  evaluate: (expr) => {
    const parsed = String(expr).match(/^\/\/([a-z0-9]+)\[([\s\S]+)\]$/i);
    if (!parsed) throw new Error(`桩 XPath 不支持：${expr}`);
    const predicate = parsed[2];
    const textOf = (node) => String(node.innerText ?? node.textContent ?? "")
      .replace(/\s+/g, " ").trim();
    let test = null;
    let hit = predicate.match(/^contains\(normalize-space\(\.\), ([\s\S]+)\)$/);
    if (hit) test = (node) => textOf(node).includes(unquoteXPath(hit[1]));
    hit = hit || predicate.match(/^starts-with\(normalize-space\(\.\), ([\s\S]+)\)$/);
    if (test === null && hit) test = (node) => textOf(node).startsWith(unquoteXPath(hit[1]));
    hit = hit || predicate.match(
      /^substring\(normalize-space\(\.\), string-length\(normalize-space\(\.\)\) - (\d+)\) = ([\s\S]+)$/,
    );
    if (test === null && hit) {
      const value = unquoteXPath(hit[2]);
      // 谓词里的数是「值长-1」，XPath substring 从 1 起算 ⇒ JS 取末 (数+1) 个字符。
      test = (node) => textOf(node).slice(-(Number(hit[1]) + 1)) === value;
    }
    if (!test) throw new Error(`桩 XPath 谓词不支持：${predicate}`);
    const out = page.filter((node) => node.tagName.toLowerCase() === parsed[1].toLowerCase() && test(node));
    return { snapshotLength: out.length, snapshotItem: (index) => out[index] };
  },
};

// 单段：`#id` / `tag[attr="v"]` / `tag.class.class` / `tag…:nth-of-type(n)`。
const matchesSimple = (node, segment) => {
  const byId = segment.match(/^#(.+)$/);
  if (byId) return node.id === unescape(byId[1]);
  const byPresence = segment.match(/^([a-z0-9]+)\[([a-z-]+)\]$/);
  if (byPresence) {
    if (node.tagName.toLowerCase() !== byPresence[1]) return false;
    return byPresence[2] in (node.liveAttrs || node.attributes);
  }
  const byAttr = segment.match(/^([a-z0-9]+)\[([a-z-]+)([*^$]?)="([\s\S]*)"\]$/);
  if (byAttr) {
    if (node.tagName.toLowerCase() !== byAttr[1]) return false;
    // M106 S1：liveAttrs 模拟「运行期值已漂移」——捕获时记下的值仍可作候选，
    // 但当前页面上的完整名值命不中，正是 handleDefaultAttr 要降级的场景。
    const source = node.liveAttrs || node.attributes;
    const actual = byAttr[2] in source ? source[byAttr[2]] : null;
    if (actual == null) return false;
    const want = unescape(byAttr[4]).replace(/\\\\/g, "\\");
    if (byAttr[3] === "*") return String(actual).includes(want);
    if (byAttr[3] === "^") return String(actual).startsWith(want);
    if (byAttr[3] === "$") return String(actual).endsWith(want);
    return String(actual) === want;
  }
  const byClass = segment.match(/^([a-z0-9]+)((?:\.[^.:>\s]+)*)(?::nth-of-type\((\d+)\))?$/);
  if (byClass) {
    if (node.tagName.toLowerCase() !== byClass[1]) return false;
    const wanted = byClass[2] ? byClass[2].split(".").filter(Boolean).map(unescape) : [];
    const own = String(node.className || "").trim().split(/\s+/).filter(Boolean);
    if (!wanted.every((name) => own.includes(name))) return false;
    if (byClass[3]) {
      const parent = node.parentElement;
      if (!parent) return false;
      const same = (parent.children || []).filter((c) => c.tagName === node.tagName);
      if (same.indexOf(node) + 1 !== Number(byClass[3])) return false;
    }
    return true;
  }
  return false;
};

// 组合选择器：`A > B` 与 `A B`（pruneCssFor 删中间层后会用后代连接符）。
// 桩 DOM 统一按「逐级上溯」处理两种连接符——只求覆盖被测形态，不模拟浏览器优先级差异。
// （注：朴素 split 对属性值里含 " > " 的极端选择器不成立。）
const matches = (node, selector) => {
  const tokens = selector.trim().split(/\s+/).filter(Boolean);
  const steps = [];
  let combinator = " ";
  for (const token of tokens) {
    if (token === ">") { combinator = ">"; continue; }
    steps.push({ selector: token, combinator });
    combinator = " ";
  }
  if (!steps.length) return false;
  const walk = (current, index) => {
    if (!current || !matchesSimple(current, steps[index].selector)) return false;
    if (index === 0) return true;
    if (steps[index].combinator === ">") return walk(current.parentElement, index - 1);
    let ancestor = current.parentElement;
    while (ancestor) {
      if (walk(ancestor, index - 1)) return true;
      ancestor = ancestor.parentElement;
    }
    return false;
  };
  return walk(node, steps.length - 1);
};

const textNode = (text) => ({ nodeType: 3, textContent: text });

const makeEl = (spec = {}) => {
  const node = {
    nodeType: 1,
    tagName: spec.tag || "DIV",
    type: spec.type,
    // 真 DOM 里 input.value 由 value 属性反射而来，此处照实模拟
    value: spec.value || "",
    id: spec.id || "",
    isContentEditable: spec.isContentEditable || false,
    className: (spec.classes || []).join(" "),
    labels: spec.labels || [],
    childNodes: spec.children || [],
    textContent: spec.text || "",
    innerText: spec.innerText || spec.text || "",
    attributes: spec.attrs || {},
    liveAttrs: spec.liveAttrs || null,
    shadowRoot: spec.shadowRoot || null,
    parentElement: null,
    children: [],
    getAttribute(name) {
      return name in this.attributes ? this.attributes[name] : null;
    },
    hasAttribute(name) {
      return name in (this.liveAttrs || this.attributes);
    },
    closest() {
      return spec.scope || null;
    },
    getBoundingClientRect: () => ({ x: 0, y: 0, left: 0, top: 0, width: 10, height: 10 }),
  };
  if (node.id) registry.set(node.id, node);
  return node;
};

const parentOf = (parent, children) => {
  parent.children = children;
  for (const child of children) child.parentElement = parent;
  return parent;
};

const {
  roleOf, accessibleName, cssSelectorFor, pathFor, candidatesFor, labelTextOf, containerTextOf,
  isCaptureModifier, isSecondaryClick, cursorKeyOf, cursorStyleText,
  isStableValue, stableIdOf, stableClassesOf, extractAttributes, attributeOrderFor,
  textXpathFor, linkFragmentFor, greedyCssFor, pruneCssFor, countFor, shadowCssFor,
  defaultAttrVariantsFor, indexOfTypeFor, optimizedCandidatesFor,
} = new Function("document", "CSS", `${slice}
return { roleOf, accessibleName, cssSelectorFor, pathFor, candidatesFor, labelTextOf,
  containerTextOf, isCaptureModifier, isSecondaryClick, cursorKeyOf, cursorStyleText,
  isStableValue, stableIdOf, stableClassesOf, extractAttributes, attributeOrderFor,
  textXpathFor, linkFragmentFor, greedyCssFor, pruneCssFor, countFor, shadowCssFor,
  defaultAttrVariantsFor, indexOfTypeFor, optimizedCandidatesFor };`)(
  document, CSS,
);

// ---- 断言 -------------------------------------------------------------------
let failed = 0;
const check = (label, got, expected) => {
  const ok = JSON.stringify(got) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(
    `${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → got ${JSON.stringify(got)} want ${JSON.stringify(expected)}`}`,
  );
};

// role 归一化
check("button → button", roleOf(makeEl({ tag: "BUTTON" })), "button");
check("a → link", roleOf(makeEl({ tag: "A" })), "link");
check("input[type=search] → searchbox", roleOf(makeEl({ tag: "INPUT", type: "search" })), "searchbox");
check("input[type=email] → textbox", roleOf(makeEl({ tag: "INPUT", type: "email" })), "textbox");
check("显式 role 白名单优先", roleOf(makeEl({ tag: "DIV", attrs: { role: "tab" } })), "tab");
check("非白名单 role 不采信，回落标签推导",
  roleOf(makeEl({ tag: "DIV", attrs: { role: "presentation" } })), null);
check("div → null（不是可交互角色）", roleOf(makeEl({ tag: "DIV" })), null);

// 可访问名优先级链
const byLabelId = makeEl({ tag: "SPAN", children: [textNode("用户名")] });
let target = makeEl({ tag: "INPUT", type: "text", attrs: { "aria-labelledby": "lbl", "aria-label": "忽略我" } });
registry.set("lbl", byLabelId);
check("aria-labelledby 压过 aria-label", accessibleName(target), "用户名");

check("aria-label 压过 <label>",
  accessibleName(makeEl({ tag: "INPUT", attrs: { "aria-label": "搜索" }, labels: [makeEl({ tag: "LABEL", children: [textNode("忽略")] })] })),
  "搜索");

check("<label> 压过 value",
  accessibleName(makeEl({
    tag: "INPUT", type: "submit", value: "发送",
    labels: [makeEl({ tag: "LABEL", children: [textNode("提交")] })],
  })),
  "提交");

check("submit 的 value 作为名字（无 label 时）",
  accessibleName(makeEl({ tag: "INPUT", type: "submit", value: "发送" })),
  "发送");

check("输入框不吃自身文本，回落 placeholder",
  accessibleName(makeEl({ tag: "TEXTAREA", attrs: { placeholder: "说点什么" }, children: [textNode("")] })),
  "说点什么");

check("非输入框取文本内容",
  accessibleName(makeEl({ tag: "BUTTON", children: [textNode("登 录")] })),
  "登 录");

check("aria-hidden 子树不进名字",
  accessibleName(makeEl({ tag: "BUTTON", children: [makeEl({ tag: "SPAN", attrs: { "aria-hidden": "true" }, text: "隐藏" })] })),
  "");

// 主 css 行为不变（升级前语义）
check("有 id → #id", cssSelectorFor(makeEl({ tag: "INPUT", id: "kw" })), "#kw");
const leaf = makeEl({ tag: "DIV", classes: ["d-flex"] });
const mid = makeEl({ tag: "DIV", classes: ["position-relative"] });
const root = makeEl({ tag: "DIV", id: "nav" });
parentOf(root, [mid]);
parentOf(mid, [leaf, makeEl({ tag: "DIV", classes: ["sibling"] })]);
check("无 id → 逐级路径；特征 class 已唯一 ⇒ **不**补 :nth-of-type",
  cssSelectorFor(leaf), "#nav > div.position-relative > div.d-flex");

// 祖先链（节点树的原料）：pathFor 是**唯一**的片段生成处，cssSelectorFor 由它 join 而来，
// 所以「树上显示的路径」与「真正下发执行的选择器」不可能漂移——这条断言就是那句保证。
check("有 id → 祖先链只有一级（带 id 即终止上溯）",
  pathFor(makeEl({ tag: "INPUT", id: "kw" })).map((e) => e.fragment), ["#kw"]);
check("pathFor 与 cssSelectorFor 同源（fragment join 即主 css）",
  pathFor(leaf).map((e) => e.fragment).join(" > "), cssSelectorFor(leaf));
check("祖先链每级可展示（tag / id / classes / nthOfType）",
  pathFor(leaf).map((e) => [e.tag, e.id, e.classes.join("."), e.nthOfType]),
  [["div", "nav", "", null], ["div", null, "position-relative", null],
    ["div", null, "d-flex", null]]);
check("祖先链根 → 目标（顺序不能反）",
  pathFor(leaf).map((e) => e.tag), ["div", "div", "div"]);

// ★ :nth-of-type 只在**必要**时才补（用户实测：div.search-area:nth-of-type(1) 这类冗余序号）。
// 判据是「本层片段（tag + 首类）在同标签兄弟间是否仍不唯一」——位置序号是稳定性最差的
// 一环（@medv/finder 给 10、Robula+ / Playwright 都排最后），页面一重排 / 插入兄弟即失效。

// 反例：父节点有多个同标签兄弟，但只有目标带特征 class ⇒ 片段已唯一，绝不补序号。
const searchArea = makeEl({ tag: "DIV", classes: ["search-area"] });
const plainA = makeEl({ tag: "DIV" });
const plainB = makeEl({ tag: "DIV" });
parentOf(makeEl({ tag: "BODY" }), [plainA, searchArea, plainB]);
check("特征 class 唯一 ⇒ 多个同标签兄弟也不补 :nth-of-type",
  cssSelectorFor(searchArea), "body > div.search-area");

// 正例：同构列表（每个兄弟 class 完全一样）⇒ 只能靠位置区分，序号是**真必要**的。
const hot1 = makeEl({ tag: "LI", classes: ["hotsearch-item"] });
const hot2 = makeEl({ tag: "LI", classes: ["hotsearch-item"] });
const hot3 = makeEl({ tag: "LI", classes: ["hotsearch-item"] });
parentOf(makeEl({ tag: "UL", id: "hotsearch-content-wrapper" }), [hot1, hot2, hot3]);
check("同构列表取第 2 项 ⇒ 补 :nth-of-type(2)（class 分不开，位置是唯一办法）",
  cssSelectorFor(hot2), "#hotsearch-content-wrapper > li.hotsearch-item:nth-of-type(2)");
check("同构列表取第 1 项 ⇒ :nth-of-type(1)",
  cssSelectorFor(hot1), "#hotsearch-content-wrapper > li.hotsearch-item:nth-of-type(1)");
// M105：不再固定「首类」——按最能消歧的 class 累加；同时「含数字的 id」按影刀
// isStableValue 判为不稳定，不当主锚点（等价临时摘掉 id 再构建）。
const pinned = makeEl({ tag: "LI", classes: ["hotsearch-item", "pinned"] });
parentOf(makeEl({ tag: "UL", id: "list2" }), [hot1, pinned, hot3]);
check("次位特征 class 参与判别 ⇒ 不补序号；不稳定 id(list2) 不进主 css",
  cssSelectorFor(pinned), "ul > li.hotsearch-item.pinned");

// 捕获光标（按下 Ctrl/⌘ 未点击时的就绪反馈）：纯函数层只判「哪个键算修饰键」与样式文本，
// 真正的 DOM 开关在 check_capture_lifecycle.mjs 里用桩真求值。
check("cursorKeyOf 只认 Control / Meta（与捕获手势同口径）",
  [cursorKeyOf({ key: "Control" }), cursorKeyOf({ key: "Meta" }),
    cursorKeyOf({ key: "Shift" }), cursorKeyOf({}), cursorKeyOf(null)],
  ["ctrl", "meta", null, null, null]);
check("光标样式：蓝色箭头 + !important + * 兜底（压过站点自己的 cursor）",
  [/2F6BFF/.test(cursorStyleText()), /\* \{ cursor:/.test(cursorStyleText()),
    /!important/.test(cursorStyleText()), /svg/.test(cursorStyleText())],
  [true, true, true, true]);

// 备选候选
page = [];
const box = makeEl({
  tag: "INPUT", type: "search", id: "sb_form_q",
  attrs: { name: "q", placeholder: "搜索", "aria-label": "搜索框", "data-testid": "sb" },
});
page = [box];
// 顺序按稳定性排：data-* 测试钩子 > name > aria-label > placeholder
check("id 候选 + 属性候选，带实测命中数",
  candidatesFor(box, "#sb_form_q"),
  [
    { kind: "attribute", selector: 'input[data-testid="sb"]', matchedCount: 1 },
    { kind: "attribute", selector: 'input[name="q"]', matchedCount: 1 },
    { kind: "attribute", selector: 'input[aria-label="搜索框"]', matchedCount: 1 },
    { kind: "attribute", selector: 'input[placeholder="搜索"]', matchedCount: 1 },
  ]);

page = [makeEl({ tag: "INPUT", id: "keyword", attrs: {} })];
check("主 css 与 id 候选重复时不重复收",
  candidatesFor(page[0], "#keyword"), []);

page = [makeEl({ tag: "BUTTON", id: "", attrs: { "aria-label": '引用"引号' } })];
check("属性值含引号仍拼出可解析选择器",
  candidatesFor(page[0], "button")[0].selector, 'button[aria-label="引用\\"引号"]');

page = [makeEl({ tag: "BUTTON", attrs: { name: "dup" } }), makeEl({ tag: "BUTTON", attrs: { name: "dup" } })];
check("命中多个也保留，如实记录 matchedCount",
  candidatesFor(page[0], "button").find((c) => c.kind === "attribute").matchedCount, 2);

page = [makeEl({ tag: "BUTTON", attrs: { name: "" } })];
check("空属性值不产生候选", candidatesFor(page[0], "button"), []);

// ★ 祖先链收缩候选（M51-B）：主 css 是「根 → 目标」的完整路径，逐级丢最外层即得更短写法，
// 每条实测命中数。这是「只有 class、没有 id/data-* 属性」的普通元素的**唯一**候选来源
// （M52 S3 实测：全库已存元素的候选全为空——小红书 a.cover 这类列表项链接一条属性都不占）。
const cover1 = makeEl({ tag: "A", classes: ["cover"] });
const cover2 = makeEl({ tag: "A", classes: ["cover"] });
const holder1 = makeEl({ tag: "DIV" });
const holder2 = makeEl({ tag: "DIV" });
parentOf(holder1, [cover1]);
parentOf(holder2, [cover2]);
const section1 = makeEl({ tag: "SECTION", classes: ["note-item"] });
const section2 = makeEl({ tag: "SECTION", classes: ["note-item"] });
parentOf(section1, [holder1]);
parentOf(section2, [holder2]);
const feed = makeEl({ tag: "DIV", id: "exploreFeeds" });
parentOf(feed, [section1, section2]);
page = [cover1, cover2, holder1, holder2, section1, section2, feed];
check("祖先链收缩候选：逐级丢最外层 + 去 nth 变体，各自实测命中数（1 = 收到该级才唯一）",
  candidatesFor(cover1, cssSelectorFor(cover1)),
  [
    { kind: "path", selector: "section.note-item:nth-of-type(1) > div > a.cover", matchedCount: 1 },
    { kind: "path", selector: "section.note-item > div > a.cover", matchedCount: 2 },
    { kind: "path", selector: "div > a.cover", matchedCount: 2 },
    { kind: "path", selector: "a.cover", matchedCount: 2 },
  ]);
check("只有 class 的元素也拿得到候选（补属性候选恒空的位）",
  candidatesFor(cover1, cssSelectorFor(cover1)).filter((c) => c.kind === "path").length > 0, true);

page = [makeEl({ tag: "INPUT", id: "kw" })];
check("有 id 的元素不产祖先链候选（单级链没有更短写法）",
  candidatesFor(page[0], "#kw").filter((c) => c.kind === "path"), []);

// ---- M105：影刀式筛选器优化（S1 过滤 / S2 稳定化 / S3 特化） ------------------
check("isStableValue：纯字母与连字符稳定，含数字判生成值",
  [isStableValue("search-area"), isStableValue("btn-a1b2c3"), isStableValue("ember123")],
  [true, false, false]);
const messy = makeEl({
  tag: "BUTTON",
  classes: ["btn", "btn--a1b2c3", "hover", "is-open", "active"],
  attrs: {
    style: "color:red", count: "123", both: `'"`,
    "data-testid": "save", title: "x".repeat(60),
  },
});
check("extractAttributes 剔 style / 纯数字 / 双引号混用 / 超长值",
  extractAttributes(messy).map((entry) => entry.name), ["data-testid"]);
check("stableClassesOf 剔状态类并裁构建哈希后缀",
  stableClassesOf(messy).map((entry) => entry.value), ["btn"]);
check("attributeOrderFor：a 优先 href、role 元素优先 role、默认从 type 起",
  [attributeOrderFor(makeEl({ tag: "A" }))[0],
    attributeOrderFor(makeEl({ tag: "DIV", attrs: { role: "tab" } }))[0],
    attributeOrderFor(makeEl({ tag: "DIV" }))[0]],
  ["href", "role", "type"]);

const numbered = makeEl({ tag: "DIV", innerText: "12345 详情" });
const textXpath = textXpathFor(numbered);
check("textXpathFor 剥动态数字，XPath 1.0 用 substring 模拟 ends-with",
  textXpath,
  "xpath://div[substring(normalize-space(.), string-length(normalize-space(.)) - 1) = '详情']");
page = [numbered];
check("countFor 按 xpath 分派到 document.evaluate", countFor("xpath", textXpath), 1);
check("linkFragmentFor 去 query/fragment 后用子串匹配",
  linkFragmentFor(makeEl({ tag: "A", attrs: { href: "https://x.test/search?a=1#top" } }), "href"),
  'a[href*="https://x.test/search"]');

page = [
  makeEl({ tag: "INPUT", attrs: { "data-testid": "target" } }),
  makeEl({ tag: "INPUT", attrs: { "data-testid": "other" } }),
];
check("greedyCssFor 按测试属性优先级试到唯一",
  greedyCssFor(page[0]), 'input[data-testid="target"]');

const pruned = makeEl({ tag: "SPAN", classes: ["target"] });
const wrap = makeEl({ tag: "DIV", classes: ["wrap"] });
const hostRoot = makeEl({ tag: "DIV", id: "root" });
parentOf(hostRoot, [wrap]);
parentOf(wrap, [pruned]);
page = [hostRoot, wrap, pruned];
check("pruneCssFor 贪心删掉命中集不变的中间层（改成后代连接符）",
  pruneCssFor("#root > div.wrap > span.target", pruned), "#root span.target");

const shadowTarget = makeEl({ tag: "SPAN", classes: ["target"] });
const shadowRootStub = {
  host: null,
  querySelectorAll: (selector) => page.filter((node) => matches(node, selector)),
};
const shadowHost = makeEl({ tag: "DIV", id: "host", shadowRoot: shadowRootStub });
shadowRootStub.host = shadowHost;
shadowTarget.getRootNode = () => shadowRootStub;
shadowHost.getRootNode = () => document;
page = [shadowHost, shadowTarget];
check("shadowCssFor 用 ::shadow 分段（host 在主文档，目标在 shadowRoot）",
  shadowCssFor(shadowTarget), "shadow:#host ::shadow span.target");
check("countFor 按 shadow 分派逐层穿透，最后一段取目标本身",
  countFor("shadow", "shadow:#host ::shadow span.target"), 1);

// ---- M106：属性化候选补齐（S1 降级 / S2 index-of-type / S4 svg） ---------------
const drifted = makeEl({
  tag: "BUTTON",
  attrs: { title: "captured" },
  liveAttrs: { title: "current" },  // 运行期值已漂移 ⇒ 完整名值命中不了
});
page = [drifted];
check("handleDefaultAttr：完整名值过度收窄时降级为属性名存在候选",
  defaultAttrVariantsFor(drifted, "title"),
  ['button[title="captured"]', "button[title]"]);
check("值漂移后仍能从候选层拿到存在性写法",
  candidatesFor(drifted, "button").filter((c) => c.selector === "button[title]").length, 1);

const listParent = makeEl({ tag: "UL" });
const item1 = makeEl({ tag: "LI" });
const item2 = makeEl({ tag: "LI" });
parentOf(listParent, [item1, item2]);
page = [listParent, item1, item2];
check("index-of-type：同类兄弟序号作为合成属性候选",
  indexOfTypeFor(item2), "li:nth-of-type(2)");
check("index-of-type 以 attribute 候选身份进优化器且实测命中数",
  optimizedCandidatesFor(item2, "ul > li:nth-of-type(1)").some(
    (c) => c.kind === "attribute" && c.selector === "li:nth-of-type(2)"
      && c.matchedCount === 1), true);
check("svg 属性特化顺序并入 tagOrderMap",
  attributeOrderFor(makeEl({ tag: "svg" }))[0], "viewBox");

// 捕获手势（回归：macOS 的 Ctrl+Click 只派发 contextmenu，不派发 ctrlKey 的 click）
check("Ctrl+左键（Windows/Linux）算捕获手势",
  isCaptureModifier({ ctrlKey: true, metaKey: false }), true);
check("⌘+左键（macOS 等价手势）算捕获手势",
  isCaptureModifier({ ctrlKey: false, metaKey: true }), true);
check("无修饰键不算捕获手势",
  isCaptureModifier({ ctrlKey: false, metaKey: false }), false);
check("contextmenu 算次要点击（button 可能是 0，不看 button）",
  isSecondaryClick({ type: "contextmenu", button: 0 }), true);
check("contextmenu 带 button=2 也算",
  isSecondaryClick({ type: "contextmenu", button: 2 }), true);
check("mousedown button=2 算次要点击",
  isSecondaryClick({ type: "mousedown", button: 2 }), true);
check("mousedown button=0 不算次要点击",
  isSecondaryClick({ type: "mousedown", button: 0 }), false);
check("普通 click 不算次要点击",
  isSecondaryClick({ type: "click", button: 0 }), false);

// label / 容器文本
check("label 文本拼接 + 截断",
  labelTextOf(makeEl({ tag: "INPUT", labels: [makeEl({ tag: "LABEL", text: "手机号" })] })), "手机号");
check("容器文本压平空白并截断 200",
  containerTextOf(makeEl({ tag: "INPUT", scope: { innerText: "a".repeat(300) } })).length, 200);
check("无容器时回落 parentElement",
  containerTextOf(makeEl({ tag: "INPUT", text: "" })), "");

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
