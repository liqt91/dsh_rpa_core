#!/usr/bin/env node
/**
 * 安装器冒烟：把技能装到临时目录，断言它真的可用，然后清掉。
 *
 * 不进 check_all（它要写磁盘、要起 node 子进程），手动跑：
 *   node workbuddy-suite/verify/skill_install_test.mjs
 *
 * 断言的是「装完能不能用」，不是「文件存在」——agent 的 cwd 通常是项目根，
 * 所以最关键的一条是占位符必须被换成绝对路径。
 */

import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const CLI = join(REPO, "bin", "rpa-skill.mjs");

const root = mkdtempSync(join(tmpdir(), "rpa-skill-"));
const dest = join(root, "workflow-studio");
const results = [];

function run(args) {
  try {
    const out = execFileSync(process.execPath, [CLI, ...args], { encoding: "utf8" });
    return { code: 0, out };
  } catch (err) {
    return { code: err.status ?? 1, out: String(err.stdout || "") };
  }
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

let installed = null;

check("install 返回 ok", () => {
  const r = run(["install", "--dir", dest]);
  assert(r.code === 0, `rc=${r.code} out=${r.out.slice(0, 300)}`);
  installed = JSON.parse(r.out);
  assert(installed.ok === true, `ok=${installed.ok}`);
  return `files_replaced=${installed.files_replaced}`;
});

check("拷贝了完整技能树", () => {
  const need = ["SKILL.md", "skill.json", "references/assembly.md", "scripts/studio.py"];
  const missing = need.filter((p) => !existsSync(join(dest, p)));
  assert(missing.length === 0, `缺 ${missing.join(", ")}`);
  return `${need.length} 个必需文件齐`;
});

check("占位符替换成安装后的绝对路径", () => {
  const md = readFileSync(join(dest, "SKILL.md"), "utf8");
  assert(!md.includes("{{SKILL_DIR}}"), "仍有未替换的占位符");
  const shown = dest.replace(/\\/g, "/");
  assert(md.includes(`python "${shown}/scripts/studio.py"`), "命令行里没有绝对路径");
  return "SKILL.md 里的 studio.py 指向安装目录";
});

check("skill.json 可解析且字段齐全", () => {
  const m = JSON.parse(readFileSync(join(dest, "skill.json"), "utf8"));
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

check("重复安装被拒绝（幂等）", () => {
  const r = run(["install", "--dir", dest]);
  assert(r.code !== 0, "重复安装竟然返回 0");
  const payload = JSON.parse(r.out);
  assert(payload.code === "ALREADY_INSTALLED", `code=${payload.code}`);
  return "rc≠0 + ALREADY_INSTALLED";
});

check("--force 覆盖时旧目录改名保留（不删除）", () => {
  const r = run(["install", "--dir", dest, "--force"]);
  assert(r.code === 0, `rc=${r.code}`);
  const payload = JSON.parse(r.out);
  const kept = String(payload.previous_moved_to || "").replace(/\//g, "\\");
  assert(kept && existsSync(kept), `旧目录没保留：${kept}`);
  return `保留为 ${kept.split("\\").pop()}`;
});

check("dry-run 不落盘", () => {
  const ghost = join(root, "ghost");
  const r = run(["install", "--dir", ghost, "--dry-run"]);
  assert(r.code === 0, `rc=${r.code}`);
  assert(!existsSync(ghost), "dry-run 竟然创建了目录");
  return "只报计划";
});

check("status 能读到已装目录", () => {
  const r = run(["status"]);
  assert(r.code === 0, `rc=${r.code}`);
  const payload = JSON.parse(r.out);
  assert(Array.isArray(payload.skills), "skills 不是数组");
  return `${payload.skills.length} 个宿主被探测`;
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
