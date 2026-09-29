// 默认元素名生成器（A3）的 JS 侧判据：与 tests/contract/test_element_naming.py
// 跑**同一份用例表**（tests/contract/data/element_name_cases.json）。做法与
// check_element_display_helpers.mjs 一致——从 app.js 的锚点切出纯函数区真求值
// （这段只在浏览器里跑，Python 读源码只能证明「写了这行字」）。
// 用法：node scripts/check_element_name.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = join(here, "..");
const source = readFileSync(
  join(ROOT, "src", "rpa_core", "devserver", "static", "app.js"),
  "utf8",
);
const cases = JSON.parse(
  readFileSync(join(ROOT, "tests", "contract", "data", "element_name_cases.json"), "utf8"),
);

// 锚点必须恰好一次（半截注释/重复锚点都会让切片静默漂移）
const START = "// [element-name-helpers:start]";
const END = "// [element-name-helpers:end]";
if (source.split(START).length !== 2 || source.split(END).length !== 2) {
  console.error("FAIL: element-name-helpers 锚点必须恰好出现一次（start/end 各一）");
  process.exit(1);
}
const slice = source.slice(source.indexOf(START), source.indexOf(END) + END.length);

let failed = 0;
const check = (label, actual, expected) => {
  const ok = actual === expected;
  if (!ok) failed += 1;
  console.log(`${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → got ${JSON.stringify(actual)} want ${JSON.stringify(expected)}`}`);
};

let factory;
try {
  factory = new Function(`${slice}\nreturn { normalizeElementHint, suggestElementName };`);
} catch (err) {
  console.error(`FAIL: 切片段语法错误 → ${err.message}`);
  process.exit(1);
}
const { normalizeElementHint, suggestElementName } = factory();

if (typeof suggestElementName !== "function" || typeof normalizeElementHint !== "function") {
  console.error("FAIL: 切片区必须导出 normalizeElementHint 与 suggestElementName");
  process.exit(1);
}

for (const { existing, hint, expected } of cases.cases) {
  const label = `suggest(${JSON.stringify(existing)}, ${JSON.stringify(hint)})`;
  let actual;
  try {
    actual = suggestElementName(existing, hint);
  } catch (err) {
    actual = `<throw ${err.message}>`;
  }
  check(label, actual, expected);
}

// 接线：捕获确认框必须真用生成器，时间戳命名必须已消失
check("捕获确认框默认名走 suggestElementName", source.includes("suggestElementName(existingNames, hint)"), true);
check("时间戳默认名不再出现", source.includes("Date.now() % 100000"), false);

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
