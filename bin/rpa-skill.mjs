#!/usr/bin/env node
/**
 * rpa-skill —— 把 workflow-studio 技能装进任意 agent 的技能目录，并把运行时的线接好。
 *
 * 设计约束（照搬 studio.py 的思路）：零依赖、只用 node 内置模块，
 * 所以 `npx github:you/rpa_core install` 不需要先装任何东西。
 *
 * 五件事：
 *   1. 拷目录（跳过 __pycache__ 这类垃圾）；
 *   2. 探测 Python 运行时：哪个解释器能 import rpa_core、源码根在哪 —— 这是最容易
 *      漏的一环。技能是拷进宿主目录的，`rpa-core` 一旦不在 PATH 上，文档里的裸命令
 *      就 `command not found`（实际发生过，而且报错发生在用户已经开始要流程的时候，
 *      看起来像技能坏了）；
 *   3. 把探测结果写进 runtime.json，技能侧的 _runtime.py / rc.py 读它；
 *   4. 把 SKILL.md 里的 {{SKILL_DIR}} / {{PYTHON}} / {{RPA_CORE}} 换成绝对路径 ——
 *      agent 的 cwd 通常是项目根，相对路径一定找不到；
 *   5. 生成 skill.json（DSH 的清单文件，其他宿主读不读都无害）。
 *
 * 不做的事：不改源文件、不跑 postinstall 偷偷写 home、不 rm 旧目录
 * （--force 时挪到同级的 .rpa-skill-backups/ 下并给 SKILL.md 改名，留证据但不冒充技能）。
 */

