// 校验活体校验通道（M47）的三段语义：
//   A. content.js 纯函数（verify-helpers 切片）：querySelectorAll 求值 + 无效选择器
//      + 闪烁上限——这段只有浏览器里有意义，Python 读源码只能证明「写了这行字」。
//   B. background.js runVerify 矩阵：只发活跃页 / 无脚本补注入重试 / 注入失败结构化
//      报错 / requestId 透传 / **取页口径**（真前台窗口优先，W6–W8）——「对哪一页发、
//      失败怎么报」是调用矩阵，桩测接口形状证明不了。
//   C. content 接线断言：verify 应答必须带 sendResponse（不带 = tab.sendMessage 永远
//      得不到回包，host 侧只能等超时）。
// 用法：node scripts/check_verify.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const content = readFileSync(join(here, "..", "extension", "content.js"), "utf8");
const background = readFileSync(join(here, "..", "extension", "background.js"), "utf8");

let failed = 0;
const check = (label, actual, expected) => {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(
    `${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → got ${JSON.stringify(actual)} want ${JSON.stringify(expected)}`}`,
  );
};

// ---- A. content.js 纯函数切片求值 ------------------------------------------
const pureStart = content.indexOf("// [verify-helpers:start]");
const pureEnd = content.indexOf("// [verify-helpers:end]");
if (pureStart < 0 || pureEnd <= pureStart) {
  console.error("FAIL: 未能定位 verify-helpers 切片（锚点缺失）");
  process.exit(1);
}
const pureSlice = content.slice(pureStart, pureEnd);
const helpers = new Function(
  `${pureSlice}\nreturn { VERIFY_FLASH_LIMIT, matchCountFor, verifyReplyFor, normalizeVerifyMode };`,
)();
const makeDoc = (matches, invalid = false) => ({
  querySelectorAll: (css) => {
    if (invalid) throw new Error(`'${css}' is not a valid selector`);
    return matches;
  },
});

const el = () => ({});
check("V1 命中 3 → count 3 / flash 3",
  verifyReply(makeDoc([el(), el(), el()]), "div.ok"), { count: 3, flash: 3 });
function verifyReply(doc, css) {
  return helpers.verifyReplyFor(doc, css);
}
check("V2 命中 0 → flash 0（不闪）",
  helpers.verifyReplyFor(makeDoc([]), "div.ok"), { count: 0, flash: 0 });
check("V3 无效选择器 → 结构化报错（不是异常）",
  helpers.verifyReplyFor(makeDoc([], true), "div["), { error: "invalid-selector" });
check("V4 闪烁上限封顶（25 命中只闪 20）",
  helpers.verifyReplyFor(makeDoc(Array.from({ length: 25 }, el)), "div"),
  { count: 25, flash: helpers.VERIFY_FLASH_LIMIT });

// ---- A2. mode 矩阵（M48：同一通道 flash/preview/clear 三语义） ---------------
check("M1 preview：只回 count（无 flash 键——驻留不该有闪烁上限的概念）",
  helpers.verifyReplyFor(makeDoc([el(), el(), el()]), "div.ok", "preview"),
  { count: 3 });
check("M2 preview 命中 0 也只回 count（清场责任在 runVerify，不在纯函数）",
  helpers.verifyReplyFor(makeDoc([]), "div.ok", "preview"),
  { count: 0 });
check("M3 clear：无讨论直接 {count:0}（css 可空，不查找）",
  helpers.verifyReplyFor(makeDoc([el(), el()]), "", "clear"),
  { count: 0 });
check("M4 未知 mode 归一为 flash（旧信封/坏值不得炸成异常）",
  helpers.verifyReplyFor(makeDoc([el()]), "div", "wat"),
  { count: 1, flash: 1 });
check("M5 normalizeVerifyMode 是守门入口（缺省信封也走它）",
  [helpers.normalizeVerifyMode(undefined), helpers.normalizeVerifyMode("preview")],
  ["flash", "preview"]);

// ---- B. background.js runVerify 矩阵 ---------------------------------------
const bgStart = background.indexOf("// ---------------------------------------------------------------- 捕获通道");
const bgEnd = background.indexOf("// ---------------------------------------------------------------- 执行通道");
if (bgStart < 0 || bgEnd <= bgStart) {
  console.error("FAIL: 未能定位 background 捕获通道区");
  process.exit(1);
}
const extBuild = background.match(/const EXT_BUILD = "([^"]+)"/)?.[1];
if (!extBuild) {
  console.error("FAIL: 未找到 EXT_BUILD 常量");
  process.exit(1);
}
const bgSlice = `let captureSessionId = null;\nconst EXT_BUILD = ${JSON.stringify(extBuild)};\n${background.slice(bgStart, bgEnd)}`;

const makeChrome = ({ tabs = [], windows = [], windowsFail = false, injectFail = false, hasScript = true } = {}) => {
  const log = { sent: [], injected: [], posted: [], queried: [] };
  let injectedOnce = false;
  const chrome = {
    tabs: {
      // QueryInfo 语义按需模拟：active / windowId / lastFocusedWindow 三个字段。
      // `focused` 落在**窗口**上（真 API 也是窗口级），故此桩把它读作窗口状态。
      query: async (q) => {
        log.queried.push({ ...(q || {}) });
        let out = tabs.filter((t) => (q && q.active ? !!t.active : true));
        if (q && q.windowId != null) out = out.filter((t) => t.windowId === q.windowId);
        if (q && q.lastFocusedWindow) out = out.filter((t) => !!t.focused);
        return out.map((t) => ({ ...t }));
      },
      sendMessage: async (tabId, msg) => {
        if (!hasScript && !injectedOnce) {
          throw new Error("Receiving end does not exist.");
        }
        log.sent.push({ tabId, msg });
        return { contentBuild: "page-build", count: 4 };
      },
      onUpdated: { addListener: () => {} },
    },
    windows: {
      getAll: async () => {
        if (windowsFail) throw new Error("windows API unavailable");
        return windows.map((w) => ({ ...w }));
      },
    },
    runtime: { onMessage: { addListener: () => {} } },
    scripting: {
      executeScript: async ({ target }) => {
        if (injectFail) throw new Error("No tab with id");
        injectedOnce = true;
        log.injected.push({ tabId: target.tabId });
        return [];
      },
    },
  };
  return { chrome, log };
};

const buildBg = (chrome, log) => new Function(
  "chrome", "post",
  `${bgSlice}\nreturn { runVerify, pickVerifyTab };`,
)(
  chrome,
  (payload) => { log.posted.push(payload); return true; },
);

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

// W1 活跃页直发：css 透传、结果按 requestId 回传并带构建标识与命中数
{
  const { chrome, log } = makeChrome({
    // 两个窗口各有一个活跃标签页，只有窗口 1 真在前台：必须发窗口 1 的活跃页。
    windows: [
      { id: 10, focused: false },
      { id: 11, focused: true },
    ],
    tabs: [
      { id: 5, url: "https://bg.test/", active: true, windowId: 10, focused: true },
      { id: 1, url: "https://front.test/", active: true, windowId: 11, focused: true },
    ],
  });
  await buildBg(chrome, log).runVerify({ sessionId: "ver-1", requestId: "rq-1", css: "#kw" });
  await settle();
  check("W1 只发聚焦窗口的活跃页", log.sent.map((s) => s.tabId), [1]);
  check("W1 页面消息类型与 css（缺省信封归一为 flash）",
    log.sent[0] && log.sent[0].msg,
    { type: "rpa-capture-verify", css: "#kw", mode: "flash" });
  const reply = log.posted[0] || {};
  check("W1 回传类型/配对/构建/命中",
    [reply.type, reply.requestId, reply.extBuild, reply.count],
    ["capture_verify_result", "rq-1", extBuild, 4]);
}

// W2 活跃页无脚本：补注入后重试一次
{
  const { chrome, log } = makeChrome({
    windows: [{ id: 20, focused: true }],
    tabs: [{ id: 7, url: "https://a.test/", active: true, windowId: 20, focused: true }],
    hasScript: false,
  });
  await buildBg(chrome, log).runVerify({ requestId: "rq-2", css: "#x" });
  await settle();
  check("W2 无脚本页补注入一次", log.injected, [{ tabId: 7 }]);
  check("W2 补注入后重发校验", log.sent.length, 1);
  check("W2 重试成功照常回传命中数", log.posted[0] && log.posted[0].count, 4);
}

// W3 受保护页（注入必失败）：结构化报错
{
  const { chrome, log } = makeChrome({
    windows: [{ id: 30, focused: true }],
    tabs: [{ id: 3, url: "chrome://settings/", active: true, windowId: 30, focused: true }],
    hasScript: false,
    injectFail: true,
  });
  await buildBg(chrome, log).runVerify({ requestId: "rq-3", css: "#x" });
  await settle();
  check("W3 注入失败 → inject-failed 结构化报错",
    log.posted[0] && log.posted[0].error, "inject-failed");
}

// W4 无活跃标签页：结构化报错
{
  const { chrome, log } = makeChrome({ tabs: [] });
  await buildBg(chrome, log).runVerify({ requestId: "rq-4", css: "#x" });
  await settle();
  check("W4 无活跃页 → no-active-tab", log.posted[0] && log.posted[0].error, "no-active-tab");
}

// W5 mode 透传（M48）：host 信封的 preview 必须原样到达 content script——
// 通道语义收口在 content，background 只当邮差；漏传 = 预览永远表现为闪烁。
{
  const { chrome, log } = makeChrome({
    windows: [{ id: 40, focused: true }],
    tabs: [{ id: 9, url: "https://p.test/", active: true, windowId: 40, focused: true }],
  });
  await buildBg(chrome, log).runVerify({ requestId: "rq-5", css: "#x", mode: "preview" });
  await settle();
  check("W5 preview mode 原样透传 content", log.sent[0] && log.sent[0].msg.mode, "preview");
}

// ---- W6–W8 取页口径（2026-10-08 真机实锤：计数抖动其实是「打到了哪一页」在抖）----
// 真机 trace：同一条 css 的回复一拨来自 explore 页（count 0）、一拨来自 search_result_ai
// 页（count 1）。根因是旧实现只用 lastFocusedWindow —— 它是**窗口**级的「最近聚焦」记忆，
// Chrome 整体失去前台时不会失效，多窗口下会来回指。

// W6 真前台窗口优先于「最近聚焦」（本次修复的核心回归钉）：
// 用户眼前是窗口 51，但 lastFocusedWindow 记忆指向窗口 50（GUI 对话框抢过焦点）
// ——必须发 51 的活跃页，绝不能发 50 的（那正是真机上数到 0 的那一页）。
{
  const { chrome, log } = makeChrome({
    windows: [
      { id: 50, focused: false },
      { id: 51, focused: true },
    ],
    tabs: [
      // 50 是「最近聚焦」记忆所指（focused:true 供 lastFocusedWindow 兜底链读），
      // 但它不在前台；51 才是前台窗口。
      { id: 500, url: "https://stale.test/", active: true, windowId: 50, focused: true },
      { id: 510, url: "https://visible.test/", active: true, windowId: 51, focused: false },
    ],
  });
  await buildBg(chrome, log).runVerify({ requestId: "rq-6", css: "#kw" });
  await settle();
  check("W6 真前台窗口的活跃页胜过「最近聚焦」记忆（本次 bug 的回归钉）",
    log.sent.map((s) => s.tabId), [510]);
  check("W6 未退化成按 lastFocusedWindow 取页（发给 500 就是旧 bug 复现）",
    log.sent.map((s) => s.tabId).includes(500), false);
}

// W7 Chrome 整体不在前台（无 focused 窗口）：回退到 lastFocusedWindow 的旧行为——
// 修复不得把「Chrome 不在前台」变成 no-active-tab（那是把稳定性换成了永远失败）。
{
  const { chrome, log } = makeChrome({
    windows: [
      { id: 60, focused: false },
      { id: 61, focused: false },
    ],
    tabs: [
      { id: 600, url: "https://a.test/", active: true, windowId: 60, focused: false },
      { id: 610, url: "https://last.test/", active: true, windowId: 61, focused: true },
    ],
  });
  await buildBg(chrome, log).runVerify({ requestId: "rq-7", css: "#kw" });
  await settle();
  check("W7 无前台窗口 → 回退最近聚焦窗口的活跃页", log.sent.map((s) => s.tabId), [610]);
  check("W7 回退链不得报 no-active-tab", log.posted[0] && log.posted[0].error, undefined);
}

// W8 windows API 不可用：取页失败不得变成校验失败，仍走兜底链
{
  const { chrome, log } = makeChrome({
    windowsFail: true,
    tabs: [{ id: 800, url: "https://x.test/", active: true, focused: true }],
  });
  await buildBg(chrome, log).runVerify({ requestId: "rq-8", css: "#kw" });
  await settle();
  check("W8 windows API 抛错仍能取到页（不进 no-active-tab）", log.sent.map((s) => s.tabId), [800]);
  check("W8 windows API 抛错照常回传命中数", log.posted[0] && log.posted[0].count, 4);
}

// W9 取页辅助函数可独立求值（切片锚点存在 = 「改了 runVerify 但忘了改取页」会被拦下）
{
  const { chrome } = makeChrome({ tabs: [] });
  const bg = buildBg(chrome, { posted: [] });
  check("W9 pickVerifyTab 从切片导出（纯函数可测）", typeof bg.pickVerifyTab, "function");
}

// ---- C. content 接线断言 ----------------------------------------------------
check("C1 content 监听器带 sendResponse 形参（无回包 = host 永远等超时）",
  /onRuntimeMessage = \(msg, _sender, sendResponse\)/.test(content), true);
check("C1 rpa-capture-verify 分支存在",
  /msg\.type === "rpa-capture-verify"/.test(content), true);
check("C1 校验应答带 contentBuild（页面脚本新旧可对账）",
  /contentBuild: EXT_BUILD,\s*\n\s*\.\.\.runVerify/.test(content), true);
check("C1 闪烁自动清理（定时器兜底，不留黄框赖在页面）",
  /setTimeout\(clearVerifyFlash, 1600\)/.test(content), true);
check("C2 闪烁定时器由 persist 短路（preview 驻留 = 不排程自动清理）",
  /if \(!persist\) setTimeout\(clearVerifyFlash, 1600\);/.test(content), true);
check("C2 runVerify 收 mode 参数并归一（语义收口在 content）",
  /runVerify = \(css, mode\) => \{\s*\n\s*mode = normalizeVerifyMode\(mode\);/.test(content), true);
check("C2 preview 恒先清场（count=0 也要清——旧框冒充命中是预览最危险的误导）",
  /if \(mode === "preview"\) clearVerifyFlash\(\);/.test(content), true);
check("C2 应答透传 msg.mode（host 侧/诊断要能知道这轮是什么语义）",
  /runVerify\(String\(msg\.css \|\| ""\), msg\.mode\)/.test(content), true);
check("C2 进捕获态先清预览框（捕获红框不能压着上一轮的黄框）",
  /if \(armed\) \{(?:\s*\n\s*\/\/[^\n]*)*\s*\n\s*clearVerifyFlash\(\);/.test(content), true);
check("C3 校验黄框用 absolute+文档坐标（fixed 钉在视口上，滚动不跟随）",
  /position:absolute;z-index:2147483646/.test(content)
    && /r\.left \+ sx - 2/.test(content)
    && /r\.top \+ sy - 2/.test(content), true);
check("C3 黄框坐标带滚动偏移兜底（scrollX/pageXOffset 双路取）",
  /window\.scrollX \|\| window\.pageXOffset \|\| 0/.test(content)
    && /window\.scrollY \|\| window\.pageYOffset \|\| 0/.test(content), true);
check("C1 校验黄框 z-index 低于捕获红框（捕获态永远压在校验框上）",
  Number(content.match(/z-index:(\d+);pointer-events:none;"\s*\n\s*\+ "box-sizing:border-box;border:3px solid #d4a017/)
    ?. [1] ?? 0)
    < Number(content.match(/z-index:(\d+);pointer-events:none;"\s*\n\s*\+ `box-sizing:border-box/)
      ?. [1] ?? Infinity), true);

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
