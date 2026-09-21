// 校验扩展执行通道的执行前预检纯函数区（M28 S2）。
// 做法与 check_input_helpers.mjs 一致：从 background.js 按锚点切片，求值后断言矩阵。
// 背景：被遮挡/隐藏/禁用的目标点下去会**静默点到遮罩层**（报成功但点错地方）。
// 这里把「哪些 method 要预检」和「怎么判定禁用」钉死，避免预检被后来的改动悄悄摘掉。
// 用法：node scripts/check_precheck_helpers.mjs   （退出码非 0 = 有失败）
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

const START = "// [precheck-helpers:start]";
const END = "// [precheck-helpers:end]";
const start = source.indexOf(START);
const end = source.indexOf(END);
if (start < 0 || end < 0 || end <= start) {
  console.error(`FAIL: 未能定位纯函数区（锚点 ${START} / ${END}）`);
  process.exit(1);
}
const slice = source.slice(start, end);

let failed = 0;

// ---- 预检区里引用了浏览器全局（window/document）：给一个最小替身再求值 ----
const makeFactory = (globals) =>
  new Function(
    "window",
    "document",
    `${slice}\nreturn { precheckRequired, elementPrecheck, coveringElement, isDisabledElement, isElementVisible, PRECHECK_MESSAGES };`,
  )(globals.window, globals.document);

const check = (label, actual, expected) => {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failed += 1;
    console.error(`FAIL | ${label}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`);
  } else {
    console.log(`PASS | ${label}`);
  }
};

// 最小 DOM 替身：够 elementPrecheck 走完判定即可
const stubElement = (overrides = {}) => ({
  isConnected: true,
  disabled: false,
  getAttribute: () => null,
  closest: () => null,
  getClientRects: () => [{ width: 20, height: 10, left: 5, top: 5 }],
  getBoundingClientRect: () => ({ width: 20, height: 10, left: 5, top: 5 }),
  tagName: "BUTTON",
  id: "target",
  className: "btn",
  contains: () => false,
  ...overrides,
});

const baseGlobals = {
  window: { getComputedStyle: () => ({ visibility: "visible", display: "block" }) },
  document: {},
};

const { precheckRequired, elementPrecheck, coveringElement, isDisabledElement, isElementVisible } =
  makeFactory(baseGlobals);

// ---- precheckRequired：只有可操作动作要预检（读取类允许读隐藏元素） ----
for (const method of ["click", "hover", "input", "select", "check", "drag"]) {
  check(`可操作动作需预检（${method}）`, precheckRequired(method), true);
}
for (const method of ["getText", "getPosition", "queryAll", "getSelectOptions", "setValue"]) {
  check(`读取/非指针动作不预检（${method}）`, precheckRequired(method), false);
}
check("大小写与空白容忍（ CLICK ）", precheckRequired("  CLICK "), true);
check("缺省（undefined）不预检", precheckRequired(undefined), false);
check("空串不预检", precheckRequired(""), false);

// ---- elementPrecheck：通过 → null；失败 → { code, ... } ----
check("正常元素 → 通过", elementPrecheck(stubElement()), null);
check("已从文档移除 → NOT_FOUND", elementPrecheck(stubElement({ isConnected: false })).code, "ELEMENT_NOT_FOUND");
check("原生 disabled → DISABLED", elementPrecheck(stubElement({ disabled: true })).code, "ELEMENT_DISABLED");
check(
  "aria-disabled=true → DISABLED",
  elementPrecheck(stubElement({ getAttribute: (n) => (n === "aria-disabled" ? "true" : null) })).code,
  "ELEMENT_DISABLED",
);
check(
  "祖先 inert 子树 → DISABLED",
  elementPrecheck(stubElement({ closest: (sel) => (sel === "[inert]" ? {} : null) })).code,
  "ELEMENT_DISABLED",
);
check("零尺寸（无 client rects）→ NOT_VISIBLE", elementPrecheck(stubElement({ getClientRects: () => [] })).code, "ELEMENT_NOT_VISIBLE");
check(
  "零宽高 rect → NOT_VISIBLE",
  elementPrecheck(stubElement({ getClientRects: () => [{ width: 0, height: 0 }] })).code,
  "ELEMENT_NOT_VISIBLE",
);
check("null 元素 → NOT_FOUND", elementPrecheck(null).code, "ELEMENT_NOT_FOUND");

// display:none / visibility:hidden 用独立 globals 求值（getComputedStyle 由闭包捕获）
const hiddenFactory = makeFactory({
  window: { getComputedStyle: () => ({ visibility: "hidden", display: "block" }) },
  document: {},
});
check(
  "computedStyle visibility:hidden → NOT_VISIBLE",
  hiddenFactory.elementPrecheck(stubElement()).code,
  "ELEMENT_NOT_VISIBLE",
);
const displayNoneFactory = makeFactory({
  window: { getComputedStyle: () => ({ visibility: "visible", display: "none" }) },
  document: {},
});
check(
  "computedStyle display:none → NOT_VISIBLE",
  displayNoneFactory.elementPrecheck(stubElement()).code,
  "ELEMENT_NOT_VISIBLE",
);

