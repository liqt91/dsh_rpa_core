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
const end = source.indexOf("const show = (");
if (start < 0 || end < 0 || end <= start) {
  console.error("FAIL: 未能从 content.js 定位纯函数区（锚点 const ROLE_NAMES / const show =）");
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

const document = {
  getElementById: (id) => registry.get(id) || null,
  querySelectorAll: (selector) => page.filter((node) => matches(node, selector)),
};

const matches = (node, selector) => {
  const byId = selector.match(/^#(.+)$/);
  if (byId) return node.id === unescape(byId[1]);
  const byAttr = selector.match(/^([a-z0-9]+)\[([a-z-]+)="([\s\S]*)"\]$/);
  if (byAttr) {
    if (node.tagName.toLowerCase() !== byAttr[1]) return false;
    return node.getAttribute(byAttr[2]) === unescape(byAttr[3]).replace(/\\\\/g, "\\");
  }
  return false;
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
    parentElement: null,
    children: [],
    getAttribute(name) {
      return name in this.attributes ? this.attributes[name] : null;
    },
    hasAttribute(name) {
      return name in this.attributes;
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
  roleOf, accessibleName, cssSelectorFor, candidatesFor, labelTextOf, containerTextOf,
} = new Function("document", "CSS", `${slice}
return { roleOf, accessibleName, cssSelectorFor, candidatesFor, labelTextOf, containerTextOf };`)(
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
check("无 id → 逐级路径带 :nth-of-type",
  cssSelectorFor(leaf), "#nav > div.position-relative > div.d-flex:nth-of-type(1)");

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

// label / 容器文本
check("label 文本拼接 + 截断",
  labelTextOf(makeEl({ tag: "INPUT", labels: [makeEl({ tag: "LABEL", text: "手机号" })] })), "手机号");
check("容器文本压平空白并截断 200",
  containerTextOf(makeEl({ tag: "INPUT", scope: { innerText: "a".repeat(300) } })).length, 200);
check("无容器时回落 parentElement",
  containerTextOf(makeEl({ tag: "INPUT", text: "" })), "");

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
