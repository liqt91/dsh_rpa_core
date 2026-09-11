// 校验编辑器「参数分组」判定逻辑（paramGroupPlan，与渲染共用同一份源码）。
// 用法：node scripts/check_param_groups.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "src", "rpa_core", "devserver", "static", "app.js"), "utf8");

const start = source.indexOf("function paramGroupPlan");
const end = source.indexOf("function renderGroupedFields");
if (start < 0 || end < 0 || end <= start) {
  console.error("FAIL: 未能从 app.js 定位 paramGroupPlan 代码段");
  process.exit(1);
}
const paramGroupPlan = new Function(`${source.slice(start, end)}\nreturn paramGroupPlan;`)();

// navigate.json 的分组声明（与 manifest 保持一致）
const properties = {
  action: {}, url: {}, waitUntil: {}, timeoutMs: {}, channel: {}, args: {},
  headless: {}, userAgent: {}, userDataDir: {}, transport: {},
};
const groups = [
  { label: "常规", fields: ["action", "url"] },
  { label: "浏览器", fields: ["transport", "channel"] },
  { label: "高级", fields: ["timeoutMs"], collapsed: true },
  { label: "启动选项（仅 playwright 通道）", fields: ["waitUntil", "args", "headless", "userAgent", "userDataDir"], collapsed: true },
];

let failed = 0;
const check = (label, got, expected) => {
  const ok = JSON.stringify(got) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(`${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → got ${JSON.stringify(got)} want ${JSON.stringify(expected)}`}`);
};

const planLabels = (withArgs) => paramGroupPlan({ "x-depends": DEPS }, properties, withArgs, groups)
  .map((p) => [p.label, p.fields, p.open]);
const DEPS = {
  waitUntil: { transport: "playwright" },
  args: { transport: "playwright" },
  headless: { transport: "playwright" },
  userAgent: { transport: "playwright" },
  userDataDir: { transport: "playwright" },
};

// 缺省（extension 通道）：playwright 专属字段全部进「其他通道参数」折叠区
check(
  "缺省通道： inactive=5 个专属字段，折叠",
  planLabels({ url: "https://x" }).filter((p) => p[0] === "__inactive__"),
  [["__inactive__", ["waitUntil", "args", "headless", "userAgent", "userDataDir"], false]],
);
check(
  "缺省通道：高级组因 timeoutMs 未填值而收起",
  planLabels({}).find((p) => p[0] === "高级")[2],
  false,
);
check(
  "填了 timeoutMs → 高级组自动展开",
  planLabels({ timeoutMs: 5000 }).find((p) => p[0] === "高级")[2],
  true,
);

// playwright 通道：启动选项字段归位到「启动选项」组，无 inactive
const pw = planLabels({ transport: "playwright", headless: true });
check(
  "playwright 通道：启动选项组归位 5 字段并展开",
  pw.find((p) => p[0] === "启动选项（仅 playwright 通道）"),
  ["启动选项（仅 playwright 通道）", ["waitUntil", "args", "headless", "userAgent", "userDataDir"], true],
);
check(
  "playwright 通道：无 inactive 字段",
  pw.find((p) => p[0] === "__inactive__"),
  undefined,
);

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