// checkVisibility 存在且返回 false → NOT_VISIBLE（浏览器新 API 路径）
const cvFactory = makeFactory({
  window: { getComputedStyle: () => ({ visibility: "visible", display: "block" }) },
  document: {},
});
check(
  "checkVisibility()=false → NOT_VISIBLE",
  cvFactory.elementPrecheck(stubElement({ checkVisibility: () => false })).code,
  "ELEMENT_NOT_VISIBLE",
);
check(
  "checkVisibility()=true → 通过",
  cvFactory.elementPrecheck(stubElement({ checkVisibility: () => true })),
  null,
);

// ---- isDisabledElement：disabled / aria-disabled / inert 三态都要认 ----
check("普通元素不禁用", isDisabledElement(stubElement()), false);
check("disabled 属性", isDisabledElement(stubElement({ disabled: true })), true);
check(
  "aria-disabled 大小写容忍",
  isDisabledElement(stubElement({ getAttribute: () => "TRUE" })),
  true,
);
check("null 不禁用", isDisabledElement(null), false);

// ---- coveringElement：遮挡判定 ----
const viewportGlobals = {
  window: {
    getComputedStyle: () => ({ visibility: "visible", display: "block" }),
    innerWidth: 1000,
    innerHeight: 800,
  },
};
const self = stubElement();
const noGlobal = makeFactory({ ...viewportGlobals, document: {} });
check(
  "无 elementFromPoint 时不判遮挡",
  noGlobal.coveringElement(self, [{ width: 20, height: 10, left: 5, top: 5 }]),
  null,
);

const hitSelf = makeFactory({ ...viewportGlobals, document: { elementFromPoint: () => self } });
check(
  "命中自身 → 不遮挡",
  hitSelf.coveringElement(self, [{ width: 20, height: 10, left: 5, top: 5 }]),
  null,
);

const mask = { tagName: "DIV", id: "mask", className: "overlay modal", contains: () => false };
const hitMask = makeFactory({ ...viewportGlobals, document: { elementFromPoint: () => mask } });
const blocked = hitMask.coveringElement(self, [{ width: 20, height: 10, left: 5, top: 5 }]);
check("命中遮罩 → 报告遮挡者 id", blocked && blocked.id, "mask");
check("命中遮罩 → 报告遮挡者 tag", blocked && blocked.tag, "div");
check("命中遮罩 → className 原样带回", blocked && blocked.className, "overlay modal");

const offscreen = makeFactory({ ...viewportGlobals, document: { elementFromPoint: () => mask } });
check(
  "元素中心在视口外 → 不判遮挡（交给视口预检）",
  offscreen.coveringElement(self, [{ width: 20, height: 10, left: 5000, top: 5000 }]),
  null,
);
check(
  "祖先链包含关系 → 不误判（el.contains(top)）",
  hitSelf.coveringElement({ ...self, contains: () => true }, [{ width: 20, height: 10, left: 5, top: 5 }]),
  null,
);

// ---- M29：遮挡判定必须用**实际要点击的那个点**（默认仍是元素中心）----
let probed = null;
const probeFactory = makeFactory({
  ...viewportGlobals,
  document: {
    elementFromPoint: (x, y) => {
      probed = { x, y };
      return self;
    },
  },
});
probeFactory.coveringElement(self, [{ width: 200, height: 100, left: 0, top: 0 }]);
check("不传点 → 用元素中心探测", probed, { x: 100, y: 50 });
probeFactory.coveringElement(self, [{ width: 200, height: 100, left: 0, top: 0 }], { x: 12, y: 34 });
check("传点 → 用传入的点探测（随机点击点不能被中心判定掩盖）", probed, { x: 12, y: 34 });
probeFactory.coveringElement(self, [{ width: 200, height: 100, left: 0, top: 0 }], { x: NaN, y: 34 });
check("单个坐标非法 → 该轴退回元素中心（不拿 NaN 去探测）", probed, { x: 100, y: 34 });
const oddMask = { tagName: "DIV", id: "odd", className: "", contains: () => false };
const oddFactory = makeFactory({ ...viewportGlobals, document: { elementFromPoint: () => oddMask } });
check(
  "传入点在视口外 → 不判遮挡（交给视口预检）",
  oddFactory.coveringElement(self, [{ width: 200, height: 100, left: 0, top: 0 }], { x: 5000, y: 10 }),
  null,
);

