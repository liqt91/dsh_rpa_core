// 校验捕获扩展「高亮框几何」纯函数区（content.js 的 capture-overlay-geometry）。
// 做法与 check_capture_helpers.mjs 一致：按首尾锚点切片，用真实元素 rect 求值后断言。
//
// 为什么必须用 node 验：这段几何**只在浏览器里跑**。Python 侧读源码只能证明
// 「写了 left - OVERLAY_OUTSET 这行字」，证明不了「框线带落在元素外 [2,5)、碰不到指针」。
// 而它唯一的职责就是让框线躲开鼠标（桌面侧同款几何的真机实测：旧实现 6/13 个采样点的
// 指针被框线像素覆盖，最近距离 0）。
//
// 用法：node scripts/check_capture_overlay_geometry.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "extension", "content.js"), "utf8");

// 0) 整文件语法校验（new Function 只编译不执行）
try {
  new Function(source);
} catch (err) {
  console.error(`FAIL: content.js 语法错误 → ${err.message}`);
  process.exit(1);
}

const START = "// [capture-overlay-geometry:start]";
const END = "// [capture-overlay-geometry:end]";
const start = source.indexOf(START);
const end = source.indexOf(END);
if (start < 0 || end < 0 || end <= start) {
  console.error(`FAIL: 未能从 content.js 定位高亮框几何区（锚点 ${START} / ${END}）`);
  process.exit(1);
}
// 锚点行须独占一行（同 check_element_display_helpers.mjs 的约定）。这里再兜一层：
// 若锚点后仍跟有同行文字，会被当成代码求值而报 “Invalid or unexpected token”——
// 那种报错离现场很远，所以统一在第一个换行处切开。
const rawSlice = source.slice(start + START.length, end);
const slice = rawSlice.slice(rawSlice.indexOf("\n") + 1);

let geometry;
try {
  geometry = new Function(`${slice}
return { overlayBoxRect, OVERLAY_BORDER, OVERLAY_OUTSET };`)();
} catch (err) {
  console.error(`FAIL: 高亮框几何区求值失败 → ${err.message}`);
  process.exit(1);
}
const { overlayBoxRect, OVERLAY_BORDER, OVERLAY_OUTSET } = geometry;

let failed = 0;
const check = (label, got, expected) => {
  const same = JSON.stringify(got) === JSON.stringify(expected);
  if (!same) failed += 1;
  console.log(
    `${same ? "PASS" : "FAIL"} | ${label}${same ? "" : ` → got ${JSON.stringify(got)} want ${JSON.stringify(expected)}`}`,
  );
};
const ok = (label, cond, detail) => {
  if (!cond) failed += 1;
  console.log(`${cond ? "PASS" : "FAIL"} | ${label}${cond ? "" : ` → ${detail}`}`);
};

// ---- 1) 几何口径 -------------------------------------------------------------
check("框线宽 = 3px", OVERLAY_BORDER, 3);
check("外移量 = 框线宽 + 指针热区(2)", OVERLAY_OUTSET, OVERLAY_BORDER + 2);

// ---- 2) 四条框线带整条落在元素之外，且元素内无处指针热区压线 ------------------
const CURSOR_HOTSPOT = 2;
const frameBands = (rect) => {
  const box = overlayBoxRect(rect);
  return {
    left: [box.left, box.left + OVERLAY_BORDER],
    right: [box.left + box.width - OVERLAY_BORDER, box.left + box.width],
    top: [box.top, box.top + OVERLAY_BORDER],
    bottom: [box.top + box.height - OVERLAY_BORDER, box.top + box.height],
  };
};
const overlaps = (a, b) => a[0] < b[1] && b[0] < a[1];

const RECTS = [
  { name: "普通控件", left: 100, top: 100, width: 40, height: 24 },
  { name: "16px 图标（最坏情况）", left: 0, top: 0, width: 16, height: 16 },
  { name: "整窗口", left: 0, top: 0, width: 1280, height: 720 },
  { name: "负坐标（元素滚出视口）", left: -40, top: -30, width: 160, height: 120 },
];

for (const rect of RECTS) {
  const bands = frameBands(rect);
  const right = rect.left + rect.width;
  const bottom = rect.top + rect.height;
  ok(`${rect.name}：左边框带在元素外`, bands.left[1] <= rect.left - CURSOR_HOTSPOT, JSON.stringify(bands.left));
  ok(`${rect.name}：上边框带在元素外`, bands.top[1] <= rect.top - CURSOR_HOTSPOT, JSON.stringify(bands.top));
  ok(`${rect.name}：右边框带在元素外`, bands.right[0] >= right + CURSOR_HOTSPOT, JSON.stringify(bands.right));
  ok(`${rect.name}：下边框带在元素外`, bands.bottom[0] >= bottom + CURSOR_HOTSPOT, JSON.stringify(bands.bottom));

  let touched = 0;
  for (let x = rect.left; x <= right; x += 1) {
    const hot = [x - CURSOR_HOTSPOT, x + CURSOR_HOTSPOT];
    if (overlaps(hot, bands.left) || overlaps(hot, bands.right)) touched += 1;
  }
  for (let y = rect.top; y <= bottom; y += 1) {
    const hot = [y - CURSOR_HOTSPOT, y + CURSOR_HOTSPOT];
    if (overlaps(hot, bands.top) || overlaps(hot, bands.bottom)) touched += 1;
  }
  ok(`${rect.name}：元素内无一处指针热区压到框线`, touched === 0, `压到 ${touched} 处`);
}

// ---- 3) 旧几何确实会压住指针（证明上面的判据不是空转） -----------------------
{
  const rect = RECTS[0];
  const legacyLeftBand = [rect.left, rect.left + OVERLAY_BORDER]; // 旧：带在元素最外 3px
  const hot = [rect.left - CURSOR_HOTSPOT, rect.left + CURSOR_HOTSPOT];
  ok(
    "旧几何（内缩）会压住贴边指针——判据有牙齿",
    overlaps(hot, legacyLeftBand),
    "旧几何竟然不压线，说明判据或取样有问题",
  );
}

// ---- 4) 样式：只画框线、不填充（填充会盖住元素内容） -------------------------
const showStart = source.indexOf("const show = (el) => {");
const tipAt = source.indexOf("tip.style.top", showStart);
const showEnd = source.indexOf("};", tipAt);
if (showStart < 0 || tipAt < 0 || showEnd <= showStart) {
  console.error("FAIL: 未能定位 content.js 的 show()");
  process.exit(1);
}
const showBody = source.slice(showStart, showEnd);
ok("高亮框声明 border-box（外扩后的 width 才是外沿）", showBody.includes("box-sizing:border-box"), showBody.slice(0, 200));
ok("高亮框用 OVERLAY_BORDER 画边", showBody.includes("${OVERLAY_BORDER}px solid"), "");
ok("高亮框没有填充", !/background/.test(showBody), showBody.slice(0, 200));
ok("高亮框位置取自 overlayBoxRect", showBody.includes("overlayBoxRect(r)"), "");

if (failed) {
  console.error(`\n${failed} 项失败`);
  process.exit(1);
}
console.log("\n全部通过");