import { execFileSync } from "node:child_process";
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
import { delimiter, basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PKG_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PKG_VERSION = JSON.parse(readFileSync(join(PKG_ROOT, "package.json"), "utf8")).version;
const SKILL_NAME = "workflow-studio";
const SRC = join(PKG_ROOT, "workbuddy-suite", "skills", SKILL_NAME);
const SKIP_NAMES = new Set(["__pycache__", "node_modules", ".DS_Store"]);

// 文档里允许出现的占位符。留一个没换，就是个会在用户机器上炸的路径。
const PLACEHOLDERS = ["{{SKILL_DIR}}", "{{PYTHON}}", "{{RPA_CORE}}"];
const PLACEHOLDER_RE = /\{\{[A-Z_]+\}\}/g;

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

// 探测脚本：能不能 import rpa_core.cli —— 这正是「生成流程」那一步真正依赖的东西。
// 用 argv 传 src，避免把路径拼进代码（Windows 反斜杠 + 空格会碎）。
const PROBE_SRC = `import sys, importlib
src = sys.argv[1]
if src != "-":
    sys.path.insert(0, src)
try:
    mod = importlib.import_module("rpa_core.cli")
except Exception as exc:
    sys.stderr.write("PROBE_FAIL %s: %s\\n" % (type(exc).__name__, exc))
    sys.exit(3)
print("OK " + getattr(mod, "__file__", ""))
`;

const MAX_PROBES = 12;
const PY_ENV = { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };

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
// 运行时探测：python 解释器 × rpa_core 源码根
// ---------------------------------------------------------------------------

function probeRpaCore(python, srcDir) {
  try {
    const out = execFileSync(python, ["-c", PROBE_SRC, srcDir || "-"], {
      encoding: "utf8",
      timeout: 30000,
      stdio: ["ignore", "pipe", "pipe"],
      env: PY_ENV,
    });
    const line = out.split(/\r?\n/).filter((l) => l.startsWith("OK ")).pop() || "";
    return { ok: true, module: line.slice(3).trim() };
  } catch (err) {
    if (err.code === "ENOENT") return { ok: false, detail: "找不到这个解释器" };
    const tail = String(err.stderr || "").trim().split(/\r?\n/).pop();
    return { ok: false, detail: tail || `退出码 ${err.status}` };
  }
}

function isSourceRoot(p) {
  return Boolean(p) && existsSync(join(p, "src", "rpa_core", "cli.py"));
}

function sourceRootCandidates(explicit) {
  const seen = [];
  const push = (p) => {
    if (!p) return;
    const abs = resolve(String(p));
    if (!seen.includes(abs) && isSourceRoot(abs)) seen.push(abs);
  };
  push(explicit);
  push(process.env.RPA_CORE_HOME);
  // clone / 本地安装时，包自己就躺在仓库里；npm 全局安装时这里找不到，靠上面的显式参数。
  let cur = PKG_ROOT;
  for (let i = 0; i < 5; i += 1) {
    push(cur);
    const up = resolve(cur, "..");
    if (up === cur) break;
    cur = up;
  }
  return seen;
}

function pythonCandidates(explicit, roots) {
  const out = [];
  const push = (cmd, by) => {
    if (!cmd) return;
    if (!out.some((c) => c.cmd === cmd)) out.push({ cmd, by });
  };
  push(explicit, "flag");
  push(process.env.RPA_CORE_PYTHON, "env");
  for (const root of roots) {
    push(join(root, ".venv", "Scripts", "python.exe"), "repo-venv");
    push(join(root, ".venv", "bin", "python"), "repo-venv");
  }
  push("python3", "PATH");
  push("python", "PATH");
  return out;
}

function inferSourceRoot(moduleFile) {
  if (!moduleFile) return null;
  const parts = toPosix(moduleFile).split("/");
  const i = parts.lastIndexOf("src");
  if (i > 0 && parts[i + 1] === "rpa_core") {
    const root = parts.slice(0, i).join("/");
    return isSourceRoot(root) ? root : null;
  }
  return null;
}

function findRpaCoreExe() {
  // 只认「能直接 spawn」的形态：Windows 的 .exe、POSIX 的 rpa-core。
  // .cmd / .bat 在 Windows 上不能直接 execFileSync（Node 会拒，且不该为此开 shell），
  // 列进来只会被选中然后报「退出码 null」—— 假候选比没候选更坏。
  const names = process.platform === "win32" ? ["rpa-core.exe"] : ["rpa-core"];
  for (const dir of String(process.env.PATH || "").split(delimiter)) {
    if (!dir) continue;
    for (const n of names) {
      const p = join(dir, n);
      if (existsSync(p)) return p;
    }
  }
  return null;
}

/**
 * 找出「一个能跑 rpa-core 的组合」。顺序：源码树 + 解释器 → 已装的包 → PATH 上的 exe。
 * 找不到不算安装失败，但要把原因完整带出去 —— 让用户装完就知道缺什么。
 */
function resolveRuntime({ rpaCoreDir, python }) {
  const roots = sourceRootCandidates(rpaCoreDir);
  const pythons = pythonCandidates(python, roots);
  const attempts = [];
  let probes = 0;

  const tryPair = (py, src) => {
    if (probes >= MAX_PROBES) return null;
    probes += 1;
    const r = probeRpaCore(py, src);
    if (!r.ok) {
      attempts.push({ python: py, src: src || "(已安装的包)", detail: r.detail });
      return null;
    }
    return r;
  };

  for (const root of roots) {
    for (const py of pythons) {
      const r = tryPair(py.cmd, join(root, "src"));
      if (r) {
        return {
          ok: true,
          mode: "src",
          python: py.cmd,
          src: join(root, "src"),
          source_root: root,
          module: r.module,
          resolved_by: `${py.by}+src`,
          attempts,
        };
      }
    }
  }

  for (const py of pythons) {
    const r = tryPair(py.cmd, null);
    if (r) {
      return {
        ok: true,
        mode: "module",
        python: py.cmd,
        src: null,
        source_root: inferSourceRoot(r.module),
        module: r.module,
        resolved_by: `${py.by}+installed`,
        attempts,
      };
    }
  }

  const exe = findRpaCoreExe();
  if (exe) {
    return { ok: true, mode: "exe", python: null, exe, src: null, source_root: null, resolved_by: "PATH", attempts };
  }
  return { ok: false, attempts, searched: { source_roots: roots, pythons: pythons.map((p) => p.cmd) } };
}

function runtimeManifest(runtime) {
  return {
    schemaVersion: 1,
    installed_at: new Date().toISOString(),
    python: runtime.python || null,
    source_root: toPosix(runtime.source_root || "") || null,
    rpa_core: {
      mode: runtime.mode,
      src: toPosix(runtime.src || "") || null,
      exe: toPosix(runtime.exe || "") || null,
      module: toPosix(runtime.module || "") || null,
      resolved_by: runtime.resolved_by || null,
    },
    installer: {
      package: "rpa-workflow-studio",
      version: PKG_VERSION,
      package_root: toPosix(PKG_ROOT),
    },
  };
}

// ---------------------------------------------------------------------------
// install
// ---------------------------------------------------------------------------

function install(dest, { force, dryRun, rpaCoreDir, python }) {
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

  // 探测是只读的，dry-run 也照做 —— 用户最该在动手前知道的就是「运行时连不连得上」。
  const runtime = resolveRuntime({ rpaCoreDir, python });
  const runtimeReport = runtime.ok
    ? {
        ok: true,
        mode: runtime.mode,
        python: toPosix(runtime.python || runtime.exe || ""),
        source_root: toPosix(runtime.source_root || "") || null,
        resolved_by: runtime.resolved_by,
      }
    : {
        ok: false,
        error: "找不到可用的 rpa-core 运行时",
        attempts: runtime.attempts.slice(0, 8),
        searched: runtime.searched,
        fix: [
          "rpa-skill install --force --rpa-core-dir <rpa_core 仓库根>",
          "rpa-skill install --force --python <能 import rpa_core 的解释器>",
        ],
      };

  if (dryRun) {
    return {
      ...plan,
      runtime: runtimeReport,
      steps: ["copy", "探测运行时 → runtime.json", `替换 ${PLACEHOLDERS.join(" / ")}`, "write skill.json"],
    };
  }

  if (installed && force) {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    // 备份不能留在技能扫描目录里：宿主按 <skills>/<dir>/SKILL.md 找技能，
    // 一个 `xxx.replaced-<ts>` 目录就是**第二个同名技能**（实测 ~/.dsh/skills 里
    // 已经躺着这么一份，宿主会看到两个 workflow-studio）。所以挪进隐藏目录，
    // 并把里面的 SKILL.md 改名 —— 双保险：既不进 `*/` 这种 glob，也不会被
    // 「递归找 SKILL.md」的实现捡走。回滚时把它改回 SKILL.md 即可。
    const keptDir = join(dirname(dest), ".rpa-skill-backups");
    const kept = join(keptDir, `${basename(dest)}-${stamp}`);
    try {
      mkdirSync(keptDir, { recursive: true });
      renameSync(dest, kept);
      const staleSkill = join(kept, "SKILL.md");
      if (existsSync(staleSkill)) renameSync(staleSkill, join(kept, "SKILL.md.replaced"));
    } catch (err) {
      // Windows 上目录被别的进程按住（编辑器、杀软）时 rename 会失败。别把栈甩给用户：
      // 说清卡在哪一步、手动怎么收场，否则他既没装上、也读不懂那段 stack。
      fail(
        {
          error: `备份旧目录失败（${err.code || err.name}）：${err.message}`,
          previous: toPosix(dest),
          fix: [`手动把旧目录挪走：把 "${dest}" 移到别处`, "然后重跑：rpa-skill install --force"],
        },
        2,
      );
    }
    plan.previous_moved_to = toPosix(kept);
  }

  mkdirSync(dirname(dest), { recursive: true });
  cpSync(SRC, dest, {
    recursive: true,
    filter: (p) => !SKIP_NAMES.has(p.split(/[\\/]/).pop()),
  });

  if (runtime.ok) {
    writeFileSync(join(dest, "runtime.json"), JSON.stringify(runtimeManifest(runtime), null, 2) + "\n", "utf8");
  }

  // 占位符只在 .md 里出现；替换成安装后的绝对路径（正斜杠，bash 与 PowerShell 都吃）。
  // {{RPA_CORE}} 指向技能自带的 rc.py 而不是裸命令 rpa-core：rc.py 会按 runtime.json
  // 二次定位，换机器 / 换 venv / PATH 变了也还能找到，找不到时给的是可照抄的修法。
  const posixDest = toPosix(dest);
  const pythonCmd = runtime.python ? `"${toPosix(runtime.python)}"` : "python";
  // exe 形态（pipx / uv tool 装的 rpa-core，或 PATH 上本来就有的）：直接调那个
  // 可执行文件，不再绕 rc.py。绕一层要多要求一个 python —— 而「只有 exe、
  // 没有 python 在 PATH 上」正是这种形态存在的场景。
  const rpaCoreCmd =
    runtime.mode === "exe" && runtime.exe
      ? `"${toPosix(runtime.exe)}"`
      : `${pythonCmd} "${posixDest}/scripts/rc.py"`;
  const values = {
    "{{SKILL_DIR}}": posixDest,
    "{{PYTHON}}": pythonCmd,
    "{{RPA_CORE}}": rpaCoreCmd,
  };
  let replaced = 0;
  for (const file of walk(dest)) {
    if (!file.endsWith(".md")) continue;
    const text = readFileSync(file, "utf8");
    if (!PLACEHOLDERS.some((k) => text.includes(k))) continue;
    let next = text;
    for (const [k, v] of Object.entries(values)) next = next.split(k).join(v);
    writeFileSync(file, next, "utf8");
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

  const warnings = [];
  if (!runtime.ok) {
    warnings.push("技能装好了，但没接上 rpa-core 运行时：「生成流程」里的 catalog / validate 会失败。");
    warnings.push("修法：rpa-skill install --force --rpa-core-dir <rpa_core 仓库根>（或 --python <能 import rpa_core 的解释器>）");
    warnings.push("改完用 rpa-skill doctor 复核；浏览器拾取元素那条路不受影响。");
  }

  return {
    ...plan,
    runtime: runtimeReport,
    files_replaced: replaced,
    entry: toPosix(join(dest, "SKILL.md")),
    warnings,
    next: [
      runtime.ok
        ? `① 运行时已接好：${runtimeReport.mode} · ${runtimeReport.python}`
        : "① 先修运行时（见 warnings），否则生成流程会失败",
      "② 自检：rpa-skill doctor",
      "③ 需要可视化窗口时另装 PPX，或给 studio.py gui 传 --exe",
    ],
  };
}

// ---------------------------------------------------------------------------
// status / doctor / uninstall
// ---------------------------------------------------------------------------

function readRuntime(dest) {
  const p = join(dest, "runtime.json");
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return { broken: true };
  }
}

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
      row.unresolved_placeholder = md.reduce(
        (n, f) => n + (readFileSync(f, "utf8").match(PLACEHOLDER_RE) || []).length,
        0,
      );
      row.script_present = existsSync(join(dest, "scripts", "studio.py"));
      const rt = readRuntime(dest);
      row.runtime = rt
        ? {
            mode: rt.rpa_core?.mode || null,
            python: rt.python || null,
            python_present: Boolean(rt.python) && existsSync(rt.python),
            source_root: rt.source_root || null,
            source_root_present: Boolean(rt.source_root) && isSourceRoot(rt.source_root),
          }
        : null;
      row.runtime_ok = Boolean(rt && !rt.broken && (row.runtime.python_present || rt.rpa_core?.mode === "exe"));
      row.healthy = row.unresolved_placeholder === 0 && row.script_present && row.runtime_ok;
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

function runQuiet(cmd, args) {
  try {
    const out = execFileSync(cmd, args, {
      encoding: "utf8",
      timeout: 60000,
      stdio: ["ignore", "pipe", "pipe"],
      env: PY_ENV,
    });
    return { ok: true, out: out.trim() };
  } catch (err) {
    const tail = String(err.stdout || err.stderr || "").trim().split(/\r?\n/).slice(-3).join(" ");
    return { ok: false, out: tail || `退出码 ${err.status}` };
  }
}

/**
 * 一条命令回答「装了为什么跑不起来」。装完就该跑一次；
 * 之后任何「catalog 调不通」「窗口打不开」都先看它，别靠猜。
 */
function doctorWith(dest) {
  const checks = [];
  const add = (name, ok, detail, fix) => checks.push({ name, ok: Boolean(ok), detail, ...(fix ? { fix } : {}) });

  const major = Number(process.versions.node.split(".")[0]);
  add("node", major >= 18, process.versions.node, "装 node 18.17+");

  add("skill", existsSync(join(dest, "SKILL.md")), toPosix(dest));

  const md = existsSync(join(dest, "SKILL.md")) ? walk(dest).filter((f) => f.endsWith(".md")) : [];
  const leftover = md.reduce((n, f) => n + (readFileSync(f, "utf8").match(PLACEHOLDER_RE) || []).length, 0);
  add(
    "placeholders",
    leftover === 0,
    leftover === 0 ? "全部已替换" : `还剩 ${leftover} 处 {{…}}`,
    "重装：rpa-skill install --force",
  );

  add("rc.py", existsSync(join(dest, "scripts", "rc.py")), "找 rpa-core 的入口垫片");
  add("studio.py", existsSync(join(dest, "scripts", "studio.py")), "会话服务");

  const rt = readRuntime(dest);
  if (!rt) {
    // 「没写」是安装那一刻的快照，不是现在的结论 —— 常见的时序是先装技能、后补运行时
    // （uv tool install 之类），这时正确修法只是重装一次，而不是让人去翻仓库根。
    // 所以这里现场再探一遍，按探到的结果给修法。
    const probed = resolveRuntime({});
    if (probed.ok) {
      add(
        "runtime.json",
        false,
        `没写，但这台上现在找得到运行时（${probed.mode} · ${probed.resolved_by}）`,
        "重装一次就会写进去：rpa-skill install --force",
      );
    } else {
      const py = probed.searched?.pythons?.length ?? 0;
      const roots = probed.searched?.source_roots?.length ?? 0;
      add(
        "runtime.json",
        false,
        `没写，且现场探测也没找到（试过 ${py} 个解释器 / ${roots} 个源码根）`,
        "rpa-skill install --force --rpa-core-dir <仓库根>",
      );
    }
  } else if (rt.broken) {
    add("runtime.json", false, "内容不是合法 JSON", "删掉后重装");
  } else {
    const rc = rt.rpa_core || {};
    add("runtime.json", true, `${rc.mode || "?"} · ${rc.resolved_by || "?"}`);
    const pyOk = Boolean(rt.python) && existsSync(rt.python);
    add("interpreter", pyOk || rc.mode === "exe", rt.python || rc.exe || "(未记录)", "rpa-skill install --force --python <解释器>");
    // source root 只有「跑的是源码树」时才必须有。pip 装进 site-packages、
    // 或 PATH 上的 rpa-core.exe，本来就没有源码树 —— 对它们要求 source root
    // 会造出假红，还会给一条「去装源码树」的误导修法（实测：module 形态下
    // import 与 rc.py 都绿，唯独这条红）。
    if (rc.mode === "src") {
      add(
        "source root",
        Boolean(rt.source_root) && isSourceRoot(rt.source_root),
        rt.source_root || "(未记录)",
        "rpa-skill install --force --rpa-core-dir <仓库根>",
      );
    } else {
      add("source root", true, `(不适用：${rc.mode === "exe" ? "PATH 上的 rpa-core" : "已装进解释器"})`);
    }
    if (pyOk) {
      const probe = runQuiet(rt.python, ["-c", PROBE_SRC, rc.src || "-"]);
      add("import rpa_core", probe.ok, probe.ok ? probe.out.replace(/^OK\s*/, "") : probe.out, "让 runtime.json 里的解释器能 import rpa_core");
      if (probe.ok) {
        const call = runQuiet(rt.python, [join(dest, "scripts", "rc.py"), "--help"]);
        add("rc.py 调通 rpa-core", call.ok, call.ok ? "可执行" : call.out.split(/\r?\n/)[0]);
      }
    } else if (rc.mode === "exe" && rc.exe) {
      // exe 形态没有解释器可 import，唯一能证的就是「那个 exe 真的跑得起来」。
      const call = runQuiet(rc.exe, ["--help"]);
      add("rpa-core 可执行", call.ok, call.ok ? rc.exe : String(call.out).split(/\r?\n/)[0], "重装 rpa-core");
    }
  }

  const failed = checks.filter((c) => !c.ok);
  return {
    ok: failed.length === 0,
    action: "doctor",
    target: toPosix(dest),
    checks,
    next:
      failed.length === 0
        ? ["可以用了：生成流程时用 SKILL.md 里的现成命令（已指向安装后的绝对路径）"]
        : failed.filter((c) => c.fix).map((c) => `${c.name}：${c.fix}`),
  };
}

function doctor({ target, dir }) {
  const explicit = dir ? resolve(dir) : target ? targetDirFor(target) : null;
  if (explicit && existsSync(join(explicit, "SKILL.md"))) return doctorWith(explicit);
  if (explicit) {
    fail({ error: `这个目录里没有已安装的技能：${toPosix(explicit)}`, action: "doctor" }, 2);
  }
  const installed = Object.keys(HOSTS)
    .map((h) => targetDirFor(h))
    .filter((d) => existsSync(join(d, "SKILL.md")));
  if (installed.length === 0) {
    fail({ error: "本机没找到已安装的技能；先 rpa-skill install", action: "doctor" }, 2);
  }
  return doctorWith(installed[0]);
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

const HELP = `rpa-skill ${PKG_VERSION}

用法：
  rpa-skill install [--target dsh|claude|agents|gemini|all] [--dir PATH] [--force] [--dry-run]
                    [--rpa-core-dir PATH] [--python PATH]
  rpa-skill doctor  [--target dsh|claude|agents|gemini] [--dir PATH]
  rpa-skill status
  rpa-skill uninstall [--target dsh|claude|agents|gemini] [--dir PATH]

目标目录：
  dsh      ~/.dsh/skills/workflow-studio
  claude   ~/.claude/skills/workflow-studio
  agents   ~/.agents/skills/workflow-studio      （Codex 等跨宿主共享）
  gemini   ~/.gemini/config/skills/workflow-studio

不指定 --target 时自动装到本机已存在的宿主；--dir 可装到任意目录（CI / 测试用）。

运行时（install 会自动探测，探不到才需要手工指定）：
  --rpa-core-dir  rpa_core 源码仓库根（含 src/rpa_core/cli.py）
  --python        能 import rpa_core 的解释器（通常是仓库的 .venv）
  也可用环境变量 RPA_CORE_HOME / RPA_CORE_PYTHON。
`;

function main() {
  const argv = process.argv.slice(2);
  // --help 可能出现在任何位置，也可能就是唯一参数 —— 先于子命令解析处理掉。
  if (argv.length === 0 || argv.includes("--help") || argv.includes("-h")) {
    process.stdout.write(HELP);
    return;
  }
  const cmd = argv[0];
  const flags = { force: false, dryRun: false, target: null, dir: null, rpaCoreDir: null, python: null };
  for (let i = 1; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--force") flags.force = true;
    else if (a === "--dry-run") flags.dryRun = true;
    else if (a === "--target") flags.target = argv[++i];
    else if (a === "--dir") flags.dir = argv[++i];
    else if (a === "--rpa-core-dir") flags.rpaCoreDir = argv[++i];
    else if (a === "--python") flags.python = argv[++i];
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

  if (cmd === "doctor") {
    const payload = doctor({ target: flags.target, dir: flags.dir });
    console.log(JSON.stringify(payload, null, 2));
    if (!payload.ok) process.exit(1);
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
    const single = results.length === 1 ? results[0] : null;
    console.log(JSON.stringify(single || { ok: failed.length === 0, results }, null, 2));

    // 装成功但运行时没接上：这条必须显眼，否则用户会在「要流程」的时候才撞墙。
    const warn = (single ? single.warnings : results.flatMap((r) => r.warnings || [])) || [];
    if (warn.length) process.stderr.write(`\n⚠ ${warn[0]}\n  ${warn[1] || ""}\n\n`);
    if (failed.length) process.exit(1);
    return;
  }

  fail({ error: `未知子命令：${cmd}`, hint: "rpa-skill --help" }, 2);
}

main();
