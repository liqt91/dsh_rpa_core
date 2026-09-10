// 临时验证：编辑器「通道解析预览」判定逻辑（与执行器 extension_exec.channel_matches_host 同语义）。
// 沙箱下 playwright 不可用，故直接抽取 app.js 中的判定函数在 node 里跑矩阵。
// 用法：node scripts/tmp_verify_channel_preview.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "src", "rpa_core", "devserver", "static", "app.js"), "utf8");

const start = source.indexOf("// 自研扩展执行通道状态");
const end = source.indexOf("function channelPreviewField");
if (start < 0 || end < 0 || end <= start) {
  console.error("FAIL: 未能定位通道解析代码段");
  process.exit(1);
}
const snippet = source.slice(start, end);

const state = { extChannel: null };
const factory = new Function("state", `${snippet}\nreturn { resolveChannelPreview, channelMatchesHost, hostBrowserLabel };`);
const api = factory(state);

const cases = [
  // [说明, extChannel, with, 期望 channel]
  ["离线 → playwright", { online: false, host: null }, {}, "playwright"],
  ["在线(Edge) 无明显 channel → extension", { online: true, host: { browser: "msedge" } }, {}, "extension"],
  ["在线(Edge) + channel=msedge → extension", { online: true, host: { browser: "msedge" } }, { channel: "msedge" }, "extension"],
  ["在线(Edge) + channel=chromium → extension（任意 Chromium）", { online: true, host: { browser: "msedge" } }, { channel: "chromium" }, "extension"],
  ["在线(Chrome) + channel=msedge → playwright（让位）", { online: true, host: { browser: "chrome" } }, { channel: "msedge" }, "playwright"],
  ["在线(Edge) + 显式 extension + channel=chrome → 冲突", { online: true, host: { browser: "msedge" } }, { transport: "extension", channel: "chrome" }, "extension(将失败)"],
  ["显式 playwright → playwright", { online: true, host: { browser: "msedge" } }, { transport: "playwright" }, "playwright"],
  ["在线但未上报宿主 + channel=msedge → playwright", { online: true, host: null }, { channel: "msedge" }, "playwright"],
];

let failed = 0;
for (const [label, extChannel, withArgs, expected] of cases) {
  state.extChannel = extChannel;
  const got = api.resolveChannelPreview({ "with": withArgs }).channel;
  const ok = got === expected;
  if (!ok) failed += 1;
  console.log(`${ok ? "PASS" : "FAIL"} | ${label} → ${got}${ok ? "" : ` (期望 ${expected})`}`);
}

// 与后端 channel_matches_host 的矩阵对齐（子集）；status 形如 {online, host:{browser}}
const matchCases = [
  ["chromium", { browser: "brave" }, true],
  ["chromium", { browser: "firefox" }, false],
  ["msedge", { browser: "chrome" }, false],
  ["msedge", { browser: "msedge" }, true],
  ["", { browser: "chrome" }, true],
  ["msedge", null, false],
];
for (const [channel, host, expected] of matchCases) {
  const got = api.channelMatchesHost(channel, { online: true, host });
  const ok = got === expected;
  if (!ok) failed += 1;
  console.log(`${ok ? "PASS" : "FAIL"} | channelMatchesHost(${channel || '""'}, ${host ? host.browser : "null"}) → ${got}`);
}

process.exit(failed ? 1 : 0);
