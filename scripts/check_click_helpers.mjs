// 校验扩展执行通道的点击参数纯函数区（M29 S1：`simulateHuman` / `clickPosition` 实装）。
// 做法与 check_input_helpers.mjs / check_precheck_helpers.mjs 一致：从 background.js 按锚点切片，
// 求值后断言矩阵，再对整份源码做反漂移断言。
// 背景：`browser.click` 的这两个参数在 manifest 里声明、GUI 里能勾，但扩展侧**从来没读过**——
// 点击事件连坐标都没有（clientX/clientY 恒为 0），`modifiers` 的枚举值 Ctrl/Win 也对不上
// 事件字段（"Control"/"Meta"），勾了等于没勾。这里把「点哪个坐标」「修饰键怎么映射」
// 「什么时候能走 el.click()」钉死，并交叉校验 Python 侧真的转发了这两个参数。
// 用法：node scripts/check_click_helpers.mjs   （退出码非 0 = 有失败）
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

const START = "// [click-helpers:start]";
const END = "// [click-helpers:end]";
const start = source.indexOf(START);
const end = source.indexOf(END);
if (start < 0 || end < 0 || end <= start) {
  console.error(`FAIL: 未能定位纯函数区（锚点 ${START} / ${END}）`);
  process.exit(1);
}
const slice = source.slice(start, end);

let failed = 0;
const factory = new Function(
  `${slice}\nreturn { clickPoint, clickPositionMode, modifierFlags, simulateHumanEnabled, useProgrammaticClick, CLICK_POINT_BAND };`,
);
const {
  clickPoint,
  clickPositionMode,
  modifierFlags,
  simulateHumanEnabled,
  useProgrammaticClick,
  CLICK_POINT_BAND,
} = factory();

const check = (label, actual, expected) => {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failed += 1;
    console.error(`FAIL | ${label}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`);
  } else {
    console.log(`PASS | ${label}`);
  }
};

const VP = { width: 1000, height: 800 };
const NO_FLAGS = { altKey: false, ctrlKey: false, metaKey: false, shiftKey: false };
const rect = { left: 100, top: 50, width: 200, height: 100, right: 300, bottom: 150 };

// ---- clickPositionMode：manifest 只有 center/random ----
check("center 原样", clickPositionMode("center"), "center");
check("random 原样", clickPositionMode("random"), "random");
check("大小写与空白容忍（ RANDOM ）", clickPositionMode(" RANDOM "), "random");
check("缺省（undefined）→ center", clickPositionMode(undefined), "center");
check("空串 → center", clickPositionMode(""), "center");
check("未知值 → center（不静默当随机）", clickPositionMode("edge"), "center");

// ---- clickPoint：center 取交集中心；random 落在偏中心带 ----
check("center：元素完全在视口内 → 几何中心", clickPoint("center", rect, null, VP), { x: 200, y: 100 });
check("缺省位置 → center", clickPoint(undefined, rect, null, VP), { x: 200, y: 100 });
check("random=0 → 区间下界（15%）", clickPoint("random", rect, () => 0, VP), { x: 130, y: 65 });
check("random=1 → 区间上界（85%）", clickPoint("random", rect, () => 1, VP), { x: 270, y: 135 });
check("random=0.5 → 与 center 同点", clickPoint("random", rect, () => 0.5, VP), { x: 200, y: 100 });
check("random 落在元素内（不越界）", CLICK_POINT_BAND > 0 && CLICK_POINT_BAND < 0.5, true);
check("rand 返回 NaN → 取区间下界（不产生 NaN 坐标）", clickPoint("random", rect, () => NaN, VP), { x: 130, y: 65 });
check("rand 越界（-5）收敛到下界", clickPoint("random", rect, () => -5, VP), { x: 130, y: 65 });
check("rand 越界（99）收敛到上界", clickPoint("random", rect, () => 99, VP), { x: 270, y: 135 });
check("非函数 rand 容忍（退回 Math.random 且仍是合法点）", typeof clickPoint("random", rect, 42, VP).x, "number");

const partial = { left: -50, top: 700, width: 400, height: 100, right: 350, bottom: 800 };
check("部分在视口外 → 按「元素 ∩ 视口」取点", clickPoint("center", partial, null, VP), { x: 175, y: 750 });
const outside = { left: 1200, top: 900, width: 100, height: 50, right: 1300, bottom: 950 };
check("完全在视口外 → null（调用方按不可见报错）", clickPoint("center", outside, null, VP), null);
check("零尺寸 → null", clickPoint("center", { left: 10, top: 10, width: 0, height: 0, right: 10, bottom: 10 }, null, VP), null);
check("rect 缺失 → null", clickPoint("center", null, null, VP), null);
check("视口尺寸缺失 → null（不猜坐标）", clickPoint("center", rect, null, {}), null);
check("只有 left/top/width/height（无 right/bottom）也能算", clickPoint("center", { left: 0, top: 0, width: 100, height: 100 }, null, VP), { x: 50, y: 50 });

// ---- modifierFlags：manifest 枚举 Alt/Ctrl/Shift/Win 必须落到事件字段 ----
check("Alt → altKey", modifierFlags(["Alt"]).altKey, true);
check("Ctrl → ctrlKey（此前的漂移点：扩展只认 \"Control\"）", modifierFlags(["Ctrl"]).ctrlKey, true);
check("Shift → shiftKey", modifierFlags(["Shift"]).shiftKey, true);
check("Win → metaKey（此前的漂移点：扩展只认 \"Meta\"）", modifierFlags(["Win"]).metaKey, true);
check("组合", modifierFlags(["Ctrl", "Shift"]), { altKey: false, ctrlKey: true, metaKey: false, shiftKey: true });
check("大小写与空白容忍（ ctrl ）", modifierFlags([" ctrl "]).ctrlKey, true);
check("未知值忽略", modifierFlags(["Foo"]), NO_FLAGS);
check("非数组容忍", modifierFlags(undefined), NO_FLAGS);
check("空数组", modifierFlags([]), NO_FLAGS);

