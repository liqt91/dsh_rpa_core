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

// navigate.json 3.4.0 的分组声明：常规(browserType+url+action) + 高级(timeoutMs+onTimeout+commandLineArgs 折叠)
const properties = {
  browserType: {}, url: {}, action: {}, timeoutMs: {}, onTimeout: {}, commandLineArgs: {},
};
const groups = [
  { label: "常规", fields: ["browserType", "url", "action"] },
  { label: "高级", fields: ["timeoutMs", "onTimeout", "commandLineArgs"], collapsed: true },
];

let failed = 0;
const check = (label, got, expected) => {
  const ok = JSON.stringify(got) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(`${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → got ${JSON.stringify(got)} want ${JSON.stringify(expected)}`}`);
};

const planLabels = (withArgs) => paramGroupPlan({}, properties, withArgs, groups)
  .map((p) => [p.label, p.fields, p.open]);

check(
  "常规组：browserType + url + action 归位按序遍历并展开",
  planLabels({ url: "https://x" }).find((p) => p[0] === "常规"),
  ["常规", ["browserType", "url", "action"], true],
);
check(
  "高级组：timeoutMs 未填值 → 收起",
  planLabels({}).find((p) => p[0] === "高级")[2],
  false,
);
check(
  "填了 timeoutMs → 高级组展开",
  planLabels({ timeoutMs: 5000 }).find((p) => p[0] === "高级")[2],
  true,
);
check(
  "无通道参数：两组归位、不出现其他/折叠残留字段",
  planLabels({}),
  [
    ["常规", ["browserType", "url", "action"], true],
    ["高级", ["timeoutMs", "onTimeout", "commandLineArgs"], false],
  ],
);

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);