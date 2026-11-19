// 校验「网页元素缩略图」那条**新增的跨端通道**（M102 S2b）。
//
// 现场（2026-11-19）：桌面元素入库后有截图，网页元素没有。链路上每一段单看都「没问题」，
// 因为是三个各错一点叠出来的：
//   ① content 从没要过视口图 → host 只有窗口截图，而窗口截图在 Edge 开侧栏时算不出位置；
//   ② background 转发 capture-result 时只挑 descriptor/contentBuild 两个字段，图被丢掉；
//   ③ host 拿到图也不敢认——同一字段有「窗口图 / 视口图」两种几何口径。
// 三处都不在 Python 契约测试的射程里（那边测的是「拿到这份数据能不能算对」），
// 所以这里真跑一遍 JS：content 的取图函数 + background 的接线 + 两端 type 常量对账。
//
// 用法：node scripts/check_capture_shot.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const contentSrc = readFileSync(join(here, "..", "extension", "content.js"), "utf8");
const backgroundSrc = readFileSync(join(here, "..", "extension", "background.js"), "utf8");

try {
  new Function(contentSrc);
  new Function(backgroundSrc);
} catch (err) {
  console.error(`FAIL: 扩展脚本语法错误 → ${err.message}`);
  process.exit(1);
}

let failed = 0;
const check = (label, actual, expected) => {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(
    `${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → got ${JSON.stringify(actual)} want ${JSON.stringify(expected)}`}`,
  );
};

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

// ---------------------------------------------------------------- content：取图
// requestVisibleShot 是纯的（只依赖 chrome.runtime），按锚点切出来真跑四种回包。
const cStart = contentSrc.indexOf("const SHOT_REQUEST_TIMEOUT_MS");
const cEnd = contentSrc.indexOf("// 真正落地一次捕获");
if (cStart < 0 || cEnd <= cStart) {
  console.error("FAIL: 未能定位 content.js 的 requestVisibleShot（锚点：const SHOT_REQUEST_TIMEOUT_MS / 真正落地一次捕获）");
  process.exit(1);
}
const requestVisibleShotSrc = contentSrc.slice(cStart, cEnd).trim();

const makeRequest = (sendMessage) => {
  const chrome = { runtime: { sendMessage, lastError: undefined } };
  return new Function("chrome", `${requestVisibleShotSrc}; return requestVisibleShot;`)(chrome);
};

// 1. 收到图 ⇒ 拿到那个 dataUrl（**必须是 background 回的那个字段**，不是别的名字）
{
  let seen = null;
  const fn = makeRequest((msg, cb) => {
    seen = msg;
    cb({ ok: true, dataUrl: "data:image/png;base64,AAA" });
  });
  check("S1 取图：正常回包拿到 dataUrl", await fn(), "data:image/png;base64,AAA");
  check("S1 取图：请求类型与 background 监听的一致", seen && seen.type, "rpa-shot-request");
}

// 2. background 说没图（无权限/页面不可见/配额）⇒ null，不能编
{
  const fn = makeRequest((_msg, cb) => cb({ ok: false }));
  check("S2 取图：回包 ok=false ⇒ null", await fn(), null);
}

// 3. 扩展刚重载：sendMessage 直接抛 ⇒ null（捕获不能因此失败）
{
  const fn = makeRequest(() => { throw new Error("Extension context invalidated."); });
  check("S3 取图：通道异常 ⇒ null", await fn(), null);
}

// 4. 无接收端：Chrome 走 lastError 而不是抛 ⇒ null，且**必须读 lastError**
//    （不读会抛 "Unchecked runtime.lastError" 并污染整次捕获的控制台）
{
  const fn = makeRequest((_msg, cb) => {
    // 真实 Chrome 在回调触发时把 runtime.lastError 置位
    cb(undefined);
  });
  check("S4 取图：回调空回包 ⇒ null", await fn(), null);
}

// 5. 回包里 dataUrl 不是字符串（background 降级成 {ok:true}）⇒ null
{
  const fn = makeRequest((_msg, cb) => cb({ ok: true }));
  check("S5 取图：ok 但没有 dataUrl ⇒ null", await fn(), null);
}

// ---------------------------------------------------------------- content：发送接线
// capture 体太大（依赖 dom/armed 一堆状态），只钉两件会静默坏的事：
// ① 先收覆盖层再截图（否则红框/hint 条被拍进缩略图）；② 图真的被带上去。
const capStart = contentSrc.indexOf("const capture = async (e) =>");
const capEnd = contentSrc.indexOf("const onClick = (e) =>");
if (capStart < 0 || capEnd <= capStart) {
  console.error("FAIL: 未能定位 content.js 的 capture（锚点：const capture = async / const onClick）");
  process.exit(1);
}
const captureBody = contentSrc.slice(capStart, capEnd);
check("S6 捕获流程是 async（要 await 截图）", captureBody.startsWith("const capture = async"), true);
check(
  "S7 先收覆盖层、后截图（否则把红框拍进缩略图）",
  /hideOverlay\(\);\s*\n\s*(?:\/\/[^\n]*\n\s*)*const viewportShot = await requestVisibleShot\(\)/.test(captureBody),
  true,
);
check(
  "S8 捕获结果里带上视口图",
  /viewportShot:\s*viewportShot\s*\|\|\s*undefined/.test(captureBody),
  true,
);

// ---------------------------------------------------------------- background：接线
// 切出「视口截图 + onMessage 监听」整段，注入 chrome/sendCapture 替身真跑。
const bStart = backgroundSrc.indexOf("// ---------------------------------------------------------------- 视口截图");
const bEnd = backgroundSrc.indexOf("// ---------------------------------------------------------------- 执行通道");
if (bStart < 0 || bEnd <= bStart) {
  console.error("FAIL: 未能定位 background.js 的视口截图区（锚点：视口截图 / 执行通道 两行分节注释）");
  process.exit(1);
}
const bgSlice = backgroundSrc.slice(bStart, bEnd);

const makeBackground = ({ shot, throwOnShot = false } = {}) => {
  const hooks = { responses: [], captured: [] };
  const chrome = {
    tabs: {
      captureVisibleTab: async () => {
        hooks.captured.push(true);
        if (throwOnShot) throw new Error("Either the '<all_urls>' or 'activeTab' permission is required.");
        return shot;
      },
    },
    windows: { WINDOW_ID_CURRENT: -2 },
    runtime: {
      onMessage: {
        addListener: (fn) => { hooks.listener = fn; },
      },
    },
  };
  const sendCapture = (payload) => { hooks.captured.push(payload); };
  new Function("chrome", "sendCapture", "isCaptureArmed", `${bgSlice}`)(
    chrome,
    sendCapture,
    async () => true,
  );
  return hooks;
};

// 6. background 的 onMessage 接线：capture-result 里的 viewportShot 必须原样交给 host
{
  const hooks = makeBackground({ shot: "data:image/png;base64,AAA" });
  hooks.listener(
    { type: "rpa-capture-result", descriptor: { kind: "browser" }, viewportShot: "data:image/jpeg;base64,BBB" },
    {},
    () => {},
  );
  await settle();
  const forwarded = hooks.captured.find((x) => x && x.descriptor);
  check("S9 capture-result 透传 viewportShot 给 host", forwarded && forwarded.viewportShot,
    "data:image/jpeg;base64,BBB");
  check("S9 透传时 descriptor 未被改动", forwarded && forwarded.descriptor, { kind: "browser" });
}

// 7. rpa-shot-request ⇒ 真去截视口图并回包
{
  const hooks = makeBackground({ shot: "data:image/png;base64,CCC" });
  const replies = [];
  hooks.listener({ type: "rpa-shot-request" }, {}, (reply) => replies.push(reply));
  await settle();
  check("S10 截图请求回了 dataUrl", replies.length === 1 && replies[0].ok, true);
  check("S10 回包带的就是那张图", replies[0] && replies[0].dataUrl, "data:image/png;base64,CCC");
  check("S10 没有跳过截图（真调了 captureVisibleTab）", hooks.captured[0], true);
}

// 8. 截图失败（无权限/页面不可见）⇒ 回 ok:false，捕获本身继续
{
  const hooks = makeBackground({ throwOnShot: true });
  const replies = [];
  hooks.listener({ type: "rpa-shot-request" }, {}, (reply) => replies.push(reply));
  await settle();
  check("S11 截图抛错 ⇒ 回 ok:false", replies.length === 1 && replies[0].ok, false);
}

// 9. 两端常量对账：content 发的 type == background 认的 type
{
  const sent = contentSrc.match(/type:\s*"([a-z-]*shot[a-z-]*)"/g) || [];
  const handled = backgroundSrc.match(/msg\.type\s*===\s*"([a-z-]*shot[a-z-]*)"/g) || [];
  check("S12 content 只发一个取图类型", sent.length, 1);
  check("S12 background 认同一个类型", handled.length, 1);
  check(
    "S12 两端字符串一致",
    sent[0].replace(/.*"([a-z-]+)".*/, "$1"),
    handled[0].replace(/.*"([a-z-]+)".*/, "$1"),
  );
}

// 10. background 收到却不回（service worker 正在重启 / 消息通道半开）⇒ 超时兜底。
//     没有它，这次捕获会永远停在 await 上，表现是「点了元素，界面一直转」。
{
  const fn = makeRequest(() => { /* 故意不回包 */ });
  const started = Date.now();
  const value = await fn();
  const waited = Date.now() - started;
  check("S13 取图：回包迟迟不来 ⇒ 超时给 null（捕获不能挂死）", value, null);
  check("S13 取图：超时在 1 秒内触发", waited >= 700 && waited < 2000, true);
}

if (failed) {
  console.error(`\n${failed} 条判据未通过`);
  process.exit(1);
}console.log("\nM102 S2b CAPTURE SHOT CHANNEL PASSED");
