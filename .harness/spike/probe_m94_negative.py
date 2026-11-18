"""M94 负向验证：底栏默认高度与顶边拖拽的判据必须真拦得住破坏。

注入都针对本轮新写的三处接线，期望失败集按推理写、不照抄实际：
1. CSS 默认高改回 228px      → 底栏判据的 M94 用例红（静态层）；
2. HTML 删掉拖拽手柄          → M94 用例红（静态层）；
3. main.js 不接 bindDockResize → M94 用例红（mousedown 没绑）；
4. 拖拽不改 style.height      → M94 用例红（真拖了但高度不动）；
5. 折叠态不挡拖拽            → M94 用例红（折叠还能拉）。

写回一律 write_bytes（write_text 会把 LF 转 CRLF），锚点要求唯一，结束核 md5。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
GUI = ROOT / ".harness/demo/ppx_probe/app/gui"
CSS = GUI / "src/style.css"
HTML = GUI / "index.html"
MAIN = GUI / "src/main.js"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "M94-INJECT-SENTINEL"
DOCK_TEST = "M94 底栏默认高随视口给足，顶边拖拽真改高度、折叠时不拖"

INJECTIONS = [
    (
        "css-height-pinned", CSS,
        "  height: clamp(300px, 34vh, 440px); min-height: 0;",
        "  height: 228px; min-height: 0;",
        {DOCK_TEST},
    ),
    (
        "html-grip-missing", HTML,
        '        <div id="dockResize" class="dockResize" title="拖动调整底栏高度"></div>\n',
        "",
        {DOCK_TEST},
    ),
    (
        "grip-not-bound", MAIN,
        "  bindDockResize()\n",
        "  // " + SENTINEL + "\n",
        {DOCK_TEST},
    ),
    (
        "drag-noop", MAIN,
        "  el.dock.style.height = `${height}px`\n",
        "  void height // " + SENTINEL + "\n",
        {DOCK_TEST},
    ),
    (
        "fold-not-guarded", MAIN,
        "    if (el.dock.classList.contains('collapsed')) return\n",
        "    // " + SENTINEL + "\n",
        {DOCK_TEST},
    ),
]

RUNNERS = {
    "dock": ["node", "scripts/check_editor_bottom_dock.mjs"],
}


def run(which: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"TMPDIR": "D:/Temp", "TEMP": "D:/Temp", "TMP": "D:/Temp",
                "PYTHONPATH": str(ROOT / "src")})
    return subprocess.run(
        RUNNERS[which], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=180,
    )


def failed_names(output: str) -> set[str]:
    return {
        line[5:].split(" — ")[0].strip()
        for line in output.splitlines()
        if line.startswith("FAIL ")
    }


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    paths = (CSS, HTML, MAIN)
    for path in paths:
        if SENTINEL in path.read_text(encoding="utf-8"):
            print(f"[FATAL] {path.name} 已有注入哨兵，先手工清理")
            return 2
    baselines = {p: md5(p) for p in paths}
    print("baseline md5:", {p.name: v[:10] for p, v in baselines.items()})

    ok = True
    base = run("dock")
    tail = (base.stdout or base.stderr).strip().splitlines()[-1:]
    print(f"[baseline dock] rc={base.returncode} {tail[0][:70] if tail else ''}")
    if base.returncode != 0:
        print("[FATAL] 基线不绿，负向验证无意义")
        return 2

    saved: dict[Path, bytes] = {}
    try:
        for name, path, old, new, expected in INJECTIONS:
            original = path.read_text(encoding="utf-8")
            saved.setdefault(path, original.encode("utf-8"))
            if original.count(old) != 1:
                print(f"[FAIL] {name}: 锚点出现 {original.count(old)} 次（要求 1）")
                ok = False
                continue
            comment = "//" if path.suffix in (".js", ".css") else "#"
            patched = new.replace("  # " + SENTINEL, "  " + comment + " " + SENTINEL)
            path.write_bytes(original.replace(old, patched, 1).encode("utf-8"))
            try:
                bad = run("dock")
            finally:
                path.write_bytes(original.encode("utf-8"))
            got = failed_names(bad.stdout)
            tail = bad.stdout.strip().splitlines()[-1:] if bad.stdout.strip() else [""]
            print(f"\n[{name}] rc={bad.returncode} 失败集={sorted(got)}")
            print(f"  期望={sorted(expected)}  {tail[0][:80]}")
            if got != expected:
                print("  [FAIL] 失败集与期望不等")
                ok = False
            if " error" in bad.stdout or bad.returncode not in (0, 1):
                print("  [FAIL] 出现 error 或异常退出码（环境坏了，不是判据红）")
                ok = False
    finally:
        for path, original in saved.items():
            path.write_bytes(original)

    restored = {p: md5(p) for p in paths}
    print("\nrestored md5:", {p.name: v[:10] for p, v in restored.items()})
    if restored != baselines:
        print("[FAIL] md5 未逐字节还原")
        ok = False
    again = run("dock")
    print(f"[after restore] rc={again.returncode}")
    if again.returncode != 0:
        print("[FAIL] 还原后复跑不绿")
        ok = False
    print("M94 NEGATIVE VERIFICATION " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
