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
import { createHash } from "node:crypto";
import {
  copyFileSync,
  cpSync,
  createWriteStream,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { delimiter, basename, dirname, join, resolve, sep } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";

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

// --- 编辑窗口（可选件）-----------------------------------------------------
//
// 窗口是一个 PPX 应用，构建产物约 16MB，不进 npm 包（否则每个 Linux/mac 用户
// 白下 16MB，而它只有 Windows 产物）。所以走 GitHub Release：安装时按需下载。
//
// 三个都能用环境变量覆盖，测试与内网镜像都靠它 —— 判据要能在没有网络的情况下
// 真跑到「下载/校验/解压/接线」每一步，否则只能证明「参数解析没崩」。
const EDITOR_ASSET = "RPA-Editor-win64.zip";
const EDITOR_REPO = process.env.RPA_EDITOR_REPO || "liqt91/dsh_rpa_core";
const EDITOR_DEFAULT_URL = `https://github.com/${EDITOR_REPO}/releases/latest/download/${EDITOR_ASSET}`;
const EDITOR_PLATFORM = process.platform === "win32";
// 清单文件名与 api.py 的 `_launcher()` 对齐：那边只认 editor.json 里的
// rpa-core / python / workspace 三个键，这里多写 editor / version / sha256 供
// studio.py 与 doctor 用 —— 一个文件，两个消费者各读自己关心的键。
const EDITOR_MANIFEST_NAME = "editor.json";

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
// 编辑窗口（可选件，走 GitHub Release）
// ---------------------------------------------------------------------------

// 运行时数据目录。**不要用 RPA_CORE_HOME** —— 那个名字在 skills/scripts/_runtime.py
// 里已经表示「rpa_core 源码根」，一个变量两种含义必然互相打脸。
// 三处必须对齐：这里、api.py 的 `_launcher()`、studio.py 的 `_core_home()`。
const RPA_HOME_ENV = "RPA_HOME";

function coreHome(explicit) {
  return resolve(explicit || process.env[RPA_HOME_ENV] || join(homedir(), ".rpa-core"));
}

function editorManifestPath(home) {
  return join(home, EDITOR_MANIFEST_NAME);
}

function readEditorManifest(home) {
  const p = editorManifestPath(home);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return { broken: true };
  }
}

