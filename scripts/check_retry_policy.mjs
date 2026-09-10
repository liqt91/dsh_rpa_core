// 校验编辑器「重试能力」判定逻辑（retryPolicy，与属性面板共用同一份源码）。
// 语义契约：可重试 ⟺ manifest.retryable === true
//   —— 与运行时编排器门禁一致（orchestrator: error.retryable AND manifest.retryable），
//      也与编译器门禁互补（unsafe replay + retry_count>0 = 编译失败）。
// 用法：node scripts/check_retry_policy.mjs   （退出码非 0 = 有失败）
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const source = readFileSync(join(root, "src", "rpa_core", "devserver", "static", "app.js"), "utf8");

const start = source.indexOf("function retryPolicy");
const end = source.indexOf("function retryCountField");
if (start < 0 || end < 0 || end <= start) {
  console.error("FAIL: 未能从 app.js 定位 retryPolicy 代码段");
  process.exit(1);
}
const retryPolicy = new Function(`${source.slice(start, end)}\nreturn retryPolicy;`)();

const failures = [];
const check = (name, actual, expected) => {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failures.push(`${name}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`);
};

// --- 1. 判定矩阵（4 种 reason） ---
const cases = [
  ["declared/safe", { retryable: true, effect: { replay: "safe" } }, { allowed: true, reason: "declared" }],
  ["declared/idempotent", { retryable: true, effect: { replay: "idempotent" } }, { allowed: true, reason: "declared" }],
  ["unsafe replay", { retryable: false, effect: { replay: "unsafe" } }, { allowed: false, reason: "unsafe-replay" }],
  ["safe 但未声明", { retryable: false, effect: { replay: "safe" } }, { allowed: false, reason: "not-declared" }],
  ["idempotent 但未声明", { retryable: false, effect: { replay: "idempotent" } }, { allowed: false, reason: "not-declared" }],
  ["缺 retryable 字段", { effect: { replay: "safe" } }, { allowed: false, reason: "not-declared" }],
  ["无 effect", { retryable: false }, { allowed: false, reason: "not-declared" }],
  ["manifest 缺失", null, { allowed: false, reason: "unknown" }],
];
for (const [name, manifest, expected] of cases) {
  check(name, retryPolicy(manifest), expected);
}

// --- 2. 全量 manifest 交叉校验（防止 manifest 声明与判定逻辑漂移） ---
function walkJson(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walkJson(full));
    else if (entry.endsWith(".json")) out.push(full);
  }
  return out;
}
const manifests = walkJson(join(root, "commands")).map((p) => JSON.parse(readFileSync(p, "utf8")));
if (manifests.length === 0) failures.push("commands/ 下未找到任何 manifest");

const retryable = manifests.filter((m) => m.retryable === true);
const unsafeRetryable = retryable.filter((m) => m.effect.replay === "unsafe");
check("retryable:true 不得出现在 unsafe replay 上", unsafeRetryable.map((m) => m.id), []);

for (const m of manifests) {
  const policy = retryPolicy({ retryable: m.retryable === true, effect: m.effect });
  const expectedAllowed = m.retryable === true;
  if (policy.allowed !== expectedAllowed) {
    failures.push(`${m.id}: allowed=${policy.allowed} 与 manifest.retryable=${m.retryable} 不一致`);
  }
}

// --- 3. 统计（信息输出，便于人工核对覆盖面） ---
const byReplay = {};
for (const m of manifests) byReplay[m.effect.replay] = (byReplay[m.effect.replay] || 0) + 1;
console.log(
  `manifest 共 ${manifests.length} 个；replay 分布 ${JSON.stringify(byReplay)}；` +
    `可重试(retryable=true) ${retryable.length} 个，属性面板不显示「重试次数」的 ${manifests.length - retryable.length} 个`
);

if (failures.length) {
  console.error(`FAIL: ${failures.length} 项不一致`);
  for (const line of failures) console.error(`  - ${line}`);
  process.exit(1);
}
console.log("OK: retryPolicy 判定与 manifest 声明一致");
