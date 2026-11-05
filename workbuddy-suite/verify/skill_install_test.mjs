#!/usr/bin/env node
/**
 * 安装器冒烟：把技能装到临时目录，断言它真的可用，然后清掉。
 *
 * 不进 check_all（它要写磁盘、要起 node / python 子进程），手动跑：
 *   node workbuddy-suite/verify/skill_install_test.mjs
 *
 * 断言的是「装完能不能用」，不是「文件存在」——agent 的 cwd 通常是项目根，
 * 所以最关键的两条是：
 *   ① 占位符必须被换成绝对路径；
 *   ② 文档里那条命令**照抄出来真的能跑**（rpa-core 不在 PATH 上也必须能跑）。
 * 第二条是上一轮漏掉的：只断言「文件齐 + 路径是绝对的」时，`rpa-core` 找不到
 * 这件事完全看不出来，用户是在「要流程」的时候才撞墙。
 *
 * 负向夹具（缺运行时的机器）不能靠「本机恰好没装 rpa_core」来构造，那会把
 * 环境当判据。这里现造一个隔离解释器（venv --without-pip）+ 清空 PATH，
 * 让「找不到」这件事确定成立。
 */

import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const CLI = join(REPO, "bin", "rpa-skill.mjs");

const root = mkdtempSync(join(tmpdir(), "rpa-skill-"));
const dest = join(root, "workflow-studio");
const results = [];

function run(args, opts = {}) {
  // 用 spawnSync 而不是 execFileSync：后者成功时不给 stderr，
  // 而「缺运行时要在 stderr 上喊一声」正是要断言的东西。
  const p = spawnSync(process.execPath, [opts.cli || CLI, ...args], {
    encoding: "utf8",
    env: opts.env || process.env,
    timeout: 180000,
  });
  return { code: p.status ?? (p.error ? 1 : 0), out: p.stdout || "", err: p.stderr || "" };
}

function shell(cmd, opts = {}) {
  const p = spawnSync(cmd, {
    encoding: "utf8",
    env: opts.env || process.env,
    shell: true,
    timeout: 180000,
  });
  return { code: p.status ?? (p.error ? 1 : 0), out: p.stdout || "", err: p.stderr || "" };
}

