#!/usr/bin/env node
/**
 * rpa-skill —— 把 workflow-studio 技能装进任意 agent 的技能目录。
 *
 * 设计约束（照搬 studio.py 的思路）：零依赖、只用 node 内置模块，
 * 所以 `npx github:you/rpa_core install` 不需要先装任何东西。
 *
 * 三件事，没有第四件：
 *   1. 拷目录（跳过 __pycache__ 这类垃圾）；
 *   2. 把 SKILL.md 里的 {{SKILL_DIR}} 换成安装后的绝对路径 —— agent 的 cwd
 *      通常是项目根，相对路径 `scripts/studio.py` 一定找不到；
 *   3. 生成 skill.json（DSH 的清单文件，其他宿主读不读都无害）。
 *
 * 不做的事：不改源文件、不跑 postinstall 偷偷写 home、不 rm 旧目录
 * （--force 时改名成 .replaced-<时间戳>，留证据）。
 */

import {
  cpSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PKG_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SKILL_NAME = "workflow-studio";
const SRC = join(PKG_ROOT, "workbuddy-suite", "skills", SKILL_NAME);
const PLACEHOLDER = "{{SKILL_DIR}}";
const SKIP_NAMES = new Set(["__pycache__", "node_modules", ".DS_Store"]);

const HOSTS = {
  dsh: { label: "DeepSeek Harness", base: () => join(homedir(), ".dsh") },
  claude: { label: "Claude Code", base: () => join(homedir(), ".claude") },
  agents: { label: "共享目录（Codex 等）", base: () => join(homedir(), ".agents") },
  gemini: { label: "Antigravity / Gemini", base: () => join(homedir(), ".gemini") },
};

const SKILLS_SUBDIR = {
  dsh: join("skills"),
  claude: join("skills"),
  agents: join("skills"),
  gemini: join("config", "skills"),
};

function toPosix(p) {
  return p.replace(/\\/g, "/");
}

function readFrontmatter(text) {
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) return {};
  const out = {};
  for (const line of m[1].split(/\r?\n/)) {
    if (!line || /^\s/.test(line)) continue;
    const i = line.indexOf(":");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (SKIP_NAMES.has(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

function targetDirFor(host, explicit) {
  if (explicit) return resolve(explicit);
  return join(HOSTS[host].base(), SKILLS_SUBDIR[host], SKILL_NAME);
}

function detectHosts() {
  return Object.keys(HOSTS).filter((h) => existsSync(HOSTS[h].base()));
}

function fail(payload, code = 1) {
  console.log(JSON.stringify({ ok: false, code, ...payload }, null, 2));
  process.exit(code);
}

// ---------------------------------------------------------------------------
// install
// ---------------------------------------------------------------------------

function install(dest, { force, dryRun }) {
  if (!existsSync(SRC)) {
    fail({ error: `包内找不到技能源目录：${SRC}` }, 2);
  }
  const skillMd = join(SRC, "SKILL.md");
  const meta = readFrontmatter(readFileSync(skillMd, "utf8"));
  const installed = existsSync(join(dest, "SKILL.md"));

  const plan = {
    ok: true,
    action: "install",
    target: toPosix(dest),
    skill: meta.name || SKILL_NAME,
    version: meta.version || "0.0.0",
    existing: installed,
    dryRun: Boolean(dryRun),
  };

  if (installed && !force) {
    return {
      ...plan,
      ok: false,
      code: "ALREADY_INSTALLED",
      error: "目标已有同名技能；确认要覆盖时加 --force（旧目录会改名保留，不删除）",
    };
  }
  if (dryRun) {
    return { ...plan, steps: ["copy", "replace " + PLACEHOLDER, "write skill.json"] };
  }

  if (installed && force) {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const kept = `${dest}.replaced-${stamp}`;
    renameSync(dest, kept);
    plan.previous_moved_to = toPosix(kept);
  }

  mkdirSync(dirname(dest), { recursive: true });
  cpSync(SRC, dest, {
    recursive: true,
    filter: (p) => !SKIP_NAMES.has(p.split(/[\\/]/).pop()),
  });

  // 占位符只在 .md 里出现；替换成安装后的绝对路径（正斜杠，bash 与 PowerShell 都吃）。
  const posixDest = toPosix(dest);
  let replaced = 0;
  for (const file of walk(dest)) {
    if (!file.endsWith(".md")) continue;
    const text = readFileSync(file, "utf8");
    if (!text.includes(PLACEHOLDER)) continue;
    writeFileSync(file, text.split(PLACEHOLDER).join(posixDest), "utf8");
    replaced += 1;
  }

  // DSH 的技能目录带 skill.json；其他宿主读到也不影响。
  const manifest = {
    schemaVersion: 1,
    id: meta.name || SKILL_NAME,
    name: meta.name || SKILL_NAME,
    version: meta.version || "0.0.0",
    description: meta.description || "",
    capabilities: ["generation"],
    permissions: { allow: [], deny: [] },
  };
  writeFileSync(join(dest, "skill.json"), JSON.stringify(manifest, null, 2) + "\n", "utf8");

  return {
    ...plan,
    files_replaced: replaced,
    entry: toPosix(join(dest, "SKILL.md")),
    next: [
      "① 宿主能跑 shell（agent 需要执行 studio.py）",
      "② python 3.10+：studio.py 只用标准库，无 pip 依赖",
      "③ rpa-core 可用：生成流程时要用 `rpa-core catalog --view agent`",
      "④ 需要可视化窗口时另装 PPX，或给 studio.py gui 传 --exe",
    ],
  };
}

// ---------------------------------------------------------------------------
// status / uninstall
// ---------------------------------------------------------------------------

function status() {
  const hosts = detectHosts();
  const rows = Object.keys(HOSTS).map((host) => {
    const dest = targetDirFor(host);
    const entry = join(dest, "SKILL.md");
    const present = existsSync(entry);
    const row = {
      host,
      label: HOSTS[host].label,
      path: toPosix(dest),
      installed: present,
      host_root_exists: existsSync(HOSTS[host].base()),
    };
    if (present) {
      const meta = readFrontmatter(readFileSync(entry, "utf8"));
      row.version = meta.version || "0.0.0";
      const md = walk(dest).filter((f) => f.endsWith(".md"));
      row.unresolved_placeholder = md.filter((f) =>
        readFileSync(f, "utf8").includes(PLACEHOLDER),
      ).length;
      row.script_present = existsSync(join(dest, "scripts", "studio.py"));
      row.healthy = row.unresolved_placeholder === 0 && row.script_present;
    }
    return row;
  });
  return {
    ok: true,
    action: "status",
    source: toPosix(SRC),
    detected_hosts: hosts,
    skills: rows,
  };
}

function uninstall(host, explicit) {
  const dest = targetDirFor(host, explicit);
  if (!existsSync(dest)) {
    return { ok: false, code: "NOT_INSTALLED", target: toPosix(dest) };
  }
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const kept = `${dest}.removed-${stamp}`;
  renameSync(dest, kept);
  return { ok: true, action: "uninstall", moved_to: toPosix(kept) };
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

const HELP = `rpa-skill ${JSON.parse(readFileSync(join(PKG_ROOT, "package.json"), "utf8")).version}

用法：
  rpa-skill install [--target dsh|claude|agents|gemini|all] [--dir PATH] [--force] [--dry-run]
  rpa-skill status
  rpa-skill uninstall [--target dsh|claude|agents|gemini] [--dir PATH]

目标目录：
  dsh      ~/.dsh/skills/workflow-studio
  claude   ~/.claude/skills/workflow-studio
  agents   ~/.agents/skills/workflow-studio      （Codex 等跨宿主共享）
  gemini   ~/.gemini/config/skills/workflow-studio

不指定 --target 时自动装到本机已存在的宿主；--dir 可装到任意目录（CI / 测试用）。
`;

function main() {
  const argv = process.argv.slice(2);
  // --help 可能出现在任何位置，也可能就是唯一参数 —— 先于子命令解析处理掉。
  if (argv.length === 0 || argv.includes("--help") || argv.includes("-h")) {
    process.stdout.write(HELP);
    return;
  }
  const cmd = argv[0];
  const flags = { force: false, dryRun: false, target: null, dir: null };
  for (let i = 1; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--force") flags.force = true;
    else if (a === "--dry-run") flags.dryRun = true;
    else if (a === "--target") flags.target = argv[++i];
    else if (a === "--dir") flags.dir = argv[++i];
    else fail({ error: `未知参数：${a}` }, 2);
  }

  if (cmd === "help") {
    process.stdout.write(HELP);
    return;
  }

  if (cmd === "status") {
    console.log(JSON.stringify(status(), null, 2));
    return;
  }

  if (cmd === "uninstall") {
    const host = flags.target || detectHosts()[0];
    if (!host && !flags.dir) fail({ error: "没有可探测到的宿主，用 --target 或 --dir 指定" }, 2);
    console.log(JSON.stringify(uninstall(host, flags.dir), null, 2));
    return;
  }

  if (cmd === "install") {
    const targets = [];
    if (flags.dir) {
      targets.push({ host: null, dest: targetDirFor(null, flags.dir) });
    } else if (flags.target === "all") {
      const detected = detectHosts();
      if (detected.length === 0) fail({ error: "本机没探测到任何宿主，用 --dir 指定目标目录" }, 2);
      for (const h of detected) targets.push({ host: h, dest: targetDirFor(h) });
    } else if (flags.target) {
      if (!HOSTS[flags.target]) fail({ error: `未知宿主：${flags.target}` }, 2);
      targets.push({ host: flags.target, dest: targetDirFor(flags.target) });
    } else {
      const detected = detectHosts();
      if (detected.length === 0) {
        fail({ error: "没探测到宿主（~/.dsh / ~/.claude / ~/.agents / ~/.gemini）", hint: "用 --target 或 --dir 指定" }, 2);
      }
      for (const h of detected) targets.push({ host: h, dest: targetDirFor(h) });
    }

    const results = targets.map((t) => ({ host: t.host, ...install(t.dest, flags) }));
    const failed = results.filter((r) => !r.ok);
    console.log(JSON.stringify(results.length === 1 ? results[0] : { ok: failed.length === 0, results }, null, 2));
    if (failed.length) process.exit(1);
    return;
  }

  fail({ error: `未知子命令：${cmd}`, hint: "rpa-skill --help" }, 2);
}

main();
