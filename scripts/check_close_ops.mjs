// 校验扩展侧「浏览器收尾」op 的语义契约（M32 S1）：tabs.closeMany / tabs.listWindows。
//
// 为什么需要门禁而不是只靠 Python 侧的契约测试：Python 侧打桩的是**接口形状**
// （发不发一条 closeMany、参数怎么拼），它证明不了扩展里那段实现真的「逐个记账、
// 失败不吞」——而那正是这条命令的价值所在。一个天真的实现（`await Promise.all(...)`
// 然后无条件返回全部 tabId 为已关闭）能通过所有 Python 测试，却会在真机上把
// 「3 个标签关了 2 个」报成「3 个全关了」。
//
// 做法与其它 check_*_helpers.mjs 一致：按锚点从 background.js 切出可求值的片段，
// 注入最小 chrome 替身跑矩阵断言；另加反漂移正则断言。
// 用法：node scripts/check_close_ops.mjs   （退出码非 0 = 有失败）
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

let failed = 0;
const check = (label, actual, expected) => {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failed += 1;
    console.error(
      `FAIL | ${label}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`,
    );
  } else {
    console.log(`PASS | ${label}`);
  }
};
const checkTrue = (label, condition) => {
  if (!condition) {
    failed += 1;
    console.error(`FAIL | ${label}`);
  } else {
    console.log(`PASS | ${label}`);
  }
};

// ---- 锚点切片：把 executeCommand 整段拿出来求值（含 tabs.* 各 case）----
const START = "async function executeCommand(cmd) {";
const END = "\n}\n"; // executeCommand 的收尾（本文件后续再无同名片段）
const start = source.indexOf(START);
if (start < 0) {
  console.error(`FAIL: 未能定位 executeCommand（锚点 ${START}）`);
  process.exit(1);
}
// 用大括号配对找函数结束，避免正则误伤
let depth = 0;
let end = -1;
for (let i = source.indexOf("{", start); i < source.length; i += 1) {
  if (source[i] === "{") depth += 1;
  else if (source[i] === "}") {
    depth -= 1;
    if (depth === 0) {
      end = i + 1;
      break;
    }
  }
}
if (end < 0) {
  console.error("FAIL: executeCommand 大括号不配对");
  process.exit(1);
}
const slice = source.slice(start, end);

// ---- chrome 替身：只实现 closeMany / listWindows 用到的那几项 ----
const makeChrome = ({
  tabs = [],
  windows = [],
  failRemoveOn = [],
  failInjectOn = [],
} = {}) => {
  const removed = [];
  const injected = [];
  const log = []; // 统一事件序（inject:N / remove:N），用于断言「先注入后关闭」
  return {
    removed,
    injected,
    log,
    tabs: {
      async query(filter) {
        if (filter && filter.windowId != null) {
          return tabs.filter((tab) => tab.windowId === filter.windowId);
        }
        return tabs;
      },
      async remove(tabId) {
        if (failRemoveOn.includes(tabId)) throw new Error("no tab with id");
        log.push(`remove:${tabId}`);
        removed.push(tabId);
      },
    },
    windows: {
      async getCurrent() {
        return windows.find((win) => win.focused) || windows[0] || null;
      },
      async getAll() {
        return windows;
      },
    },
    scripting: {
      // M37：closeMany 的 beforeunload 抑制注入替身（stopLoading 不在本门禁矩阵内）
      async executeScript(options) {
        log.push(`inject:${options.target.tabId}`);
        injected.push(options.target.tabId);
        if (failInjectOn.includes(options.target.tabId)) throw new Error("cannot inject");
        return [{ result: true }];
      },
    },
  };
};

const makeRunner = (chrome) =>
  new Function(
    "chrome",
    `${slice}\nreturn executeCommand;`,
  )(chrome);

// ---- tabs.closeMany：显式 tabIds ----
{
  const chrome = makeChrome();
  const result = await makeRunner(chrome)({ op: "tabs.closeMany", args: { tabIds: [1, 2, 3] } });
  check("closeMany 显式 tabIds：逐个 remove", chrome.removed, [1, 2, 3]);
  check("closeMany 显式 tabIds：closedTabIds 如实", result.closedTabIds, [1, 2, 3]);
  check("closeMany 显式 tabIds：failedTabIds 为空", result.failedTabIds, []);
}

// ---- tabs.closeMany：部分失败必须**分账**（这是本 op 的核心价值）----
{
  const chrome = makeChrome({ failRemoveOn: [2] });
  const result = await makeRunner(chrome)({ op: "tabs.closeMany", args: { tabIds: [1, 2, 3] } });
  check("closeMany 部分失败：成功的进 closedTabIds", result.closedTabIds, [1, 3]);
  check("closeMany 部分失败：失败的进 failedTabIds", result.failedTabIds, [2]);
  checkTrue(
    "closeMany 部分失败：不把失败项也算成已关闭",
    !result.closedTabIds.includes(2),
  );
}

