"""编辑窗口安装器（rpa-skill editor / --with-editor）的判据验证 + 负向验证。

为什么要有这份脚本：`install --with-editor` 是「下载 → 校验 → 解压 → 落盘 → 写清单」
一条链，任何一环写松了都会「装完看起来成功、用起来炸」。之前踩过的假判据有：

  * 只看「安装了」不看「打开对不对」—— 解压时丢文件也能过；
  * 只断言 `ok: true` 而不看 editor.json 的键 —— 少了 workspace 就得等用户点运行才发现；
  * 把「没装窗口」记成失败 —— 窗口是可选件，那会让人去修一个本来不需要的东西。

用法（仓库根，用仓库的 venv）：

    ./.venv/Scripts/python.exe .harness/spike/probe_editor_install.py
    ./.venv/Scripts/python.exe .harness/spike/probe_editor_install.py --inject no-sha-check

注入模式下：先改坏被测代码 → 跑同一批用例 → **必须至少有一条红**（否则说明判据是假的）
→ 无论结果如何都在 finally 里按 md5 逐字节还原。

前置：产物 `dist/RPA-Editor-win64.zip`（`.harness/scripts/build_editor.py --release`）。
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node") or "node"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = Path(sys.executable)
ZIP = ROOT / "dist" / "RPA-Editor-win64.zip"
STUDIO = ROOT / "workbuddy-suite" / "skills" / "workflow-studio" / "scripts" / "studio.py"
INSTALLER = ROOT / "bin" / "rpa-skill.mjs"

# 必须落在**系统临时目录**：① 纯 ASCII（中文路径会让窗口的 pythonnet 崩）；
# ② 本机 WorkBuddy 的删除守卫对系统临时目录豁免，安装器清理 .staging/.download.zip
#    才能真的删掉，而不是被回收站包装成 EPERM 盖住真实结果。
# 注意 Python 的 tempfile.gettempdir() 在沙箱里被指到仓库根，不能直接用。
_TEMP_ROOT = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "C:/Windows/Temp")
TMP = Path(os.environ["PROBE_TMP"]) if os.environ.get("PROBE_TMP") else _TEMP_ROOT / "rpa-probe-editor"
HOME = TMP / "home"
SKILL_DIR = TMP / "skill"
SENTINEL = TMP / ".probe-injected"

results: list[tuple[str, bool, str]] = []
injected: list[Path] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    return bool(ok)


# ---------------------------------------------------------------------------
# 调用封装
# ---------------------------------------------------------------------------


def node(*args: str, home: Path | None = None, timeout: int = 600) -> tuple[int, dict, str]:
    env = dict(os.environ)
    env["RPA_HOME"] = str(home or HOME)
    proc = subprocess.run(
        [NODE, str(INSTALLER), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        payload = {"_raw": proc.stdout[:2000]}
    return proc.returncode, payload, proc.stderr


def python(*args: str, home: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["RPA_HOME"] = str(home or HOME)
    env.pop("RPA_EDITOR_EXE", None)
    env.pop("RPA_EDITOR_MANIFEST", None)
    return subprocess.run(
        [str(PY), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
        check=False,
    )


def fresh(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


class _EditorServer:
    """本机 HTTP 源：让判据真的走「下载 + 取 .sha256」那条腿。

    只给 --editor-zip 的话，downloadFile / sidecar fetch 这两段永远不被执行，
    装完看着成功、真从 Release 装却是另一回事。
    """

    def __init__(self, *, not_found: bool = False):
        self.not_found = not_found
        self.blob = ZIP.read_bytes()
        self.digest = hashlib.sha256(self.blob).hexdigest()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if outer.not_found:
                    self.send_response(404)
                    self.end_headers()
                    return
                if self.path.endswith(".sha256"):
                    body = f"{outer.digest}  {ZIP.name}\n".encode("utf-8")
                elif self.path.endswith(".zip"):
                    body = outer.blob
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.srv.server_port}/{ZIP.name}"

    def __exit__(self, *_exc) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def case_install_from_zip() -> None:
    fresh(HOME)
    code, data, err = node("editor", "install", "--editor-zip", str(ZIP))
    check("install: 退出码 0", code == 0, f"rc={code} {err[:200]}")
    check("install: ok", data.get("ok") is True, json.dumps(data, ensure_ascii=False)[:300])
    exe = Path(str(data.get("exe") or ""))
    check("install: exe 存在", exe.is_file(), str(exe))
    check("install: 版本从包名解析", data.get("version") == "0.1.0", str(data.get("version")))

    manifest_path = HOME / "editor.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    for key in ("editor", "workspace"):
        check(f"editor.json 有 {key}", bool(manifest.get(key)), json.dumps(manifest, ensure_ascii=False)[:200])
    check(
        "editor.json 写了运行时（python 或 rpa-core）",
        bool(manifest.get("python") or manifest.get("rpa-core")),
        json.dumps(manifest, ensure_ascii=False)[:200],
    )
    check("workspace 目录已建", Path(str(manifest.get("workspace") or "")).is_dir(), str(manifest.get("workspace")))
    check("没有留下 .staging*", not list((HOME / "editor").glob(".staging*")))
    check("没有留下 .download.zip", not (HOME / "editor" / ".download.zip").exists())


def case_extract_complete() -> None:
    """解压结果必须与 zip 清单**逐文件、逐字节相等** —— 这才是「能跑」的前提。"""
    with zipfile.ZipFile(ZIP) as zf:
        want = {i.filename: i.file_size for i in zf.infolist() if not i.is_dir()}
    root = HOME / "editor" / "current"
    got = {p.relative_to(root).as_posix(): p.stat().st_size for p in root.rglob("*") if p.is_file()}
    check("解压: 文件数一致", len(want) == len(got), f"zip={len(want)} disk={len(got)}")
    missing = sorted(set(want) - set(got))
    check("解压: 无缺文件", not missing, str(missing[:5]))
    extra = sorted(set(got) - set(want))
    check("解压: 无多余文件", not extra, str(extra[:5]))
    # ★ 用 want 逐个比对，不要写成 `[k for k in want if k in got and ...]`：
    # 一个文件都没解出来时后者是空列表，会「通过」——这正是注入 no-inflate 时
    # 抓到的假绿灯（disk=0 却仍报「每个文件大小一致」）。
    bad = sorted(k for k in want if got.get(k) != want[k])
    check("解压: 每个文件大小一致", not bad, str(bad[:5]))
    check("解压: 总字节一致", sum(want.values()) == sum(got.values()), f"{sum(want.values())} vs {sum(got.values())}")


def case_bad_sha() -> None:
    fresh(HOME)
    code, data, _ = node("editor", "install", "--editor-zip", str(ZIP), "--sha256", "0" * 64)
    check("错 sha: 必须失败", data.get("ok") is False, json.dumps(data, ensure_ascii=False)[:200])
    check("错 sha: 错误码是 EDITOR_SHA_MISMATCH", data.get("code") == "EDITOR_SHA_MISMATCH", str(data.get("code")))
    check("错 sha: 不留下 current", not (HOME / "editor" / "current").exists())
    check("错 sha: 不写 editor.json", not (HOME / "editor.json").exists())


def case_good_sha() -> None:
    fresh(HOME)
    digest = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    _, data, _ = node("editor", "install", "--editor-zip", str(ZIP), "--sha256", digest)
    check("对 sha: 成功", data.get("ok") is True, json.dumps(data, ensure_ascii=False)[:200])
    check("对 sha: 记录 sha_verified=explicit", data.get("sha_verified") == "explicit", str(data.get("sha_verified")))


def case_download_ok() -> None:
    fresh(HOME)
    with _EditorServer() as url:
        code, data, err = node("editor", "install", "--editor-url", url)
    check("下载: 退出码 0", code == 0, f"rc={code} {err[:200]}")
    check("下载: ok", data.get("ok") is True, json.dumps(data, ensure_ascii=False)[:300])
    check("下载: 自动取 .sha256 旁挂", data.get("sha_verified") == "sidecar", str(data.get("sha_verified")))
    check("下载: 窗口落地", (HOME / "editor" / "current").is_dir())
    check("下载: 没留下 .download.zip", not (HOME / "editor" / ".download.zip").exists())


def case_download_404() -> None:
    fresh(HOME)
    with _EditorServer(not_found=True) as url:
        _, data, _ = node("editor", "install", "--editor-url", url)
    check("下载 404: 失败", data.get("ok") is False, json.dumps(data, ensure_ascii=False)[:200])
    check("下载 404: 错误码", data.get("code") == "EDITOR_DOWNLOAD_FAILED", str(data.get("code")))
    fix = " ".join(data.get("fix") or [])
    check("下载 404: 给的是发布修法", "build_editor.py" in fix, fix[:200])
    check("下载 404: 不落 current", not (HOME / "editor" / "current").exists())


def case_zip_missing() -> None:
    fresh(HOME)
    _, data, _ = node("editor", "install", "--editor-zip", str(TMP / "nope.zip"))
    check("zip 不存在: 失败", data.get("ok") is False)
    check("zip 不存在: 错误码", data.get("code") == "EDITOR_ZIP_MISSING", str(data.get("code")))


def case_not_a_zip() -> None:
    fresh(HOME)
    junk = TMP / "junk.zip"
    junk.write_bytes(b"this is not a zip at all" * 100)
    _, data, _ = node("editor", "install", "--editor-zip", str(junk))
    check("非 zip: 失败", data.get("ok") is False)
    check("非 zip: 错误码是 INSTALL_FAILED", data.get("code") == "EDITOR_INSTALL_FAILED", str(data.get("code")))
    check("非 zip: 没有 current", not (HOME / "editor" / "current").exists())


def case_no_exe() -> None:
    fresh(HOME)
    empty = TMP / "no-exe.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "no binary here")
    _, data, _ = node("editor", "install", "--editor-zip", str(empty))
    check("无 exe: 失败", data.get("ok") is False)
    check("无 exe: 错误码是 EDITOR_LAYOUT", data.get("code") == "EDITOR_LAYOUT", str(data.get("code")))


def case_reinstall_keeps_one_previous() -> None:
    fresh(HOME)
    node("editor", "install", "--editor-zip", str(ZIP))
    node("editor", "install", "--editor-zip", str(ZIP))
    node("editor", "install", "--editor-zip", str(ZIP))
    base = HOME / "editor"
    previous = sorted(p.name for p in base.iterdir() if p.name.startswith("previous-"))
    check("重装: 只留一份 previous", len(previous) == 1, str(previous))
    check("重装: current 仍在且 exe 在", (base / "current").is_dir(), "current missing")
    _, data, _ = node("editor", "status")
    check("重装后 status ok", data.get("ok") is True and data.get("installed") is True, json.dumps(data, ensure_ascii=False)[:200])


def case_status_missing_exe() -> None:
    fresh(HOME)
    node("editor", "install", "--editor-zip", str(ZIP))
    manifest = json.loads((HOME / "editor.json").read_text(encoding="utf-8"))
    exe = Path(manifest["editor"])
    exe.rename(exe.with_suffix(".moved"))
    code, data, _ = node("editor", "status")
    check("exe 丢了: status 失败", data.get("ok") is False and code == 1, f"rc={code} ok={data.get('ok')}")
    check("exe 丢了: 说明是哪一步坏了", "exe" in str(data.get("detail") or ""), str(data.get("detail")))
    exe.with_suffix(".moved").rename(exe)


def case_install_with_editor() -> None:
    fresh(HOME)
    fresh(SKILL_DIR)
    code, data, err = node("install", "--dir", str(SKILL_DIR), "--with-editor", "--editor-zip", str(ZIP))
    check("--with-editor: 技能装成功", data.get("ok") is True, f"rc={code} {err[:300]}")
    check("--with-editor: 结果带 editor", bool(data.get("editor")), json.dumps(data, ensure_ascii=False)[:300])
    check("--with-editor: editor ok", (data.get("editor") or {}).get("ok") is True, json.dumps(data.get("editor") or {}, ensure_ascii=False)[:300])
    skill_md = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    check("--with-editor: 占位符已替换", "{{RPA_CORE}}" not in skill_md, "still has placeholder")
    check("--with-editor: 窗口真的落地", (HOME / "editor" / "current").is_dir())


def case_skill_fail_skips_editor() -> None:
    """技能装失败时不该去装窗口：没有运行时的窗口只能开不能跑，装它只会误导。"""
    fresh(HOME)
    fresh(SKILL_DIR)
    node("install", "--dir", str(SKILL_DIR))
    _, data, _ = node("install", "--dir", str(SKILL_DIR), "--with-editor", "--editor-zip", str(ZIP))
    check("技能已存在: 失败", data.get("ok") is False, json.dumps(data, ensure_ascii=False)[:200])
    check("技能已存在: 错误码", data.get("code") == "ALREADY_INSTALLED", str(data.get("code")))
    check("技能已存在: editor 被跳过", (data.get("editor") or {}).get("code") == "SKIPPED", json.dumps(data.get("editor") or {}, ensure_ascii=False)[:200])
    check("技能已存在: 没有装窗口", not (HOME / "editor" / "current").exists())


def case_doctor_editor() -> None:
    fresh(HOME)
    fresh(SKILL_DIR)
    node("install", "--dir", str(SKILL_DIR))
    _, before, _ = node("doctor", "--dir", str(SKILL_DIR))
    ed_before = next((c for c in before.get("checks") or [] if c.get("name") == "editor"), {})
    check("doctor 未装: editor 记 SKIP", ed_before.get("ok") is None, json.dumps(ed_before, ensure_ascii=False))
    check("doctor 未装: 整体仍 ok", before.get("ok") is True, json.dumps(before.get("next") or [])[:200])

    node("editor", "install", "--editor-zip", str(ZIP))
    _, after, _ = node("doctor", "--dir", str(SKILL_DIR))
    ed_after = next((c for c in after.get("checks") or [] if c.get("name") == "editor"), {})
    check("doctor 已装: editor PASS", ed_after.get("ok") is True, json.dumps(ed_after, ensure_ascii=False)[:200])


def _editor_line(out: str) -> str:
    """取 doctor 输出里 `editor` 那一行（不要抓到 `editor path` / `editor window`）。"""
    for line in out.splitlines():
        stripped = line.lstrip()
        if re.match(r"^\[(PASS|FAIL|SKIP)\] editor\s{2,}", stripped):
            return line
    return ""


def case_studio_sees_exe() -> None:
    """studio.py 是另一个消费者：读不到 editor.json，窗口就永远「没装」。

    ★ 不能只断言 `[PASS] editor `：开发态里 `_editor_dir()` 会找到仓库内的 PPX 工程，
    **没装窗口也照样是 PASS**（第一次写的判据就是这么假绿的）。要认清单带来的版本号。
    """
    fresh(HOME)
    node("editor", "install", "--editor-zip", str(ZIP))
    proc = python(str(STUDIO), "doctor")
    line = _editor_line(proc.stdout)
    check("studio doctor: editor PASS 且认清单版本", line.startswith("[PASS]") and "0.1.0" in line,
          line or proc.stdout[-500:].replace("\n", " | "))
    check("studio doctor: 没有 SKIP editor", "[SKIP] editor " not in proc.stdout, line)

    fresh(HOME)
    proc = python(str(STUDIO), "doctor")
    line = _editor_line(proc.stdout)
    check("studio doctor 未装: editor 不是 FAIL", bool(line) and not line.startswith("[FAIL]"), line)
    check("studio doctor 未装: 不认已装窗口", "0.1.0" not in line and "current" not in line, line)


def case_gui_uses_exe() -> None:
    """接线层：cmd_gui 必须真的把 exe 起起来（拿 whoami 当替身，秒退）。"""
    whoami = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe"
    if not whoami.is_file():
        check("gui --exe: 跳过（本机没有 whoami）", True, "skipped")
        return
    proc = python(str(STUDIO), "gui", "--exe", str(whoami), "--no-serve")
    payload = {}
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        payload = {}
    check("gui --exe: 起得来", payload.get("ok") is True, proc.stdout[-300:] + proc.stderr[-300:])
    check("gui --exe: 退出码透传", payload.get("editorExit") == 0, str(payload.get("editorExit")))


CASES = [
    case_install_from_zip,
    case_extract_complete,
    case_download_ok,
    case_download_404,
    case_bad_sha,
    case_good_sha,
    case_zip_missing,
    case_not_a_zip,
    case_no_exe,
    case_reinstall_keeps_one_previous,
    case_status_missing_exe,
    case_install_with_editor,
    case_skill_fail_skips_editor,
    case_doctor_editor,
    case_studio_sees_exe,
    case_gui_uses_exe,
]


# ---------------------------------------------------------------------------
# 注入（负向验证：判据必须变红）
# ---------------------------------------------------------------------------

INJECTIONS = {
    # 去掉 sha 比较：错 sha 那条必须红。
    "no-sha-check": (
        INSTALLER,
        "  if (expected && expected !== digest) {",
        "  if (false && expected && expected !== digest) {",
    ),
    # 解压时把 deflate 当 store 写：字节对比必须红。
    "no-inflate": (
        INSTALLER,
        "    else if (method === 8) out = inflateRawSync(raw);",
        "    else if (method === 8) out = raw;",
    ),
    # 不写运行时两键：editor.json 键齐那条必须红。整块换成 void，别只删 exe 分支
    # —— 非 exe 形态会走 else 写 python，只删一半等于没注入。
    "no-runtime-keys": (
        INSTALLER,
        '    if (runtime.ok && runtime.mode === "exe" && runtime.exe) {\n'
        '      manifest["rpa-core"] = toPosix(runtime.exe);\n'
        "    } else if (runtime.ok && runtime.python) {\n"
        "      manifest.python = toPosix(runtime.python);\n"
        "    }",
        "    void runtime;",
    ),
    # 窗口装失败也当成功：--with-editor 的 editor ok 必须红。
    "always-ok": (
        INSTALLER,
        '      code: "EDITOR_SHA_MISMATCH",',
        '      code: "EDITOR_SHA_MISMATCH", ok: true,',
    ),
    # studio.py 永远找不到窗口：studio doctor 那条必须红。
    "studio-blind": (
        STUDIO,
        '    for path in candidates:\n        if path.is_file():\n            return path\n    return None\n\n\ndef _editor_dir()',
        "    for path in candidates:\n        if path.is_file():\n            return None\n    return None\n\n\ndef _editor_dir()",
    ),
}


# 负向验证的**期望集按推理写**，不照抄某次实际输出。
# must_red：这条注入本该打中的判据（少一条说明判据是假的）；
# unchanged：不该被牵连的判据（红了一片说明验证没打中，而是整体崩了）。
INJECTION_EXPECT = {
    "no-sha-check": {
        "must_red": {"错 sha: 必须失败", "错 sha: 错误码是 EDITOR_SHA_MISMATCH",
                     "错 sha: 不留下 current", "错 sha: 不写 editor.json"},
        "unchanged": {"install: ok", "对 sha: 成功", "gui --exe: 起得来"},
    },
    "no-inflate": {
        "must_red": {"解压: 每个文件大小一致", "解压: 总字节一致"},
        "unchanged": {"zip 不存在: 错误码", "错 sha: 必须失败", "gui --exe: 起得来"},
    },
    "no-runtime-keys": {
        "must_red": {"editor.json 写了运行时（python 或 rpa-core）"},
        "unchanged": {"install: ok", "editor.json 有 workspace", "对 sha: 成功"},
    },
    "always-ok": {
        "must_red": {"错 sha: 必须失败"},
        "unchanged": {"install: ok", "对 sha: 成功", "gui --exe: 起得来"},
    },
    "studio-blind": {
        "must_red": {"studio doctor: editor PASS 且认清单版本"},
        "unchanged": {"gui --exe: 起得来", "doctor 已装: editor PASS", "install: ok"},
    },
}


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def apply_injection(name: str) -> bool:
    path, old, new = INJECTIONS[name]
    text = path.read_bytes().decode("utf-8")
    count = text.count(old)
    if count != 1:
        print(f"[probe] 锚点不唯一（命中 {count} 次）：注入 {name} 放弃", file=sys.stderr)
        return False
    SENTINEL.write_text(f"{path}\n{name}\n", encoding="utf-8")
    injected.append(path)
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    return True


def restore_all() -> None:
    for path in set(injected):
        backup = path.with_suffix(path.suffix + ".probe-bak")
        if backup.is_file():
            path.write_bytes(backup.read_bytes())
            backup.unlink()
    SENTINEL.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inject", choices=sorted(INJECTIONS), help="先改坏被测代码，再跑")
    args = parser.parse_args()

    if SENTINEL.exists():
        print(f"[probe] 发现注入哨兵 {SENTINEL}：上次没有还原干净，拒绝运行", file=sys.stderr)
        return 2
    if not ZIP.is_file():
        print(f"[probe] 缺少产物 {ZIP}；先跑 build_editor.py --release", file=sys.stderr)
        return 2

    TMP.mkdir(parents=True, exist_ok=True)
    # 先备份要注入的文件，供 finally 逐字节还原。
    for path, _old, _new in INJECTIONS.values():
        backup = path.with_suffix(path.suffix + ".probe-bak")
        if not backup.is_file():
            backup.write_bytes(path.read_bytes())

    try:
        if args.inject and not apply_injection(args.inject):
            return 2
        for case in CASES:
            try:
                case()
            except Exception as exc:  # 用例自己炸 = 红，别让整轮中断
                check(f"{case.__name__}: 未抛异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        restore_all()
        for path, _old, _new in INJECTIONS.values():
            backup = path.with_suffix(path.suffix + ".probe-bak")
            if backup.is_file():
                path.write_bytes(backup.read_bytes())
                print(f"[probe] 还原 {path.name} md5={md5(path)}")
                backup.unlink()

    failed = [r for r in results if not r[1]]
    for name, passed, detail in results:
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {name}" + (f"  —  {detail}" if not passed else ""))
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if not args.inject:
        print("RESULT:", "GREEN" if not failed else "RED")
        return 0 if not failed else 1

    # 注入模式：判「期望红全中 + 没误伤」，而不是判「全绿」——全绿反而说明判据是假的。
    expect = INJECTION_EXPECT[args.inject]
    red = {n for n, p, _ in results if not p}
    names = {n for n, _, _ in results}
    missing = expect["must_red"] - red
    unknown = expect["must_red"] - names
    broken = {n for n in expect["unchanged"] if n in red}
    meta = [
        ("期望红全中", not missing, f"没红：{sorted(missing)}"),
        ("判据名字都存在", not unknown, f"名字写错：{sorted(unknown)}"),
        ("没误伤无关判据", not broken, f"被牵连：{sorted(broken)}"),
        ("至少红一条", bool(red), f"红了 {len(red)} 条"),
    ]
    for label, ok, detail in meta:
        print(f"[{'PASS' if ok else 'FAIL'}] 注入 {args.inject}: {label} — {detail}")
    verdict = all(row[1] for row in meta)
    print("RESULT:", "GREEN (negative verified)" if verdict else "RED")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
