// M105 运行期选择器分派门禁：background.js::domOp.query 必须按前缀分派 CSS / xpath / shadow。
// 只跑切片求值，不靠「源码里写了 if」这种代理指标——真给三种选择器，看取回哪几个节点。
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "extension", "background.js"), "utf8");
const start = source.indexOf("  const query = (sel) => {");
const end = source.indexOf("  const fire = (el, type, init) => {");
if (start < 0 || end < 0 || end <= start) {
  console.error("FAIL: 未能从 background.js 定位 domOp.query 切片");
  process.exit(1);
}
const slice = source.slice(start, end);

const unescape = (value) => String(value).replace(/\\(.)/g, "$1");
const matchesSimple = (node, selector) => {
  const byId = selector.match(/^#(.+)$/);
  if (byId) return node.id === unescape(byId[1]);
  const byClass = selector.match(/^([a-z0-9]+)((?:\.[^.:>\s]+)*)$/i);
  if (byClass) {
    if (node.tagName.toLowerCase() !== byClass[1].toLowerCase()) return false;
    const wanted = byClass[2] ? byClass[2].split(".").filter(Boolean).map(unescape) : [];
    const own = String(node.className || "").trim().split(/\s+/).filter(Boolean);
    return wanted.every((name) => own.includes(name));
  }
  return node.tagName.toLowerCase() === selector.toLowerCase();
};

const el = (tag, id = "", className = "", text = "") => ({
  tagName: tag, id, className, textContent: text, innerText: text,
});
const cssTarget = el("BUTTON", "ok", "primary", "保存");
const xpathTarget = el("DIV", "", "row", "订单 12345 详情");
const shadowTarget = el("SPAN", "", "target", "影子");
const shadowRootStub = {
  querySelectorAll: (selector) => [shadowTarget].filter((node) => matchesSimple(node, selector)),
};
const shadowHost = el("DIV", "host", "", "");
shadowHost.shadowRoot = shadowRootStub;
const page = [cssTarget, xpathTarget, shadowHost];

const documentStub = {
  querySelectorAll: (selector) => page.filter((node) => matchesSimple(node, selector)),
  evaluate: (expr) => {
    const match = String(expr).match(/^\/\/([a-z0-9]+)\[contains\(normalize-space\(\.\), '([^']+)'\)\]$/i);
    if (!match) return { snapshotLength: 0, snapshotItem: () => null };
    const out = page.filter((node) => node.tagName.toLowerCase() === match[1].toLowerCase()
      && String(node.innerText || "").includes(match[2]));
    return { snapshotLength: out.length, snapshotItem: (index) => out[index] };
  },
};

const query = new Function("document", `${slice}\nreturn query;`)(documentStub);

let failed = 0;
const check = (label, got, expected) => {
  const ok = JSON.stringify(got) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(`${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → got ${JSON.stringify(got)} want ${JSON.stringify(expected)}`}`);
};

check("普通 CSS 仍走 querySelectorAll", query("#ok"), [cssTarget]);
check("xpath: 前缀走 document.evaluate",
  query("xpath://div[contains(normalize-space(.), '详情')]"), [xpathTarget]);
check("shadow: 前缀最后一段取目标本身（不是 shadowRoot）",
  query("shadow:#host ::shadow span.target"), [shadowTarget]);
check("非法 XPath 不抛错、返回空集", query("xpath://div[["), []);

if (failed) {
  console.log(`\n${failed} 项失败`);
  process.exit(1);
}
console.log("\n全部通过");