// ---- tabs.closeMany：all=true 只关当前窗口 ----
{
  const chrome = makeChrome({
    tabs: [
      { id: 11, windowId: 1 },
      { id: 12, windowId: 1 },
      { id: 99, windowId: 2 },
    ],
    windows: [{ id: 1, focused: true }],
  });
  const result = await makeRunner(chrome)({ op: "tabs.closeMany", args: { all: true } });
  check("closeMany all：只关当前窗口的标签", result.closedTabIds, [11, 12]);
  checkTrue("closeMany all：不动其它窗口", !chrome.removed.includes(99));
}

// ---- tabs.closeMany：显式 windowId 优先于「当前窗口」----
{
  const chrome = makeChrome({
    tabs: [
      { id: 11, windowId: 1 },
      { id: 99, windowId: 2 },
    ],
    windows: [{ id: 1, focused: true }],
  });
  const result = await makeRunner(chrome)({
    op: "tabs.closeMany",
    args: { all: true, windowId: 2 },
  });
  check("closeMany all + windowId：按指定窗口取标签", result.closedTabIds, [99]);
}

// ---- tabs.closeMany：两边都不给 → 关 0 个（不猜「全关」）----
{
  const chrome = makeChrome({ tabs: [{ id: 1, windowId: 1 }] });
  const result = await makeRunner(chrome)({ op: "tabs.closeMany", args: {} });
  check("closeMany 无参数：不关任何标签", result.closedTabIds, []);
  checkTrue("closeMany 无参数：一个 remove 都不发", chrome.removed.length === 0);
}

// ---- tabs.closeMany：空 tabIds 数组不能落到 all 分支 ----
// 这一条钉的是一个真实可发生的输入：Python 侧把「显式给了 tabIds 但列表为空」
// 挡在了参数校验（bool(tabIds) == close_all → INVALID_INPUT），但扩展作为**独立
// 进程边界**也必须自洽——它可能被别的客户端（直接调通道、手工构造信封）喂到
// `{tabIds: []}`，此时绝不能因为 `[]` 是 falsy 就退化成「关掉当前窗口全部标签」。
{
  const chrome = makeChrome({
    tabs: [
      { id: 11, windowId: 1 },
      { id: 12, windowId: 1 },
    ],
    windows: [{ id: 1, focused: true }],
  });
  const result = await makeRunner(chrome)({ op: "tabs.closeMany", args: { tabIds: [] } });
  check("closeMany 空 tabIds：不关任何标签", result.closedTabIds, []);
  checkTrue("closeMany 空 tabIds：不退化成「关当前窗口全部」", chrome.removed.length === 0);
}

// ---- tabs.closeMany：all 必须严格 === true ----
{
  const chrome = makeChrome({
    tabs: [
      { id: 11, windowId: 1 },
      { id: 12, windowId: 1 },
    ],
    windows: [{ id: 1, focused: true }],
  });
  const result = await makeRunner(chrome)({ op: "tabs.closeMany", args: { all: "yes" } });
  check("closeMany all='yes'（真值但非 true）：不关任何标签", result.closedTabIds, []);
  checkTrue("closeMany all 非布尔真值：一个 remove 都不发", chrome.removed.length === 0);
}

// ---- tabs.closeMany：all=false 不等于 all=true ----
{
  const chrome = makeChrome({ tabs: [{ id: 1, windowId: 1 }] });
  const result = await makeRunner(chrome)({ op: "tabs.closeMany", args: { all: false } });
  check("closeMany all=false：不关任何标签（必须严格 === true）", result.closedTabIds, []);
}

// ---- M37：ignoreBeforeUnload 默认 true —— 每个目标页先注入抑制脚本再 remove ----
{
  const chrome = makeChrome();
  const result = await makeRunner(chrome)({ op: "tabs.closeMany", args: { tabIds: [1, 2] } });
  checkTrue(
    "closeMany M37：默认对每个目标页先注入再逐个关闭（事件序）",
    JSON.stringify(chrome.log) ===
      JSON.stringify(["inject:1", "remove:1", "inject:2", "remove:2"]),
  );
  check("closeMany M37：注入不改变逐个记账", [result.closedTabIds, result.failedTabIds], [[1, 2], []]);
}

// ---- M37：ignoreBeforeUnload=false —— 完全不注入（保留页面拦截）----
{
  const chrome = makeChrome();
  const result = await makeRunner(chrome)({
    op: "tabs.closeMany",
    args: { tabIds: [1], ignoreBeforeUnload: false },
  });
  checkTrue("closeMany M37：显式 false 时不注入", chrome.injected.length === 0);
  check("closeMany M37：false 只跳过注入，关闭照常", [result.closedTabIds, chrome.removed], [[1], [1]]);
}