// ---- 反漂移：预检必须在 domOp 里被真正调用，且失败要结构化回传 ----
if (!/elementPrecheck\(el\)/.test(source)) {
  failed += 1;
  console.error("FAIL | domOp 未调用 elementPrecheck：预检会再次被绕过（静默误点回归）");
} else {
  console.log("PASS | domOp 调用 elementPrecheck");
}
if (!/coveringElement\(el, el\.getClientRects\(\), actionPoint\)/.test(source)) {
  failed += 1;
  console.error("FAIL | domOp 未用实际点击点做遮挡判定：点到遮罩层会再次静默成功");
} else {
  console.log("PASS | domOp 用实际点击点做遮挡判定");
}
if (!/precheckRequired\(method\)/.test(source)) {
  failed += 1;
  console.error("FAIL | 预检未按 method 门控：读取类命令会被误伤");
} else {
  console.log("PASS | 预检按 method 门控");
}
if (!/precheck:\s*\{\s*code:\s*"ELEMENT_COVERED"/.test(source)) {
  failed += 1;
  console.error("FAIL | 遮挡失败未带结构化 precheck.code：错误码会退化成 EXECUTOR_FAILED");
} else {
  console.log("PASS | 遮挡失败结构化回传 ELEMENT_COVERED");
}
if (!/precheck:\s*\{\s*code:\s*"ELEMENT_NOT_VISIBLE"/.test(source)) {
  failed += 1;
  console.error("FAIL | 视口外失败未带结构化 precheck.code");
} else {
  console.log("PASS | 视口外失败结构化回传 ELEMENT_NOT_VISIBLE");
}
if (!/value\.precheck && value\.precheck\.code/.test(source)) {
  failed += 1;
  console.error("FAIL | runCommand 未把 precheck 翻成失败响应：会误报成功");
} else {
  console.log("PASS | runCommand 消费 precheck");
}
if (!/KNOWN_ERROR_CODES\.includes\(code\)/.test(source)) {
  failed += 1;
  console.error("FAIL | errorCode 未优先认结构化 code：预检错误码传不到执行器");
} else {
  console.log("PASS | errorCode 优先认结构化 code");
}

// ---- M32：可见性判定是**单一事实来源**，count/waitFor 与预检必须复用同一个 ----
// 背景：此前 `count` 用的 `isVisible` 比预检宽松（不查 opacity、不查视口），
// 于是 `waitFor(state=visible)` 说「等到了」，紧接着的点击报 `ELEMENT_NOT_VISIBLE`。
check("可见：正常元素", isElementVisible(stubElement()), true);
check("不可见：已脱离文档", isElementVisible(stubElement({ isConnected: false })), false);
check("不可见：null", isElementVisible(null), false);
check("不可见：无 client rects", isElementVisible(stubElement({ getClientRects: () => [] })), false);
check(
  "不可见：零尺寸 rect",
  isElementVisible(stubElement({ getClientRects: () => [{ width: 0, height: 10, left: 0, top: 0 }] })),
  false,
);
check(
  "不可见：visibility:hidden",
  makeFactory({
    window: { getComputedStyle: () => ({ visibility: "hidden", display: "block" }) },
    document: {},
  }).isElementVisible(stubElement()),
  false,
);
check(
  "不可见：display:none",
  makeFactory({
    window: { getComputedStyle: () => ({ visibility: "visible", display: "none" }) },
    document: {},
  }).isElementVisible(stubElement()),
  false,
);
check(
  "不可见：checkVisibility 说不可见（覆盖 opacity:0）",
  cvFactory.isElementVisible(stubElement({ checkVisibility: () => false })),
  false,
);
check(
  "可见：checkVisibility 说可见",
  cvFactory.isElementVisible(stubElement({ checkVisibility: () => true })),
  true,
);
check(
  "旧内核无 checkVisibility → 仍按 rect + style 判定（兜底不失效）",
  cvFactory.isElementVisible(stubElement({ checkVisibility: undefined })),
  true,
);

// 反漂移：`count` 必须复用 isElementVisible，不许再出现第二份可见性判定
if (!/list\.filter\(isElementVisible\)/.test(source)) {
  failed += 1;
  console.error("FAIL | count 未复用 isElementVisible：两套可见口径会重新分叉");
} else {
  console.log("PASS | count 复用 isElementVisible");
}
if (/\bisVisible\b/.test(source)) {
  failed += 1;
  console.error("FAIL | 仍存在旧的 isVisible 定义/引用：可见性判定有第二份实现");
} else {
  console.log("PASS | 无第二份可见性判定（isVisible 已彻底移除）");
}
if (!/selector:\s*"\[inert\]"/.test(source) && !source.includes('"[inert]"')) {
  failed += 1;
  console.error("FAIL | inert 判定锚点丢失");
} else {
  console.log("PASS | inert 子树判定仍在");
}

// ---- 反漂移：新增的预检错误码必须真实存在于枚举（跨语言一致性）----
const errorsPy = readFileSync(join(here, "..", "src", "rpa_core", "model", "errors.py"), "utf8");
for (const code of ["ELEMENT_COVERED", "ELEMENT_DISABLED", "ELEMENT_NOT_VISIBLE"]) {
  if (!errorsPy.includes(`${code} = "${code}"`)) {
    failed += 1;
    console.error(`FAIL | ErrorCode 枚举缺少 ${code}`);
  } else {
    console.log(`PASS | ErrorCode 枚举含 ${code}`);
  }
  if (!source.includes(`"${code}"`)) {
    failed += 1;
    console.error(`FAIL | background.js 未产出 ${code}`);
  } else {
    console.log(`PASS | background.js 产出 ${code}`);
  }
}

if (failed) {
  console.error(`\n执行前预检纯函数校验失败：${failed} 项`);
  process.exit(1);
}
console.log("\n执行前预检纯函数校验全部通过");
