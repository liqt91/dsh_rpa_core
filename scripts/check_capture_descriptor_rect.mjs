// 校验捕获描述符里的元素矩形键名（content.js 的 rectOf），并与 host 换算函数对账。
// 做法与 check_capture_overlay_geometry.mjs 一致：按首尾锚点切片，真跑一次求值。
//
// 为什么必须真跑、且必须与 host 侧交叉核对（2026-10-09 真机事故）：
// 捕获回传里的 rect 原写成 {x, y}，而 host 的 screen_shot.browser_box_in_window
// 只读 {left, top}。后果是捕获时那张截图**恒算不出红框**——函数里
// `except (KeyError, ...) return None` 把 KeyError 吞了，于是只表现为
//「截图有、框没有」，看起来像功能没实现，而不是像键名对不上。
//
// **同一个仓库里两套 rect 键名并存**，而且各自都有消费面：
//   - 输入契约（两条腿 + 两个 JS 产出点）：left/top
//     （desktop_agent 回 left/top、firstHitRect 回 left/top、verify.py 读 left）
//   - 输出 box（screen_shot 内部产物、GUI 画框）：x/y
// 所以光断言「content.js 里出现了 left」不够——`x/y` 也确实在文件里出现过。
// 本门禁做三件事：
//   W1 真跑 rectOf，断言键**恰好**是 left/top/width/height（不多不少）；
//   W2 把 rectOf 的输出喂给 **host 侧真实的换算函数**，断言真的算出红框；
//   W3 断言输出是 x/y/width/height（GUI 画框真正读的那套）。
//
// W2 为什么是**子进程调真 Python**、而不是在 node 里手写 Python→JS 转译：
// 手写转译器等于引入第二个真相源——它对 Python 语法的覆盖必然不完整，函数一改
// （换个构造、换个类型注解）门禁就开始报「求值失败」，而这个失败与它要抓的缺陷
// 毫无关系。实测第一版正是这样：转译产物语法坏，报的是
// `Unexpected identifier 'browser_box_in_window'`，离现场很远。
// 子进程里跑的是**真函数**，Python 改了门禁自动跟着对账。
//
// 用法：node scripts/check_capture_descriptor_rect.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const source = readFileSync(join(root, "extension", "content.js"), "utf8");

// 0) 整文件语法校验（new Function 只编译不执行）
try {
  new Function(source);
} catch (err) {
  console.error(`FAIL: content.js 语法错误 → ${err.message}`);
  process.exit(1);
}

// ---------------------------------------------------------------- W1: rectOf 的键
const START = "// [capture-descriptor-rect:start]";
const END = "// [capture-descriptor-rect:end]";
const start = source.indexOf(START);
const end = source.indexOf(END);
if (start < 0 || end < 0 || end <= start) {
  console.error(`FAIL: 未能从 content.js 定位 rectOf 区（锚点 ${START} / ${END}）`);
  process.exit(1);
}
// 锚点行须独占一行；统一在第一个换行处切开（锚点后有同行文字会被当代码求值，
// 报出离现场很远的 "Invalid or unexpected token"）。
const rawSlice = source.slice(start + START.length, end);
const slice = rawSlice.slice(rawSlice.indexOf("\n") + 1);

let rectOf;
try {
  rectOf = new Function(`${slice}
return rectOf;`)();
} catch (err) {
  console.error(`FAIL: rectOf 区求值失败 → ${err.message}`);
  process.exit(1);
}

const rect = rectOf({
  getBoundingClientRect: () => ({ left: 12.4, top: 30.6, width: 300.2, height: 40.1 }),
});
const keys = Object.keys(rect).sort();