function sha256File(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

/**
 * 清临时文件。清理失败不能把一次成功的安装变成失败 —— 临时文件留着，下次会被覆盖。
 * 也别用固定的 `.staging` 目录：万一上次没清干净，残留文件会被一起搬进 current。
 */
function cleanupPath(path, recursive = false) {
  try {
    if (existsSync(path)) rmSync(path, { recursive, force: true });
  } catch {
    // 只记在结果里由调用方决定要不要提醒；这里吞掉是为了不让 finally 抛异常盖住真错误。
  }
}

/** 窗口只有 Windows 产物（pythonnet / PPX 都只在那边验证过）。别假装通用。 */
function editorUnsupported(extra = {}) {
  return {
    ok: false,
    skip: true,
    code: "EDITOR_UNSUPPORTED",
    detail: `这套窗口只有 Windows 产物（当前平台 ${process.platform}）`,
    fix: ["在 Windows 上装：rpa-skill install --with-editor", "或继续不用窗口：改文件 + rpa-core capture"],
    ...extra,
  };
}

async function downloadFile(url, dest) {
  let res;
  try {
    res = await fetch(url, { redirect: "follow", headers: { "user-agent": `rpa-skill/${PKG_VERSION}` } });
  } catch (err) {
    const cause = err.cause?.code || err.cause?.message || err.message;
    throw new Error(`下载失败：${url}（${cause}）`);
  }
  if (!res.ok) {
    throw new Error(`下载失败：HTTP ${res.status} ${url}`);
  }
  mkdirSync(dirname(dest), { recursive: true });
  await pipeline(Readable.fromWeb(res.body), createWriteStream(dest));
  return dest;
}

/**
 * 解压 zip —— 自己按中央目录解，不调用任何外部程序。
 *
 * 为什么不用 `tar` / `Expand-Archive`：PATH 上的 `tar` 可能是 Git for Windows 的
 * GNU tar（不认 zip，实测报 "This does not look like a tar archive"），
 * 而 `powershell -Command "..." arg1 arg2` 的额外参数并不会进 $args（实测报
 * 「无法对参数 LiteralPath 执行绑定，因为该参数为 Null」）。两者都取决于用户机器上
 * 装了什么，出错还要靠猜。zip 格式本身很简单（中央目录 + deflate），自己解最确定。
 *
 * 只支持本套件产物会出现的情况：store / deflate、无 zip64、无加密。
 */
function unzipTo(zipPath, dest) {
  const buf = readFileSync(zipPath);
  let eocd = -1;
  const floor = Math.max(0, buf.length - 65557);
  for (let i = buf.length - 22; i >= floor; i -= 1) {
    if (buf.readUInt32LE(i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error("不是有效的 zip（找不到中央目录）");
  const count = buf.readUInt16LE(eocd + 10);
  let ptr = buf.readUInt32LE(eocd + 16);
  const root = resolve(dest);
  let files = 0;
  for (let i = 0; i < count; i += 1) {
    if (ptr + 46 > buf.length || buf.readUInt32LE(ptr) !== 0x02014b50) {
      throw new Error(`zip 中央目录损坏（第 ${i + 1} 项）`);
    }
    const method = buf.readUInt16LE(ptr + 10);
    const compSize = buf.readUInt32LE(ptr + 20);
    const nameLen = buf.readUInt16LE(ptr + 28);
    const extraLen = buf.readUInt16LE(ptr + 30);
    const commentLen = buf.readUInt16LE(ptr + 32);
    const localOff = buf.readUInt32LE(ptr + 42);
    const name = buf.toString("utf8", ptr + 46, ptr + 46 + nameLen);
    ptr += 46 + nameLen + extraLen + commentLen;

    if (name.startsWith("__MACOSX/")) continue;
    const target = resolve(root, name);
    if (target !== root && !target.startsWith(root + sep)) {
      throw new Error(`zip 里有越界路径：${name}`);
    }
    if (name.endsWith("/")) {
      mkdirSync(target, { recursive: true });
      continue;
    }
    const localNameLen = buf.readUInt16LE(localOff + 26);
    const localExtraLen = buf.readUInt16LE(localOff + 28);
    const dataOff = localOff + 30 + localNameLen + localExtraLen;
    const raw = buf.subarray(dataOff, dataOff + compSize);
    let out;
    if (method === 0) out = raw;
    else if (method === 8) out = inflateRawSync(raw);
    else throw new Error(`不支持的压缩方式 ${method}：${name}`);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, out);
    files += 1;
  }
  return { via: "内置解压", files };
}

/** 在解压结果里找窗口 exe。不硬编码产品名 —— 换名字不该让安装器失效。 */
function findEditorExe(dir, depth = 0) {
  if (depth > 3) return null;
  let entries = [];
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return null;
  }
  for (const e of entries) {
    if (e.isFile() && /\.exe$/i.test(e.name)) return join(dir, e.name);
  }
  for (const e of entries) {
    // `_internal` 里是 Python 运行时与 dll，翻它只会找到别的 exe。
    if (!e.isDirectory() || e.name === "_internal" || e.name.startsWith(".")) continue;
    const found = findEditorExe(join(dir, e.name), depth + 1);
    if (found) return found;
  }
  return null;
}

/** 版本从**包内**读：release 的资源名是固定的（latest/download 不查 API），
 * 文件名里没有版本号。顺带当结构判据：随手塞个 exe 的 zip 不该被当成窗口装上。 */
function versionFromPayload(staging) {
  for (const p of walk(staging)) {
    if (basename(p) !== "editor-version.json") continue;
    try {
      const data = JSON.parse(readFileSync(p, "utf8"));
      if (data && typeof data.version === "string" && /^\d+\.\d+\.\d+/.test(data.version)) {
        return data.version;
      }
    } catch {
      // 继续找下一个；一个坏的同名文件不该让整包直接装上。
    }
  }
  return null;
}

/**
 * 装窗口：下载（或复制本地 zip）→ 校验 → 解压 → 落到 <home>/editor/current → 写 editor.json。
 *
 * 为什么是 `current` 而不是 `<版本>/`：exe 里的相对资源靠同级目录，版本目录会让
 * 「升级后旧目录还在」变成一个需要人清理的负担；保留一份 `previous-*` 就够回滚。
 */
async function installEditor(options = {}) {
  const home = coreHome(options.home);
  const runtime = options.runtime || resolveRuntime({});
  const base = join(home, "editor");

  if (!EDITOR_PLATFORM && !options.zip) return editorUnsupported({ home: toPosix(home) });
  if (!EDITOR_PLATFORM && options.zip) {
    // 本地 zip 也不假装能跑：产物是 win64，装上去也起不来。
    return editorUnsupported({ home: toPosix(home) });
  }

  const source = options.zip ? resolve(options.zip) : options.url || EDITOR_DEFAULT_URL;
  if (options.zip && !existsSync(source)) {
    return {
      ok: false,
      code: "EDITOR_ZIP_MISSING",
      error: `找不到本地窗口包：${source}`,
      fix: ["检查 --editor-zip 的路径", "或不给该参数，直接从 Release 下载"],
    };
  }

  mkdirSync(base, { recursive: true });
  const staged = join(base, ".download.zip");
  const staging = join(base, `.staging-${process.pid}-${Date.now()}`);
  try {
    if (options.zip) {
      copyFileSync(source, staged);
    } else {
      await downloadFile(source, staged);
    }
  } catch (err) {
    const message = String(err.message || err);
    const notPublished = /HTTP 404/.test(message);
    return {
      ok: false,
      code: "EDITOR_DOWNLOAD_FAILED",
      error: message,
      fix: notPublished
        ? [
            `Release 里还没有 ${EDITOR_ASSET}：维护者先跑 build_editor.py --release，再 gh release create 传 zip + .sha256（见 README）`,
            "等不及就用离线包：--editor-zip <本地 zip>",
            "技能本身已经装好了，窗口可以以后再补：rpa-skill editor install",
          ]
        : [
            `检查网络能不能打开 ${EDITOR_DEFAULT_URL}`,
            "内网镜像：--editor-url <zip 地址>；已有离线包：--editor-zip <path>",
            "技能本身已经装好了，窗口可以以后再补：rpa-skill editor install",
          ],
    };
  }

  // 校验：显式 --sha256 或同目录的 .sha256 旁挂文件。两者都没有就照装，但把
  // 「没校验」写进结果 —— 不静默。
  const digest = sha256File(staged);
  let expected = options.sha256 ? String(options.sha256).trim().toLowerCase() : null;
  let shaOrigin = expected ? "explicit" : null;
  if (!expected && options.zip) {
    const sidecar = `${source}.sha256`;
    if (existsSync(sidecar)) {
      expected = readFileSync(sidecar, "utf8").trim().split(/\s+/)[0].toLowerCase();
      shaOrigin = "sidecar";
    }
  }
  if (!expected && !options.zip) {
    try {
      const sidecarUrl = `${source}.sha256`;
      const res = await fetch(sidecarUrl, { redirect: "follow" });
      if (res.ok) {
        expected = (await res.text()).trim().split(/\s+/)[0].toLowerCase();
        shaOrigin = "sidecar";
      }
    } catch {
      // 取不到旁挂文件不阻断：老 release 没附它。
    }
  }
  if (expected && expected !== digest) {
    cleanupPath(staged);
    return {
      ok: false,
      code: "EDITOR_SHA_MISMATCH",
      error: `窗口包校验不通过：期望 ${expected.slice(0, 12)}…，实际 ${digest.slice(0, 12)}…`,
      fix: ["重新下载（可能是代理返回了错误页）", "或从 release 页手工下载后 --editor-zip <path>"],
    };
  }

  try {
    const how = unzipTo(staged, staging);
    const exe = findEditorExe(staging);
    const version = versionFromPayload(staging);
    if (!exe || !version) {
      const what = !exe ? "没有 .exe" : "没有 editor-version.json（版本清单）";
      return {
        ok: false,
        code: "EDITOR_LAYOUT",
        error: `解压出来的包里${what}（${how.via}）`,
        fix: [
          "确认下的是 RPA-Editor-win64.zip，而不是源码包或随便一个 zip",
          `重新取包：${EDITOR_DEFAULT_URL}`,
        ],
      };
    }

    const current = join(base, "current");
    if (existsSync(current)) {
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      renameSync(current, join(base, `previous-${stamp}`));
      // 只留一份回滚：每份 35MB，攒三次就是 100MB。
      for (const name of readdirSync(base)) {
        if (name.startsWith("previous-") && name !== `previous-${stamp}`) {
          cleanupPath(join(base, name), true);
        }
      }
    }
    renameSync(staging, current);
    cleanupPath(staged);

    const relExe = toPosix(exe.slice(staging.length + 1));
    const manifest = {
      schemaVersion: 1,
      version,
      editor: toPosix(join(current, relExe)),
      workspace: toPosix(join(home, "workspace")),
      installed_at: new Date().toISOString(),
      sha256: digest,
      sha_verified: shaOrigin || "none",
      source: toPosix(source),
    };
    // 运行时两选一：exe 形态直指 exe；否则写解释器（api.py 会退回 python -m）。
    if (runtime.ok && runtime.mode === "exe" && runtime.exe) {
      manifest["rpa-core"] = toPosix(runtime.exe);
    } else if (runtime.ok && runtime.python) {
      manifest.python = toPosix(runtime.python);
    }
    mkdirSync(join(home, "workspace", "workflows"), { recursive: true });
    writeFileSync(editorManifestPath(home), JSON.stringify(manifest, null, 2) + "\n", "utf8");

    const warnings = [];
    if (!manifest["rpa-core"] && !manifest.python) {
      warnings.push("窗口装好了，但没写运行时：窗口里点「运行」会失败。修法：rpa-skill install --force");
    }
    if (shaOrigin === "none") warnings.push("这个窗口包没有 sha256 可校（老 release 或本地包），按信任处理。");
    if (/[^\x00-\x7F]/.test(manifest.editor)) {
      // 实测过的坑：路径里的中文会让 pythonnet 加载 Python.Runtime.dll 时崩掉。
      warnings.push(
        `窗口路径含非 ASCII 字符，可能启动失败：${manifest.editor}` +
          "。改到纯英文目录：rpa-skill editor install --editor-home D:\\rpa-core",
      );
    }
    return {
      ok: true,
      action: "editor",
      home: toPosix(home),
      exe: manifest.editor,
      version: manifest.version,
      sha256: digest,
      sha_verified: manifest.sha_verified,
      bytes: statSync(manifest.editor).size,
      warnings,
      next: [`打开：studio.py gui`, `自检：rpa-skill doctor`],
    };
  } catch (err) {
    return {
      ok: false,
      code: "EDITOR_INSTALL_FAILED",
      error: String(err.message || err),
      fix: ["重试一次；仍失败就把完整报错贴出来", "窗口是可选的：不装也能生成流程、跑流程"],
    };
  } finally {
    cleanupPath(staging, true);
    cleanupPath(staged);
  }
}

function editorStatus(home) {
  const root = coreHome(home);
  const manifest = readEditorManifest(root);
  if (!manifest) {
    return {
      ok: true,
      installed: false,
      home: toPosix(root),
      detail: EDITOR_PLATFORM ? "没装（可选件）" : "本平台没有窗口产物",
      install: EDITOR_PLATFORM ? "rpa-skill editor install" : null,
    };
  }
  if (manifest.broken) {
    return { ok: false, installed: false, home: toPosix(root), error: "editor.json 不是合法 JSON", fix: ["删掉它后重装：rpa-skill editor install --force"] };
  }
  const exe = manifest.editor;
  const alive = Boolean(exe) && existsSync(exe);
  return {
    ok: alive,
    installed: true,
    home: toPosix(root),
    exe: exe || null,
    version: manifest.version || null,
    sha256: manifest.sha256 || null,
    sha_verified: manifest.sha_verified || null,
    workspace: manifest.workspace || null,
    detail: alive ? "已装" : "清单在，但 exe 不在了",
    fix: alive ? null : ["重装：rpa-skill editor install --force"],
  };
}

function uninstallEditor(home) {
  const root = coreHome(home);
  const base = join(root, "editor");
  if (!existsSync(base)) return { ok: true, action: "editor", removed: null, detail: "本来就没装" };
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const kept = join(root, `editor.removed-${stamp}`);
  renameSync(base, kept);
  cleanupPath(editorManifestPath(root));
  return { ok: true, action: "editor", moved_to: toPosix(kept), detail: "挪走而不是删除；确认不要了再手动删" };
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
      "③ 要在窗口里拼流程：rpa-skill editor install（Windows；也可重装时加 --with-editor）",
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
    editor: editorStatus(),
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
  // ok 三态：true 通过 / false 失败 / null 不适用（SKIP）。窗口是可选项，
  // 没装不算坏 —— 把它记成失败会让人一上来就去修一个本来不需要的东西。
  const add = (name, ok, detail, fix) =>
    checks.push({ name, ok: ok === null ? null : Boolean(ok), detail, ...(fix ? { fix } : {}) });

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

  // 编辑窗口：可选件。装了就要真能用（exe 在、路径是 ASCII），没装是 SKIP。
  const est = editorStatus();
  if (!est.installed) {
    add("editor", null, est.detail, est.install || undefined);
  } else {
    add("editor", est.ok, est.ok ? `${est.version || "?"} · ${est.exe}` : est.detail, est.fix?.[0]);
    if (est.ok && /[^\x00-\x7F]/.test(est.exe || "")) {
      // 实测：中文路径下 pythonnet 加载 Python.Runtime.dll 会直接失败，窗口起不来。
      add(
        "editor path",
        false,
        `含非 ASCII 字符：${est.exe}`,
        "rpa-skill editor install --editor-home D:\\rpa-core",
      );
    }
  }

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

  const failed = checks.filter((c) => c.ok === false);
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
                    [--rpa-core-dir PATH] [--python PATH] [--with-editor]
  rpa-skill doctor  [--target dsh|claude|agents|gemini] [--dir PATH]
  rpa-skill status
  rpa-skill editor  [install|status|uninstall] [--editor-zip PATH] [--editor-url URL]
                    [--sha256 HEX] [--editor-home PATH] [--force]
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

编辑窗口（可选件，只有 Windows 产物）：
  --with-editor  装完技能顺手装窗口（从 GitHub Release 下载 ~16MB）
  --editor-zip   用本地 zip 代替下载（离线 / 内网 / 测试）
  --editor-url   换下载地址（默认 ${EDITOR_DEFAULT_URL}）
  --sha256       手工指定校验值（不给就读 release 的 .sha256 旁挂文件）
  --editor-home  窗口装到哪（默认 ~/.rpa-core，路径别带中文）
  装到：<editor-home>/editor/current/，清单：<editor-home>/editor.json
  只装窗口、不动技能：rpa-skill editor install
`;

async function main() {
  const argv = process.argv.slice(2);
  // --help 可能出现在任何位置，也可能就是唯一参数 —— 先于子命令解析处理掉。
  if (argv.length === 0 || argv.includes("--help") || argv.includes("-h")) {
    process.stdout.write(HELP);
    return;
  }
  const cmd = argv[0];
  const flags = {
    force: false,
    dryRun: false,
    target: null,
    dir: null,
    rpaCoreDir: null,
    python: null,
    withEditor: false,
    editorZip: null,
    editorUrl: null,
    editorSha: null,
    editorHome: null,
  };
  for (let i = 1; i < argv.length; i += 1) {
    const a = argv[i];
    // `editor install` 的第二段是子命令，不是选项 —— 别当成未知参数打回去。
    if (i === 1 && cmd === "editor" && !a.startsWith("-")) continue;
    if (a === "--force") flags.force = true;
    else if (a === "--dry-run") flags.dryRun = true;
    else if (a === "--with-editor") flags.withEditor = true;
    else if (a === "--target") flags.target = argv[++i];
    else if (a === "--dir") flags.dir = argv[++i];
    else if (a === "--rpa-core-dir") flags.rpaCoreDir = argv[++i];
    else if (a === "--python") flags.python = argv[++i];
    else if (a === "--editor-zip") flags.editorZip = argv[++i];
    else if (a === "--editor-url") flags.editorUrl = argv[++i];
    else if (a === "--sha256") flags.editorSha = argv[++i];
    else if (a === "--editor-home") flags.editorHome = argv[++i];
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

  if (cmd === "editor") {
    // 二级子命令：install / status / uninstall。默认 status（只读）。
    const sub = argv[1] && !argv[1].startsWith("-") ? argv[1] : "status";
    if (sub === "status") {
      const payload = editorStatus(flags.editorHome);
      console.log(JSON.stringify(payload, null, 2));
      if (!payload.ok) process.exit(1);
      return;
    }
    if (sub === "uninstall") {
      console.log(JSON.stringify(uninstallEditor(flags.editorHome), null, 2));
      return;
    }
    if (sub !== "install") fail({ error: `未知子命令：editor ${sub}`, hint: "rpa-skill editor [install|status|uninstall]" }, 2);
    const payload = await installEditor({
      home: flags.editorHome,
      zip: flags.editorZip || process.env.RPA_EDITOR_ZIP,
      url: flags.editorUrl || process.env.RPA_EDITOR_URL,
      sha256: flags.editorSha,
    });
    console.log(JSON.stringify(payload, null, 2));
    if (payload.skip) return;
    if (!payload.ok) process.exit(1);
    for (const w of payload.warnings || []) process.stderr.write(`\n⚠ ${w}\n`);
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

    // 窗口是可选件：装它失败不算技能装失败（技能已经能生成/跑流程），
    // 只是结果里带 ok:false 与修法。反过来，技能装失败时不装窗口 —— 没有运行时的
    // 窗口只能开不能跑，装它只会让人以为问题出在窗口上。
    if (flags.withEditor) {
      const anchor = results.find((r) => r.ok);
      let editor = null;
      if (!anchor) {
        editor = { ok: false, code: "SKIPPED", detail: "技能没装成功，跳过窗口" };
      } else if (flags.dryRun) {
        editor = { ok: true, dryRun: true, detail: `将下载并解压到 ${toPosix(join(coreHome(flags.editorHome), "editor", "current"))}` };
      } else {
        editor = await installEditor({
          home: flags.editorHome,
          zip: flags.editorZip || process.env.RPA_EDITOR_ZIP,
          url: flags.editorUrl || process.env.RPA_EDITOR_URL,
          sha256: flags.editorSha,
        });
      }
      for (const r of results) r.editor = editor;
      if (editor && !editor.ok && !editor.skip) {
        for (const r of results) {
          r.warnings = [...(r.warnings || []), `编辑窗口没装上：${editor.error || editor.detail}`, "补装：rpa-skill editor install"];
        }
      }
    }

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

main().catch((err) => {
  fail({ error: String(err?.stack || err) }, 2);
});