function check(name, fn) {
  try {
    const detail = fn();
    results.push({ name, ok: true, detail });
  } catch (err) {
    results.push({ name, ok: false, detail: String(err.message || err) });
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function json(text) {
  return JSON.parse(text);
}

const PLACEHOLDER_RE = /\{\{[A-Z_]+\}\}/g;

let installed = null;

// ---------------------------------------------------------------------------
// 正向：能装上，且文档里的命令真的能跑
// ---------------------------------------------------------------------------

check("install 返回 ok 且接上了运行时", () => {
  const r = run(["install", "--dir", dest]);
  assert(r.code === 0, `rc=${r.code} out=${r.out.slice(0, 300)}`);
  installed = json(r.out);
  assert(installed.ok === true, `ok=${installed.ok}`);
  assert(installed.runtime?.ok === true, `runtime.ok=${installed.runtime?.ok}（${JSON.stringify(installed.runtime?.attempts || [])}）`);
  return `${installed.runtime.mode} · ${installed.runtime.resolved_by}`;
});

check("拷贝了完整技能树（含定位层与 runtime.json）", () => {
  const need = [
    "SKILL.md",
    "skill.json",
    "runtime.json",
    "references/assembly.md",
    "scripts/studio.py",
    "scripts/rc.py",
    "scripts/_runtime.py",
  ];
  const missing = need.filter((p) => !existsSync(join(dest, p)));
  assert(missing.length === 0, `缺 ${missing.join(", ")}`);
  return `${need.length} 个必需文件齐`;
});

check("占位符全部替换（三个都查，不只是 SKILL_DIR）", () => {
  const bad = [];
  for (const rel of ["SKILL.md", "references/assembly.md", "references/protocol.md"]) {
    const text = readFileSync(join(dest, rel), "utf8");
    const hits = text.match(PLACEHOLDER_RE) || [];
    if (hits.length) bad.push(`${rel}: ${[...new Set(hits)].join(",")}`);
  }
  assert(bad.length === 0, `仍有未替换的占位符 → ${bad.join(" | ")}`);
  return "SKILL.md 与 references 里没有残留";
});

check("文档里那条命令照抄出来真的能跑", () => {
  const md = readFileSync(join(dest, "SKILL.md"), "utf8");
  const line = md
    .split(/\r?\n/)
    .map((l) => l.trim())
    .find((l) => /^".*"\s+".*scripts[\\/]rc\.py"\s+--help$/.test(l));
  assert(line, "SKILL.md 里找不到 `<python> <skill>/scripts/rc.py --help` 这一行");
  const r = shell(line);
  assert(r.code === 0, `退出码 ${r.code}：${(r.out || r.err).slice(0, 200)}`);
  assert(/catalog/.test(r.out), "输出里没有 catalog 子命令，看起来跑的不是 rpa-core");
  return "退出码 0，且列出 catalog";
});

check("rc.py 能取到能力清单（真跑 catalog）", () => {
  const py = installed.runtime.python;
  const r = shell(`"${py}" "${join(dest, "scripts", "rc.py").replace(/\\/g, "/")}" catalog --view agent`);
  assert(r.code === 0, `退出码 ${r.code}：${(r.out || r.err).slice(0, 200)}`);
  const view = json(r.out);
  assert(view.semantic_commands > 0, `semantic_commands=${view.semantic_commands}`);
  assert(/^[0-9a-f]{64}$/.test(view.catalog_digest || ""), "catalog_digest 不是 64 位十六进制");
  return `${view.semantic_commands} 条语义命令`;
});

check("runtime.json 指向真实存在的解释器与源码根", () => {
  const rt = json(readFileSync(join(dest, "runtime.json"), "utf8"));
  assert(rt.schemaVersion === 1, `schemaVersion=${rt.schemaVersion}`);
  assert(rt.python && existsSync(rt.python), `python 不存在：${rt.python}`);
  assert(rt.source_root && existsSync(join(rt.source_root, "src", "rpa_core", "cli.py")), `source_root 不对：${rt.source_root}`);
  assert(["src", "module", "exe"].includes(rt.rpa_core?.mode), `mode=${rt.rpa_core?.mode}`);
  return `${rt.rpa_core.mode} · ${rt.rpa_core.resolved_by}`;
});

check("skill.json 可解析且字段齐全", () => {
  const m = json(readFileSync(join(dest, "skill.json"), "utf8"));
  for (const k of ["schemaVersion", "id", "name", "version", "description"]) {
    assert(k in m, `缺字段 ${k}`);
  }
  assert(m.name === "workflow-studio", `name=${m.name}`);
  assert(/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(m.name), "name 不是 kebab-case");
  return `version=${m.version}`;
});

check("description 在宿主上限内（DSH 500）", () => {
  const md = readFileSync(join(dest, "SKILL.md"), "utf8");
  const body = md.split("---")[1] || "";
  const line = body.split(/\r?\n/).find((l) => l.startsWith("description:"));
  assert(line, "frontmatter 里没有 description");
  const len = line.slice("description:".length).trim().length;
  assert(len <= 500, `description ${len} 字符 > 500`);
  assert(len > 40, `description 只有 ${len} 字符，太短会让模型认不出触发场景`);
  return `${len} 字符`;
});

check("doctor 全绿", () => {
  const r = run(["doctor", "--dir", dest]);
  assert(r.code === 0, `rc=${r.code}：${r.out.slice(0, 400)}`);
  const payload = json(r.out);
  const failed = (payload.checks || []).filter((c) => !c.ok).map((c) => c.name);
  assert(failed.length === 0, `红的是 ${failed.join(", ")}`);
  return `${payload.checks.length} 项全过`;
});

check("重复安装被拒绝（幂等）", () => {
  const r = run(["install", "--dir", dest]);
  assert(r.code !== 0, "重复安装竟然返回 0");
  assert(json(r.out).code === "ALREADY_INSTALLED", `code=${json(r.out).code}`);
  return "rc≠0 + ALREADY_INSTALLED";
});

check("--force 覆盖时旧目录改名保留（不删除）", () => {
  const r = run(["install", "--dir", dest, "--force"]);
  assert(r.code === 0, `rc=${r.code}`);
  const kept = String(json(r.out).previous_moved_to || "").replace(/\//g, "\\");
  assert(kept && existsSync(kept), `旧目录没保留：${kept}`);
  return `保留为 ${kept.split("\\").pop()}`;
});

check("dry-run 不落盘，但先说清运行时连不连得上", () => {
  const ghost = join(root, "ghost");
  const r = run(["install", "--dir", ghost, "--dry-run"]);
  assert(r.code === 0, `rc=${r.code}`);
  assert(!existsSync(ghost), "dry-run 竟然创建了目录");
  assert(json(r.out).runtime?.ok === true, "dry-run 没报运行时探测结果");
  return "只报计划 + 运行时探测";
});

check("status 报得出运行时状态", () => {
  const r = run(["status"]);
  assert(r.code === 0, `rc=${r.code}`);
  const payload = json(r.out);
  assert(Array.isArray(payload.skills), "skills 不是数组");
  const atDest = payload.skills.find((s) => s.path.replace(/\//g, "\\") === dest.replace(/\//g, "\\"));
  // 装到 --dir 的目录不在宿主目录里，status 扫不到属正常；宿主里那份才要求 healthy。
  if (atDest && atDest.installed) {
    assert(atDest.runtime_ok === true, "status 没认出运行时");
    return `宿主内 ${payload.skills.length} 条，本目录 runtime_ok`;
  }
  return `${payload.skills.length} 个宿主被探测（--dir 不在扫描范围）`;
});

// ---------------------------------------------------------------------------
// 负向：换一台没装 rpa_core 的机器（包在仓库外 + PATH 里没有 python）
// ---------------------------------------------------------------------------

const pkgRoot = join(root, "pkg");
const bareEnv = join(root, "bare-env");
const abroad = join(root, "abroad");

function barePython() {
  return join(bareEnv, process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
}

check("夹具：造出「找不到运行时」的环境", () => {
  mkdirSync(pkgRoot, { recursive: true });
  for (const rel of ["bin", "workbuddy-suite", "package.json"]) {
    // 必须带 filter：本机 node 22 上「不带 filter 的 cpSync」会直接把进程干掉
    // （exit 127、无异常、无输出）。安装器本身一直带 filter，所以不受影响；
    // 这里跟随同样的调用形态，别为了省一行把自己的进程写没了。
    cpSync(join(REPO, rel), join(pkgRoot, rel), {
      recursive: true,
      filter: (p) => !p.includes("__pycache__") && !p.endsWith(".pyc"),
    });
  }
  execFileSync(installed.runtime.python, ["-m", "venv", "--without-pip", bareEnv], { stdio: "ignore" });
  assert(existsSync(barePython()), "裸解释器没造出来");
  // 夹具自检：它必须真的 import 不到 rpa_core，否则下面的红是假的。
  let imported = true;
  try {
    execFileSync(barePython(), ["-c", "import rpa_core"], { stdio: "ignore" });
  } catch {
    imported = false;
  }
  assert(imported === false, "裸解释器竟然能 import rpa_core，负向夹具失效");
  return "包搬出仓库 + 隔离解释器 + 清空 PATH";
});

const noPathEnv = () => ({ ...process.env, PATH: join(root, "no-such"), Path: join(root, "no-such") });

check("仓库外安装：不报错，但把「没接上运行时」说清楚", () => {
  const r = run(["install", "--dir", abroad, "--python", barePython()], {
    cli: join(pkgRoot, "bin", "rpa-skill.mjs"),
    env: noPathEnv(),
  });
  assert(r.code === 0, `rc=${r.code}（装文件本身不该失败）：${r.out.slice(0, 200)}`);
  const payload = json(r.out);
  assert(payload.ok === true, "文件没装成");
  assert(payload.runtime.ok === false, `runtime.ok=${payload.runtime.ok}，不该探到运行时`);
  assert(!existsSync(join(abroad, "runtime.json")), "没探到运行时却写了 runtime.json");
  assert((payload.warnings || []).length > 0, "缺运行时却没有 warnings");
  assert(/⚠/.test(r.err), "stderr 上没有显眼的告警");
  return "ok:true + runtime.ok:false + ⚠ 到 stderr";
});

check("这种安装里 rc.py 必须 exit 2 并给可照抄的修法", () => {
  const r = shell(`"${barePython()}" "${join(abroad, "scripts", "rc.py").replace(/\\/g, "/")}" catalog --view agent`, {
    env: noPathEnv(),
  });
  assert(r.code === 2, `退出码 ${r.code}（应为 2）`);
  const payload = json(r.out);
  assert(payload.code === "RPA_CORE_NOT_FOUND", `code=${payload.code}`);
  assert(Array.isArray(payload.fix) && payload.fix.length > 0, "没给修法");
  assert(payload.fix.some((f) => f.includes("--rpa-core-dir")), "修法里没提 --rpa-core-dir");
  return "exit 2 + RPA_CORE_NOT_FOUND + fix 列表";
});

check("这种安装里 doctor 报红", () => {
  const r = run(["doctor", "--dir", abroad], { cli: join(pkgRoot, "bin", "rpa-skill.mjs"), env: noPathEnv() });
  assert(r.code === 1, `rc=${r.code}（应为 1）`);
  const payload = json(r.out);
  assert(payload.ok === false, "doctor 竟然说没事");
  const names = payload.checks.filter((c) => !c.ok).map((c) => c.name);
  assert(names.includes("runtime.json"), `红的项里没有 runtime.json：${names.join(",")}`);
  return `红：${names.join(", ")}`;
});

check("给 --rpa-core-dir 就能补上，rc.py 随之可用", () => {
  const r = run(["install", "--dir", abroad, "--force", "--rpa-core-dir", REPO], {
    cli: join(pkgRoot, "bin", "rpa-skill.mjs"),
    env: noPathEnv(),
  });
  assert(r.code === 0, `rc=${r.code}`);
  assert(json(r.out).runtime?.ok === true, "指定仓库根后仍没探到运行时");
  const again = shell(`"${barePython()}" "${join(abroad, "scripts", "rc.py").replace(/\\/g, "/")}" --help`, {
    env: noPathEnv(),
  });
  assert(again.code === 0, `修好后 rc.py 仍失败：${(again.out || again.err).slice(0, 200)}`);
  return "重装即通";
});

// ---------------------------------------------------------------------------
// 另两种正当形态：pip 装进解释器、PATH 上有 rpa-core
// ---------------------------------------------------------------------------

check("pip 形态（mode=module）doctor 不误报「缺源码根」", () => {
  // 实测踩到的假红：pip 装进 site-packages 时 source_root 必然为空，
  // 旧判据却要求它存在 —— 于是 import 与 rc.py 都绿，唯独这条红，
  // 还给出「去装源码树」的误导修法。这里直接改写 runtime.json 造出该形态。
  const rtPath = join(dest, "runtime.json");
  const original = readFileSync(rtPath, "utf8");
  try {
    const rt = json(original);
    rt.source_root = null;
    rt.rpa_core = { ...rt.rpa_core, mode: "module", src: null };
    writeFileSync(rtPath, JSON.stringify(rt, null, 2) + "\n", "utf8");

    const payload = json(run(["doctor", "--dir", dest]).out);
    const byName = Object.fromEntries(payload.checks.map((c) => [c.name, c]));
    assert(byName["source root"], "doctor 里没有 source root 这一项，判据失效");
    assert(byName["source root"].ok === true, `source root 误红：${byName["source root"].detail}`);
    assert(/不适用/.test(byName["source root"].detail), `没说清为什么不需要：${byName["source root"].detail}`);
    // 关键：不能靠「放宽判定」混过去 —— 这种形态必须真的能 import 才算好
    assert(byName["import rpa_core"]?.ok === true, "module 形态下 import rpa_core 却没绿");
    return "source root 标为不适用，import 仍绿";
  } finally {
    writeFileSync(rtPath, original, "utf8");
  }
});

check("exe 形态（PATH 上只有 rpa-core）：命令直指 exe，不绕 rc.py", () => {
  // uv tool / pipx 装出来的形态：PATH 上有 rpa-core.exe，但没有「能 import 的解释器」。
  // 这种机器上 {{RPA_CORE}} 若还写成 `python rc.py`，凭空多要一个 python —— 必挂。
  const exeRel = process.platform === "win32" ? ["Scripts", "rpa-core.exe"] : ["bin", "rpa-core"];
  const srcExe = join(REPO, ".venv", ...exeRel);
  assert(existsSync(srcExe), `夹具缺失：${srcExe}（本仓约定跑 .venv，缺了就没法验 exe 形态）`);

  const binDir = join(root, "pathbin");
  mkdirSync(binDir, { recursive: true });
  const stub = join(binDir, exeRel[exeRel.length - 1]);
  cpSync(srcExe, stub, { filter: () => true });

  // PATH 只留这一个目录：所有 python 候选都会失败，exe 是唯一出路。
  const env = { ...process.env, PATH: binDir, Path: binDir };
  const at = join(root, "exe-skill");
  const cli = join(pkgRoot, "bin", "rpa-skill.mjs");
  const r = run(["install", "--dir", at], { env, cli });
  assert(r.code === 0, `rc=${r.code}：${(r.out || r.err).slice(0, 300)}`);
  const payload = json(r.out);
  assert(payload.runtime?.mode === "exe", `探测到的是 ${payload.runtime?.mode}，不是 exe`);

  const md = readFileSync(join(at, "SKILL.md"), "utf8");
  const line = md.split(/\r?\n/).find((l) => l.includes("catalog --view agent"));
  assert(line, "SKILL.md 里找不到 catalog 那条命令");
  assert(line.includes("rpa-core"), `命令里没有 exe：${line}`);
  assert(!line.includes("rc.py"), `exe 形态仍然绕了 rc.py：${line}`);

  const doc = json(run(["doctor", "--dir", at], { env, cli }).out);
  const failed = (doc.checks || []).filter((c) => !c.ok).map((c) => `${c.name}(${c.detail})`);
  assert(failed.length === 0, `doctor 有红：${failed.join(", ")}`);
  return "mode=exe + 命令直指 exe + doctor 全绿";
});

// 只删自己建的临时目录（名字带 rpa-skill- 前缀）。
if (root.includes("rpa-skill-") && existsSync(root)) {
  rmSync(root, { recursive: true, force: true });
}

const failed = results.filter((r) => !r.ok);
for (const r of results) {
  console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.detail ? " — " + r.detail : ""}`);
}
console.log(failed.length === 0 ? "\nSKILL INSTALL SMOKE PASSED" : `\nSKILL INSTALL SMOKE FAILED (${failed.length})`);
process.exit(failed.length === 0 ? 0 : 1);
