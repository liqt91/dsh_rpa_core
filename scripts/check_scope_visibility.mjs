// 变量可见范围（2026-10-14）的 JS 侧判据：与 tests/contract/test_variable_scope_visibility.py
// 跑**同一份用例表**（tests/contract/data/scope_visibility_cases.json）。
//
// 为什么必须有这一条：桌面端菜单读 Python 的 compiler.visible_scope，而 Web 的属性面板
// 是**另一份 JS 实现**（app.js 的 visibleScopeAt）。两边各自实现一遍「哪些变量在这个节点
// 可见」，正是零构建双端项目的经典漂移点——Python 侧读 app.js 源码只能证明「写了
// visibleScopeAt」，证明不了「它对每个节点的判定与编译器一致」。这里切片真求值。
//
// 用法：node scripts/check_scope_visibility.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = join(here, "..");
const appJsPath = join(ROOT, "src", "rpa_core", "devserver", "static", "app.js");
const source = readFileSync(appJsPath, "utf8");
const table = JSON.parse(
  readFileSync(
    join(ROOT, "tests", "contract", "data", "scope_visibility_cases.json"),
    "utf8",
  ),
);

const START = "// [scope-helpers:start]";
const END = "// [scope-helpers:end]";
if (source.split(START).length !== 2 || source.split(END).length !== 2) {
  console.error("FAIL: scope-helpers 锚点必须恰好出现一次（start/end 各一）");
  process.exit(1);
}
const slice = source.slice(source.indexOf(START), source.indexOf(END) + END.length);

let failed = 0;
const check = (label, actual, expected) => {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(
    `${ok ? "PASS" : "FAIL"} | ${label}` +
      (ok ? "" : ` → got ${JSON.stringify(actual)} want ${JSON.stringify(expected)}`),
  );
};

let factory;
try {
  factory = new Function(`${slice}\nreturn { visibleScopeAt, declaredNamesOf };`);
} catch (err) {
  console.error(`FAIL: 切片段语法错误 → ${err.message}`);
  process.exit(1);
}
const { visibleScopeAt, declaredNamesOf } = factory();
for (const [name, fn] of [["visibleScopeAt", visibleScopeAt], ["declaredNamesOf", declaredNamesOf]]) {
  if (typeof fn !== "function") {
    console.error(`FAIL: 切片区必须导出 ${name}`);
    process.exit(1);
  }
}

// 「变量写入」认 manifest 声明（与 compiler / GUI 同一事实源）：从真 manifest 读，
// 命令改名或字段改名都会让这条门禁红，而不是静默用桩。
const setVarManifest = JSON.parse(
  readFileSync(join(ROOT, "commands", "data", "setVar.json"), "utf8"),
);
const varWrite = setVarManifest["x-var-write"];
if (!varWrite || !varWrite.field) {
  console.error("FAIL: commands/data/setVar.json 缺 x-var-write.field（用例表靠它）");
  process.exit(1);
}
const manifestOf = (command) => (command === "data.setVar" ? setVarManifest : null);

for (const item of table.cases) {
  const scope = visibleScopeAt(table.workflow.root, item.path, manifestOf);
  const label = `${item.nodeId}${item.note ? ` (${item.note})` : ""}`;
  check(`${label} 可见变量`, scope.variables.map((v) => v.name), item.variables);
  check(`${label} 循环变量`, scope.loopVars, item.loopVars);
  check(`${label} 错误变量`, scope.errorVars, item.errorVars);
}

// 目标节点不存在 ⇒ 空作用域（调用方据此给空菜单，不退回「列全流程」）
const missing = visibleScopeAt(
  table.workflow.root,
  [{ key: "children", index: 99 }],
  manifestOf,
);
check("节点不存在 ⇒ 空作用域", missing, { variables: [], loopVars: [], errorVars: [] });

// ---- 接线：两个消费面都必须真喂「当前节点」，且全流程收集已消失 ----------------
check(
  "collectUserVariables 接收当前节点",
  /function collectUserVariables\(targetPath = state\.selected\)/.test(source),
  true,
);
check(
  "collectUserVariables 走 visibleScopeAt",
  source.includes("return visibleScopeAt(state.workflow.root, targetPath, manifestOf).variables"),
  true,
);
check(
  "computeReferencePaths 接收当前节点",
  /function computeReferencePaths\(targetPath = state\.selected\)/.test(source),
  true,
);
check(
  "computeReferencePaths 用可见范围的 loop/error 变量",
  source.includes("for (const item of scope.loopVars) paths.push"),
  true,
);
check(
  "fx 浮层真喂当前节点（变量分组）",
  source.includes("const userVars = collectUserVariables(state.selected);"),
  true,
);
check(
  "fx 浮层真喂当前节点（内置作用域分组）",
  source.includes("computeReferencePaths(state.selected)"),
  true,
);
check(
  "美元补全的缺省即当前节点（attachRefCompletion 拿不到节点句柄）",
  /function computeReferencePaths\(targetPath = state\.selected\)/.test(source),
  true,
);
check(
  "全流程用户变量收集已消失（不再 found.set(alias, ...)）",
  source.includes('found.set(alias, "输出别名")'),
  false,
);
check(
  "全流程 loop/error 收集已消失（不再遍历全树 push loop.*）",
  /if \(node\.type === "forEach"\) paths\.push\(/.test(source),
  false,
);

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
