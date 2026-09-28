// 校验 background.js 捕获通道的**可达性语义**（M42）：已打开标签页补注入 + 撤防兜底。
//
// 为什么需要门禁而不是只靠 Python 契约测试：这里的三条语义**只有扩展侧能证明**——
// ① 「已打开的标签页拿不到 arm」是 Chrome 声明式注入的固有行为，Python 侧根本看不见；
// ② 「补注入失败不阻断、撤防不补注入、活页面不白注入」是**调用矩阵**，桩测接口形状
//    只能证明「发了一条消息」，证明不了「对哪些页面发、发了几次」；
// ③ 「先落盘再广播」与「host 断开必须撤防」是**顺序**与**接线**，写反了照样能通过
//    所有接口形状断言，却在真机上表现为「已打开的网页上不生效 / 红框永远清不掉」。
//
// 做法与 check_close_ops.mjs 一致：按锚点切出可求值片段 + 注入最小 chrome 替身跑矩阵。
// 用法：node scripts/check_capture_broadcast.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "extension", "background.js"), "utf8");
const manifest = JSON.parse(
  readFileSync(join(here, "..", "extension", "manifest.json"), "utf8"),
);

try {
  new Function(source);
} catch (err) {
  console.error(`FAIL: background.js 语法错误 → ${err.message}`);
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

// ---- 锚点切片：捕获通道整段（armTab → sendCapture + 两处监听器接线）----
const START = "// ---------------------------------------------------------------- 捕获通道";
const END = "// ---------------------------------------------------------------- 执行通道";
const start = source.indexOf(START);
const end = source.indexOf(END);
if (start < 0 || end <= start) {
  console.error("FAIL: 未能定位捕获通道区（锚点：捕获通道 / 执行通道 两行分节注释）");
  process.exit(1);
}
// `captureSessionId` 在文件顶部声明、本区里读且写：补一份同名的区内声明，
// 让切片可独立求值（Function 作用域里不冲突），并用 getter 观察它。
const slice = `let captureSessionId = null;\n${source.slice(start, end)}`;

// ---- chrome 替身 -----------------------------------------------------------
// 送达模型贴近真机：`sendMessage` 只在页面里**有可用脚本**时才成功
// （无脚本 / 僵尸脚本都报 "Receiving end does not exist"），补注入才会让页面变得可送达。
const makeChrome = ({ tabs = [], injectFailOn = [] } = {}) => {
  const hasScript = new Map(tabs.map((t) => [t.id, !!t.hasScript]));
  const log = { injected: [], sent: [], received: [], queryCount: 0 };
  const chrome = {
    tabs: {
      query: async () => { log.queryCount += 1; return tabs.map((t) => ({ ...t })); },
      sendMessage: async (tabId, msg) => {
        if (!hasScript.get(tabId)) {
          throw new Error("Could not establish connection. Receiving end does not exist.");
        }
        log.sent.push({ tabId, msg });
        return { ok: true };
      },
      onUpdated: { addListener: () => {} },
    },
    runtime: { onMessage: { addListener: () => {} } },
    scripting: {
      executeScript: async ({ target, files }) => {
        if (injectFailOn.includes(target.tabId)) throw new Error("No tab with id");
        log.injected.push({ tabId: target.tabId, files });
        hasScript.set(target.tabId, true);
        return [{ result: null }];
      },
    },
  };
  return { chrome, log, hasScript };
};

const build = (chrome, hooks) => new Function(
  "chrome", "post", "setCaptureArmed", "isCaptureArmed", "log_fake",
  `${slice}
   return { armTab, ensureContentScript, broadcast, disarmCapture, sendCapture,
            getSessionId: () => captureSessionId, setSessionId: (v) => { captureSessionId = v; } };`,
)(
  chrome,
  (payload) => { hooks.posted.push(payload); return true; },
  (armed) => { hooks.armedWrites.push(armed); return Promise.resolve(); },
  async () => false,
  null,
);

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

// ---------------------------------------------------------------- 场景 1：已打开页面
// 扩展装载/重载前就已打开的标签页没有脚本（或只剩僵尸脚本）：arm 送不到它。
// 这是维护者「不会在已经打开的网页上生效」的直接成因，补注入是唯一出路。
{
  const { chrome, log } = makeChrome({ tabs: [{ id: 7, url: "https://a.test/" }] });
  const api = build(chrome, { posted: [], armedWrites: [] });
  await api.broadcast(true);
  check("S1 首次 arm 失败后补注入 content.js",
    log.injected, [{ tabId: 7, files: ["content.js"] }]);
  check("S1 补注入后把这次 arm 重发给该页",
    log.sent.map((s) => [s.tabId, s.msg.type, s.msg.armed]), [[7, "rpa-capture-arm", true]]);
}

// ---------------------------------------------------------------- 场景 2：活页面不白注入
{
  const { chrome, log } = makeChrome({
    tabs: [{ id: 1, url: "https://a.test/", hasScript: true }],
  });
  const api = build(chrome, { posted: [], armedWrites: [] });
  await api.broadcast(true);
  check("S2 有脚本的页面直接送达，不注入", log.injected, []);
  check("S2 送达一次即可", log.sent.length, 1);
}

// ---------------------------------------------------------------- 场景 3：受保护页面
{
  const { chrome, log } = makeChrome({ tabs: [{ id: 3, url: "chrome://settings/" }] });
  const api = build(chrome, { posted: [], armedWrites: [] });
  await api.broadcast(true);
  check("S3 受保护页面不做无谓注入", log.injected, []);
}

// ---------------------------------------------------------------- 场景 4：撤防不补注入
// 新注入的脚本默认就是未 arm：为撤防往每个页面塞一遍脚本毫无收益（还多一次 IPC）。
{
  const { chrome, log } = makeChrome({ tabs: [{ id: 7, url: "https://a.test/" }] });
  const api = build(chrome, { posted: [], armedWrites: [] });
  await api.broadcast(false);
  check("S4 撤防不补注入", log.injected, []);
}

// ---------------------------------------------------------------- 场景 5：注入失败不阻断
{
  const { chrome, log } = makeChrome({
    tabs: [{ id: 9, url: "https://gone.test/" }], injectFailOn: [9],
  });
  const api = build(chrome, { posted: [], armedWrites: [] });
  let threw = null;
  try {
    await api.broadcast(true);
  } catch (err) {
    threw = String(err);
  }
  check("S5 注入失败（页面已卸载）不抛异常", threw, null);
  check("S5 注入失败后不重发 arm", log.sent, []);
}

// ---------------------------------------------------------------- 场景 6：统一撤防
// 「落盘」与「广播」缺一不可：落盘供新页面/补注入脚本查态，广播是已打开页面的出路。
{
  const hooks = { posted: [], armedWrites: [] };
  const { chrome, log } = makeChrome({
    tabs: [{ id: 1, url: "https://a.test/", hasScript: true }],
  });
  const api = build(chrome, hooks);
  api.setSessionId("cap-abc");
  api.disarmCapture();
  await settle();
  check("S6 撤防：捕获态落盘置假", hooks.armedWrites, [false]);
  check("S6 撤防：会话 id 清空", api.getSessionId(), null);
  check("S6 撤防：广播 armed=false 到页面",
    log.sent.map((s) => s.msg.armed), [false]);
}

// ---------------------------------------------------------------- 场景 7：捕获结果落地即撤防
{
  const hooks = { posted: [], armedWrites: [] };
  const { chrome, log } = makeChrome({
    tabs: [{ id: 1, url: "https://a.test/", hasScript: true }],
  });
  const api = build(chrome, hooks);
  api.setSessionId("cap-abc");
  api.sendCapture({ descriptor: { kind: "browser" } });
  await settle();
  check("S7 捕获结果经 port 回传", hooks.posted.map((p) => p.type), ["capture_result"]);
  check("S7 回传用的会话 id 是本次会话",
    hooks.posted[0].sessionId, "cap-abc");
  check("S7 回传后立刻撤防（否则页面停在捕获态）",
    hooks.armedWrites, [false]);
  check("S7 撤防广播到页面", log.sent.map((s) => s.msg.armed), [false]);
}

// ---------------------------------------------------------------- 接线断言
// host 断开 = 捕获会话必然结束。推送模型没有心跳，此时不自行撤防，页面会永久停在
// 捕获态（红框跟着鼠标走、怎么点都捕获不到）。
const disconnectStart = source.indexOf("port.onDisconnect.addListener(");
if (disconnectStart < 0) {
  failed += 1;
  console.log("FAIL | 未找到 port.onDisconnect.addListener");
} else {
  const open = source.indexOf("{", source.indexOf("=>", disconnectStart));
  let depth = 0;
  let close = -1;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) { close = i + 1; break; }
    }
  }
  const body = close > 0 ? source.slice(open, close) : "";
  check("C1 port.onDisconnect 回调里必须撤防", body.includes("disarmCapture()"), true);
  check("C1 port.onDisconnect 仍要重连", body.includes("scheduleReconnect()"), true);
}

// arm 必须先落盘再广播：补注入的脚本启动时会查 storage.session 拿当前态，
// 先广播后落盘会让它在窗口期内读到 false —— arm 在这一页丢失。
const armCase = source.indexOf('case "capture_arm":');
const armCaseEnd = source.indexOf("break;", armCase);
const armBody = armCase >= 0 ? source.slice(armCase, armCaseEnd) : "";
check("C2 capture_arm 分支存在", armBody.length > 0, true);
check("C2 先落盘捕获态", /setCaptureArmed\(true\)/.test(armBody), true);
check("C2 落盘早于广播",
  armBody.indexOf("setCaptureArmed(true)") < armBody.indexOf("broadcast(true)"), true);
check("C2 ack 立即回（不等广播完成）",
  armBody.indexOf("capture_armed") > 0, true);

// 跨端口径：补注入用的文件名必须与 manifest 声明的 content script 一致（改名即静默失效）
const declared = (manifest.content_scripts || []).flatMap((cs) => cs.js || []);
check("P1 补注入的文件名与 manifest 声明一致", declared, ["content.js"]);
check("P1 补注入确实用了它", /files:\s*\["content\.js"\]/.test(source), true);

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
