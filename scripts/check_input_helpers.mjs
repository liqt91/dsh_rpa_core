// 校验扩展执行通道的输入参数纯函数区（模式归一化 / 逐字间隔解析）。
// 做法与 check_capture_helpers.mjs 一致：从 background.js 按锚点切片，求值后断言矩阵。
// 背景：browser.input 的 mode/keyIntervalMs 曾在 manifest 里声明却不生效（漂移），
// 这里把「归一化与解析」钉死，避免再次静默失效。
// 用法：node scripts/check_input_helpers.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "extension", "background.js"), "utf8");

try {
  new Function(source);
} catch (err) {
  console.error(`FAIL: background.js 语法错误 → ${err.message}`);
  process.exit(1);
}

const START = "// [input-helpers:start]";
const END = "// [input-helpers:end]";
const start = source.indexOf(START);
const end = source.indexOf(END);
if (start < 0 || end < 0 || end <= start) {
  console.error(`FAIL: 未能定位纯函数区（锚点 ${START} / ${END}）`);
  process.exit(1);
}
const slice = source.slice(start, end);

const factory = new Function(
  `${slice}\nreturn { inputMode, inputGapMs };`,
);
const { inputMode, inputGapMs } = factory();

let failed = 0;
const check = (label, actual, expected) => {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failed += 1;
    console.error(`FAIL | ${label}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`);
  } else {
    console.log(`PASS | ${label}`);
  }
};

// ---- inputMode：manifest 声明 fill/type/clipboard；"set" 为历史别名 ----
check("fill 原样", inputMode("fill"), "fill");
check("type 原样", inputMode("type"), "type");
check("clipboard 原样", inputMode("clipboard"), "clipboard");
check("历史别名 set → fill", inputMode("set"), "fill");
check("大小写与空白容忍（ TYPE ）", inputMode("  TYPE "), "type");
check("缺省（undefined）→ fill（与 manifest 默认一致）", inputMode(undefined), "fill");
check("空串 → fill", inputMode(""), "fill");
check("未知值 → fill（不静默当作逐字）", inputMode("paste"), "fill");
check("非字符串（数字）→ fill", inputMode(123), "fill");

// ---- inputGapMs：非数字/负数 → 0；上限 5000 ----
check("正常值", inputGapMs(50), 50);
check("字符串数字", inputGapMs("120"), 120);
check("缺省 → 0", inputGapMs(undefined), 0);
check("null → 0", inputGapMs(null), 0);
check("非数字 → 0", inputGapMs("abc"), 0);
check("负数 → 0", inputGapMs(-5), 0);
check("零 → 0", inputGapMs(0), 0);
check("上限收敛", inputGapMs(999999), 5000);

// ---- 反漂移：扩展的 input 分支必须真的 await 间隔、且提供 clipboard 分支 ----
if (!/await delay\(gap\)/.test(source)) {
  failed += 1;
  console.error("FAIL | 逐字输入未 await delay(gap)：keyIntervalMs 会再次失效");
} else {
  console.log("PASS | 逐字输入按间隔 await");
}
if (!/injectPaste\(el, text\)/.test(source)) {
  failed += 1;
  console.error("FAIL | 未找到 clipboard 的 injectPaste 调用：cloneboard 模式会再次退化成逐字");
} else {
  console.log("PASS | clipboard 模式有粘贴注入实现");
}
if (!/inputRejected/.test(source)) {
  failed += 1;
  console.error("FAIL | 未找到 inputRejected 显式失败标记：粘贴未被接受时会静默成功");
} else {
  console.log("PASS | 粘贴未被接受时显式失败");
}

if (failed) {
  console.error(`\n输入参数纯函数校验失败：${failed} 项`);
  process.exit(1);
}
console.log("\n输入参数纯函数校验全部通过");