// ---- simulateHumanEnabled：manifest 默认 true，只有显式 false 才关 ----
check("缺省（undefined）→ 开", simulateHumanEnabled(undefined), true);
check("null → 开", simulateHumanEnabled(null), true);
check("true → 开", simulateHumanEnabled(true), true);
check("false → 关", simulateHumanEnabled(false), false);
check('字符串 "false" → 关', simulateHumanEnabled("false"), false);
check('字符串 " FALSE " → 关', simulateHumanEnabled(" FALSE "), false);

// ---- useProgrammaticClick：只有「显式关 + 普通左键单击」才走最短路径 ----
check("显式关 + 普通左键单击 → el.click()", useProgrammaticClick(false, "left", [], "single"), true);
check("缺省（模拟人工开）→ 事件链", useProgrammaticClick(true, "left", [], "single"), false);
check("未传 simulateHuman → 事件链", useProgrammaticClick(undefined, "left", [], "single"), false);
check("右键 → 仍走事件链（el.click() 表达不了）", useProgrammaticClick(false, "right", [], "single"), false);
check("中键 → 仍走事件链", useProgrammaticClick(false, "middle", [], "single"), false);
check("双击 → 仍走事件链", useProgrammaticClick(false, "left", [], "double"), false);
check("带辅助键 → 仍走事件链", useProgrammaticClick(false, "left", ["Ctrl"], "single"), false);
check("button/clickType 缺省视作 left/single", useProgrammaticClick(false, undefined, undefined, undefined), true);

// ---- 反漂移：纯函数必须真的被用上，且不能再回退到「声明了不生效」 ----
const mustMatch = [
  ["点击分支按参数决定是否走最短路径", /useProgrammaticClick\(args\.simulateHuman, args\.button, modifiers, args\.clickType\)/],
  ["最短路径真的调 el.click()", /el\.click\(\)/],
  ["点击事件带上真实坐标 x", /clientX: point\.x/],
  ["点击事件带上真实坐标 y", /clientY: point\.y/],
  ["修饰键走归一化函数（而非直接比字符串）", /Object\.assign\(init, modifierFlags\(modifiers\)\)/],
  ["点击点按 manifest 的 clickPosition 选点", /method === "click" \? args\.clickPosition : "center"/],
  ["点击点由 clickPoint 统一裁剪计算", /actionPoint = clickPoint\(/],
  ["遮挡判定使用同一个点击点", /coveringElement\(el, el\.getClientRects\(\), actionPoint\)/],
  ["视口外/交集为空时结构化报 ELEMENT_NOT_VISIBLE", /precheck:\s*\{\s*code:\s*"ELEMENT_NOT_VISIBLE"/],
];
for (const [label, pattern] of mustMatch) {
  if (!pattern.test(source)) {
    failed += 1;
    console.error(`FAIL | ${label}：源码里找不到 ${pattern}`);
  } else {
    console.log(`PASS | ${label}`);
  }
}

if (/===\s*"Control"/.test(source)) {
  failed += 1;
  console.error('FAIL | 又出现了 "Control"：manifest 枚举是 Ctrl，直接比字符串会让 ctrlKey 永远不生效');
} else {
  console.log("PASS | 没有回退到与 manifest 枚举对不上的字符串比较");
}

// ---- 交叉校验：Python 侧必须转发这两个参数（默认值与 manifest 一致） ----
const executorPy = readFileSync(join(here, "..", "src", "rpa_core", "executors", "browser.py"), "utf8");
if (!/"simulateHuman": bool\(inputs\.get\("simulateHuman", True\)\)/.test(executorPy)) {
  failed += 1;
  console.error("FAIL | 执行器未转发 simulateHuman（默认须为 manifest 的 true）");
} else {
  console.log("PASS | 执行器转发 simulateHuman（默认 true）");
}
if (!/"clickPosition": inputs\.get\("clickPosition"\) or "center"/.test(executorPy)) {
  failed += 1;
  console.error("FAIL | 执行器未转发 clickPosition（默认须为 manifest 的 center）");
} else {
  console.log("PASS | 执行器转发 clickPosition（默认 center）");
}

// ---- 交叉校验：manifest 的默认值/说明不能漂 ----
const manifest = JSON.parse(
  readFileSync(join(here, "..", "commands", "browser", "click.json"), "utf8"),
);
const props = (manifest.input_schema || {}).properties || {};
if (props.simulateHuman && props.simulateHuman.default !== true) {
  failed += 1;
  console.error("FAIL | manifest simulateHuman 默认值应为 true");
} else {
  console.log("PASS | manifest simulateHuman 默认 true");
}
if (props.clickPosition && props.clickPosition.default !== "center") {
  failed += 1;
  console.error("FAIL | manifest clickPosition 默认值应为 center");
} else {
  console.log("PASS | manifest clickPosition 默认 center");
}
if (props.simulateHuman && /被遮挡时可用/.test(String(props.simulateHuman.description || ""))) {
  failed += 1;
  console.error("FAIL | simulateHuman 说明又写回了「待元素被遮挡时可用」——遮挡是显式报错，不是靠这个开关绕过");
} else {
  console.log("PASS | simulateHuman 说明未误导「可绕过遮挡」");
}

if (failed) {
  console.error(`\n点击参数纯函数校验失败：${failed} 项`);
  process.exit(1);
}
console.log("\n点击参数纯函数校验全部通过");