// W1a：键集恰好是四个。**「恰好」而非「包含」**——多一个 x/y 键同样是坑：
// 下游若按 left 取值而上游某天改成 x，症状与完全没写一模一样。
const expected = ["height", "left", "top", "width"];
if (JSON.stringify(keys) !== JSON.stringify(expected)) {
  console.error(
    `FAIL: rectOf 的键集应为 ${JSON.stringify(expected)}，实际 ${JSON.stringify(keys)}\n` +
      "  （捕获回传的 rect 必须与 host 换算函数读的是同一套键名；" +
      "x/y 那套是 screen_shot 输出 box 的口径，混用会让红框恒为空）",
  );
  process.exit(1);
}

// W1b：取整行为（content.js 历来 Math.round，别让某次改成小数）。
if (rect.left !== 12 || rect.top !== 31 || rect.width !== 300 || rect.height !== 40) {
  console.error(`FAIL: rectOf 的取整行为变了 → ${JSON.stringify(rect)}（应为整数）`);
  process.exit(1);
}

// W2/W3：与 host 换算函数对账
// 子进程里跑**真** Python：输入就是上面 rectOf 的输出，于是「JS 产出」与
//「host 消费」真的对上，而不是两边各自绿。
const PY = join(root, ".venv", "Scripts", "python.exe");

// 喂给 host 的 viewport 用**真机实测值**（2026-10-09 trace 那一行）：
//   窗口 outerWidth/Height = 1555/936，视口 width/height = 1539/785，
//   窗口在副屏上方 ⇒ screenX/screenY = 229/-939，与 GetWindowRect 的
//   windowOrigin 完全相等（这正是它**不能**用来推视口位置的铁证）。
const REAL_VIEWPORT = {
  width: 1539,
  height: 785,
  dpr: 1,
  screenX: 229,
  screenY: -939,
  outerWidth: 1555,
  outerHeight: 936,
};
const REAL_IMAGE_SIZE = [1555, 936];
// 真机那个元素在图内应是 y = 79 + 143（视口顶 = 936 - 785 - 8）。
const REAL_BORDER = 8;
const REAL_VIEWPORT_TOP = 143;