// ---- M37：注入失败是 best-effort —— 不阻断 remove，记账如实 ----
{
  const chrome = makeChrome({ failInjectOn: [1] });
  const result = await makeRunner(chrome)({ op: "tabs.closeMany", args: { tabIds: [1] } });
  checkTrue("closeMany M37：注入失败仍尝试 remove", chrome.removed.includes(1));
  check("closeMany M37：注入失败不影响记账", [result.closedTabIds, result.failedTabIds], [[1], []]);
}

// ---- tabs.listWindows ----
{
  const chrome = makeChrome({
    windows: [
      { id: 1, focused: true, incognito: false, type: "normal", tabs: [{}, {}] },
      { id: 2, focused: false, incognito: true, type: "normal", tabs: [{}] },
    ],
  });
  const result = await makeRunner(chrome)({ op: "tabs.listWindows", args: {} });
  check("listWindows：窗口数与 id", result.windows.map((win) => win.windowId), [1, 2]);
  check("listWindows：focused 如实", result.windows.map((win) => win.focused), [true, false]);
  check("listWindows：incognito 如实", result.windows.map((win) => win.incognito), [false, true]);
  check("listWindows：tabCount", result.windows.map((win) => win.tabCount), [2, 1]);
  checkTrue(
    "listWindows 不泄露页面内容（只报 id/状态）",
    !JSON.stringify(result).includes("http"),
  );
}

// ---- 反漂移：closeMany 必须逐个 try/catch 记账，不许 Promise.all 一把梭 ----
checkTrue(
  "closeMany 逐个 remove 并分别记账（存在 closedTabIds/failedTabIds 两个数组）",
  /closedTabIds\.push\(tabId\)/.test(source) && /failedTabIds\.push\(tabId\)/.test(source),
);

// ---- 反漂移（M37）：抑制注入必须是 MAIN world + 不等页面加载 + 缺省视为 true ----
// 注意前两条必须**切进 closeMany case 的作用域**再测：background.js 里 page.call/
// page.eval 的注入也用 world: "MAIN"，全文件正则会被它们喂出假绿灯（M37 负向验证
// 实测：删掉 closeMany 的 world 行后全文件正则照样绿）。以相邻 case 名为界切片。
const closeManyStart = source.indexOf('case "tabs.closeMany"');
const closeManyEnd = source.indexOf('case "tabs.listWindows"', closeManyStart);
const closeManySlice = closeManyStart >= 0 && closeManyEnd > closeManyStart
  ? source.slice(closeManyStart, closeManyEnd)
  : "";
checkTrue(
  "closeMany M37：beforeunload 抑制走 MAIN world 注入且不等加载完成",
  /world: "MAIN"/.test(closeManySlice) && /injectImmediately: true/.test(closeManySlice),
);
checkTrue(
  "closeMany M37：ignoreBeforeUnload 缺省视为 true（!== false 语义）",
  /ignoreBeforeUnload !== false/.test(source),
);

// ---- 跨语言一致性：op 名与 Python 侧封装必须一致 ----
const extPy = readFileSync(
  join(here, "..", "src", "rpa_core", "executors", "browser_ext.py"),
  "utf8",
);
for (const op of ["tabs.closeMany", "tabs.listWindows"]) {
  checkTrue(`扩展实现含 op ${op}`, source.includes(`case "${op}"`));
  checkTrue(`Python 封装转发 op ${op}`, extPy.includes(`"${op}"`));
}

// ---- 跨语言一致性：manifest 的 errors 与 Python 侧返回点对齐（M30 S3 口径）----
const manifest = JSON.parse(
  readFileSync(join(here, "..", "commands", "browser", "closeTabs.json"), "utf8"),
);
checkTrue("closeTabs manifest 声明 INVALID_INPUT", manifest.errors.includes("INVALID_INPUT"));
checkTrue(
  "closeTabs manifest 声明 tabIds/all 二选一约束",
  Array.isArray(manifest.input_schema.oneOf) && manifest.input_schema.oneOf.length === 2,
);
checkTrue(
  "closeTabs manifest 声明 ignoreBeforeUnload 默认 true",
  Boolean(manifest.input_schema.properties.ignoreBeforeUnload) &&
    manifest.input_schema.properties.ignoreBeforeUnload.default === true,
);

if (failed) {
  console.error(`\n浏览器收尾 op 校验失败：${failed} 项`);
  process.exit(1);
}
console.log("\n浏览器收尾 op 校验全部通过");
