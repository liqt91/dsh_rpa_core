// 校验活体校验通道（M47）的三段语义：
//   A. content.js 纯函数（verify-helpers 切片）：querySelectorAll 求值 + 无效选择器
//      + 闪烁上限——这段只有浏览器里有意义，Python 读源码只能证明「写了这行字」。
//   B. background.js runVerify 矩阵：只发活跃页 / 无脚本补注入重试 / 注入失败结构化
//      报错 / requestId 透传——「对哪一页发、失败怎么报」是调用矩阵，桩测接口形状
//      证明不了。
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
  `${pureSlice}\nreturn { VERIFY_FLASH_LIMIT, matchCountFor, verifyReplyFor };`,
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

const makeChrome = ({ tabs = [], injectFail = false, hasScript = true } = {}) => {
  const log = { sent: [], injected: [], posted: [] };
  let injectedOnce = false;
  const chrome = {
    tabs: {
      query: async (q) => tabs.filter((t) => (q && q.active ? !!t.active : true))
        .filter((t) => (q && q.lastFocusedWindow ? !!t.focused : true))
        .map((t) => ({ ...t })),
      sendMessage: async (tabId, msg) => {
        if (!hasScript && !injectedOnce) {
          throw new Error("Receiving end does not exist.");
        }
        log.sent.push({ tabId, msg });
        return { contentBuild: "page-build", count: 4 };
      },
      onUpdated: { addListener: () => {} },
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
  `${bgSlice}\nreturn { runVerify };`,
)(
  chrome,
  (payload) => { log.posted.push(payload); return true; },
);

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

// W1 活跃页直发：css 透传、结果按 requestId 回传并带构建标识与命中数
{
  const { chrome, log } = makeChrome({
    tabs: [
      { id: 5, url: "https://bg.test/", active: true },
      { id: 1, url: "https://front.test/", active: true, focused: true },
    ],
  });
  await buildBg(chrome, log).runVerify({ sessionId: "ver-1", requestId: "rq-1", css: "#kw" });
  await settle();
  check("W1 只发聚焦窗口的活跃页", log.sent.map((s) => s.tabId), [1]);
  check("W1 页面消息类型与 css", log.sent[0] && log.sent[0].msg,
    { type: "rpa-capture-verify", css: "#kw" });
  const reply = log.posted[0] || {};
  check("W1 回传类型/配对/构建/命中",
    [reply.type, reply.requestId, reply.extBuild, reply.count],
    ["capture_verify_result", "rq-1", extBuild, 4]);
}

// W2 活跃页无脚本：补注入后重试一次
{
  const { chrome, log } = makeChrome({
    tabs: [{ id: 7, url: "https://a.test/", active: true, focused: true }],
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
    tabs: [{ id: 3, url: "chrome://settings/", active: true, focused: true }],
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

// ---- C. content 接线断言 ----------------------------------------------------
check("C1 content 监听器带 sendResponse 形参（无回包 = host 永远等超时）",
  /onRuntimeMessage = \(msg, _sender, sendResponse\)/.test(content), true);
check("C1 rpa-capture-verify 分支存在",
  /msg\.type === "rpa-capture-verify"/.test(content), true);
check("C1 校验应答带 contentBuild（页面脚本新旧可对账）",
  /contentBuild: EXT_BUILD, \.\.\.runVerify/.test(content), true);
check("C1 闪烁自动清理（定时器兜底，不留黄框赖在页面）",
  /setTimeout\(clearVerifyFlash, 1600\)/.test(content), true);
check("C1 校验黄框 z-index 低于捕获红框（捕获态永远压在校验框上）",
  Number(content.match(/z-index:(\d+);pointer-events:none;"\s*\n\s*\+ "box-sizing:border-box;border:3px solid #d4a017/)
    ?. [1] ?? 0)
    < Number(content.match(/z-index:(\d+);pointer-events:none;"\s*\n\s*\+ `box-sizing:border-box/)
      ?. [1] ?? Infinity), true);

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