function callHost(fn, payload) {
  const probe = [
    "import json",
    "from rpa_core.capture.screen_shot import " + fn,
    "payload = " + JSON.stringify(payload),
    "box = " + fn + "(payload['rect'], payload['viewport'], tuple(payload['imageSize']))",
    "print(json.dumps(box))",
  ].join("\n");
  return execFileSync(PY, ["-c", probe], {
    cwd: root,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
}

let box;
try {
  const out = callHost("browser_box_in_window", {
    rect,
    viewport: REAL_VIEWPORT,
    imageSize: REAL_IMAGE_SIZE,
  });
  box = JSON.parse(out.trim().split(/\r?\n/).pop());
} catch (err) {
  console.error(
    "FAIL: 调 host 侧 browser_box_in_window 失败 → "
      + String(err.stderr || err.message).split(/\r?\n/).slice(0, 4).join("\n"),
  );
  process.exit(1);
}

// W2：rectOf 的输出喂进去必须算出框。数值取自真机量级（视口 1280 宽、图宽 1936、
// 窗口原点 -8 —— 负值是最大化窗口的常态）。
if (!box || typeof box.x !== "number" || typeof box.y !== "number") {
  console.error(
    "FAIL: host 换算函数吃下 rectOf 的输出后仍算不出红框 ⇒ 两侧键名对不上\n" +
      `  喂进去的是 ${JSON.stringify(rect)}`,
  );
  process.exit(1);
}

// W3：输出必须是 GUI 画框真正读的那套 x/y（见 element_editor 里
// `self._box["x"]`）。这条钉住「输入 left/top → 输出 x/y」不是笔误。
const boxKeys = Object.keys(box).sort();
if (JSON.stringify(boxKeys) !== JSON.stringify(["height", "width", "x", "y"])) {
  console.error(
    `FAIL: host 换算函数的输出键应为 x/y/width/height，实际 ${JSON.stringify(boxKeys)}\n` +
      "  （GUI 画框只读 box.x / box.y；输出键名一改，红框就画不出来）",
  );
  process.exit(1);
}

console.log("check_capture_descriptor_rect: PASS（rectOf 键集 + 与 host 换算函数对账）");

// ------------------------------------------------------------------ W4/W5
// W4：**换算必须用「窗口尺寸」推视口位置，不能用 screenX/screenY**。
//
// 真机事故（2026-10-09，维护者报「截图位置偏上」）：`window.screenY` 是**窗口**
// 左上角的屏幕坐标，**不是视口的**——实测它与 GetWindowRect 的 top 完全相等
// （都是 -939），那个减法恒等于 0，于是整整少算了标签栏+地址栏（真机 151px），
// 裁剪区偏上约 143px。
//
// 换算必须读 viewport 里的 `outerWidth/outerHeight`，所以把那两个字段**删掉**
// 再喂一次：此时 host **必须返回 None**（宁可不出裁剪区，也不给一个偏 143px 的）。
// 若仍返回框 ⇒ 说明它还在用某条不需要窗口尺寸的路径（多半是 screenY）。
const withoutWindowSize = { ...REAL_VIEWPORT };
delete withoutWindowSize.outerWidth;
delete withoutWindowSize.outerHeight;
try {
  const out = callHost("browser_box_in_window", {
    rect,
    viewport: withoutWindowSize,
    imageSize: REAL_IMAGE_SIZE,
  });
  const noSize = JSON.parse(out.trim().split(/\r?\n/).pop());
  if (noSize !== null) {
    console.error(
      "FAIL: viewport 缺 outerWidth/outerHeight 时仍算出了框 → "
        + "换算没走「窗口内部布局」这条路（八成又用回 screenX/screenY 了）\n"
        + `  实得 ${JSON.stringify(noSize)}`,
    );
    process.exit(1);
  }
} catch (err) {
  console.error("FAIL: W4 调 host 失败 → " + String(err.stderr || err.message).split(/\r?\n/)[0]);
  process.exit(1);
}

// W5：裁剪与画框**必须落在同一位置**——两者只差表达（crop=裁一块、box=画一个框），
// 坐标不一致就意味着同一份descriptor 在两条路上会落在不同位置，
// 而那正是「框在一个位置、裁剪在另一个位置」这类症状的温床。
let crop;
try {
  const out = callHost("browser_crop_in_window", {
    rect,
    viewport: REAL_VIEWPORT,
    imageSize: REAL_IMAGE_SIZE,
  });
  crop = JSON.parse(out.trim().split(/\r?\n/).pop());
} catch (err) {
  console.error("FAIL: W5 调 host 失败 → " + String(err.stderr || err.message).split(/\r?\n/)[0]);
  process.exit(1);
}
if (!crop || typeof crop.y !== "number") {
  console.error("FAIL: browser_crop_in_window 没算出裁剪区");
  process.exit(1);
}
// 真机那个元素在图内 y = 79 + 视口顶 143 = 222（视口顶 = 936 - 785 - 8）。
const realElY = rect.top + REAL_VIEWPORT_TOP;
if (!(crop.y <= realElY && crop.y + crop.height >= realElY + rect.height)) {
  console.error(
    `FAIL: 裁剪区没罩住真机那个元素（应含 y ${realElY}）→ ${JSON.stringify(crop)}\n`
      + `  视口顶应为 ${REAL_VIEWPORT_TOP}（= ${REAL_VIEWPORT.outerHeight}`
      + ` - ${REAL_VIEWPORT.height} - ${REAL_BORDER}）`,
  );
  process.exit(1);
}
if (!(box.y <= realElY && box.y + box.height >= realElY + rect.height)) {
  console.error(
    `FAIL: 红框没罩住真机那个元素（应含 y ${realElY}）→ ${JSON.stringify(box)}`,
  );
  process.exit(1);
}

console.log(
  "check_capture_descriptor_rect: PASS"
  + "（+ 换算走窗口内部布局、crop/box 同位）",
);
