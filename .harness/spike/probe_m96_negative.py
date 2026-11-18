"""M96 负向验证：校验高亮框的两条判据必须真拦得住破坏。

注入按推理写期望集（不照抄实际）：
1. 去掉窗口类重复注册的兜底 → 「连续三次闪框」红（回到用户看到的第二次不框）；
2. `_show_overlay` 不返回成功标志  → 「连续三次闪框」红（返回值恒 None ⇒ False）；
3. `flash_rect` 不看 `_show_overlay` 的结果 → 「非法矩形不许报成功」红。

写回一律 write_bytes（write_text 会把 LF 转 CRLF）；锚点要求唯一；结束核 md5。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
HL = ROOT / "src/rpa_core/capture/desktop_highlight.py"
DA = ROOT / "src/rpa_core/capture/desktop_agent.py"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "M96-INJECT-SENTINEL"

REPEATED = "test_flash_rect_survives_repeated_calls"
BAD_RECT = "test_flash_rect_noop_on_bad_rect"
SWITCH = "test_capture_at_switches_to_finer_screen_hit"
KEEP_SCOPED = "test_capture_at_keeps_scoped_when_no_finer_hit"
THRESHOLD = "test_rect_fills_window_threshold"
GATE = "M96 捕获粒度：整窗容器回退屏幕级"

INJECTIONS = [
    (
        "register-swallow-removed", HL,
        "    try:\n"
        "        win32gui.RegisterClass(wc)\n"
        "    except Exception:  # noqa: BLE001 - 1410 类已存在 = 复用，其它错误交给外层兜底\n"
        "        pass\n",
        "    win32gui.RegisterClass(wc)  # " + SENTINEL + "\n",
        ("highlight",), {REPEATED},
    ),
    (
        "show-overlay-no-return", HL,
        "        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,\n"
        "    )\n"
        "    return True\n",
        "        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,\n"
        "    )\n"
        "    # " + SENTINEL + "\n",
        ("highlight",), {REPEATED},
    ),
    (
        "flash-ignores-show-result", HL,
        "        if not _show_overlay(win32gui, win32con, hwnd, rect):\n"
        "            return False\n",
        "        _show_overlay(win32gui, win32con, hwnd, rect)  # " + SENTINEL + "\n",
        ("highlight",), {BAD_RECT},
    ),
    (
        "no-finer-fallback", DA,
        "        finer = _finer_element_in_same_window(x, y, int(scope_hwnd))\n"
        "        if finer is not None:\n"
        "            return finer\n",
        "        # " + SENTINEL + "\n",
        ("gate", "capture"), {SWITCH, KEEP_SCOPED, GATE},
    ),
    (
        "fills-window-always-false", DA,
        "    return elem_w * elem_h >= win_w * win_h * threshold",
        "    return False  # " + SENTINEL,
        # 整窗判定恒 False ⇒ 直接采信窗口作用域结果：switch 拿不到细元素、
        # keeps_scoped 里兜底压根没被调用（它断言 finer==1）、阈值用例同时红，
        # 契约里的面积阈值锚点也消失。
        ("gate", "capture"), {SWITCH, KEEP_SCOPED, THRESHOLD, GATE},
    ),
]

RUNNERS = {
    "highlight": [
        str(PY), "-m", "pytest", "tests/unit/test_desktop_highlight.py",
        "-q", "--basetemp=D:/Temp/pt_m96neg_hl",
    ],
    "capture": [
        str(PY), "-m", "pytest", "tests/unit/test_capture_container_fallback.py",
        "-q", "--basetemp=D:/Temp/pt_m96neg_cap",
    ],
    "gate": [str(PY), ".harness/scripts/check_element_pick_contract.py"],
}


def run(which: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"TMPDIR": "D:/Temp", "TEMP": "D:/Temp", "TMP": "D:/Temp",
                "PYTHONPATH": str(ROOT / "src")})
    return subprocess.run(
        RUNNERS[which], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=300,
    )


def failed_names(which: str, output: str) -> set[str]:
    if which == "gate":
        return {
            line[5:].split(" — ")[0].strip()
            for line in output.splitlines() if line.startswith("FAIL ")
        }
    return {
        line.split("::")[-1].split()[0].strip()
        for line in output.splitlines() if line.startswith("FAILED ")
    }


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    paths = (HL, DA)
    for path in paths:
        if SENTINEL in path.read_text(encoding="utf-8"):
            print(f"[FATAL] {path.name} 已有注入哨兵，先手工清理")
            return 2
    baselines = {p: md5(p) for p in paths}
    print("baseline md5:", {p.name: v[:10] for p, v in baselines.items()})

    for which in RUNNERS:
        base = run(which)
        tail = (base.stdout or base.stderr).strip().splitlines()[-1:]
        print(f"[baseline {which}] rc={base.returncode} {tail[0][:70] if tail else ''}")
        if base.returncode != 0:
            print("[FATAL] 基线不绿，负向验证无意义")
            return 2

    ok = True
    for name, path, old, new, runners, expected in INJECTIONS:
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"[FAIL] {name}: 锚点出现 {original.count(old)} 次（要求 1）")
            ok = False
            continue
        path.write_bytes(original.replace(old, new, 1).encode("utf-8"))
        got: set[str] = set()
        codes: list[int] = []
        errorish = False
        try:
            for which in runners:
                bad = run(which)
                codes.append(bad.returncode)
                got |= failed_names(which, bad.stdout)
                errorish = errorish or " error" in bad.stdout
        finally:
            path.write_bytes(original.encode("utf-8"))
        print(f"\n[{name}] rc={codes} 失败集={sorted(got)}")
        print(f"  期望={sorted(expected)}")
        if got != expected:
            print("  [FAIL] 失败集与期望不等")
            ok = False
        if any(code not in (0, 1) for code in codes) or errorish:
            print("  [FAIL] 出现 error 或异常退出码（环境坏了，不是判据红）")
            ok = False

    restored = {p: md5(p) for p in paths}
    print("\nrestored md5:", {p.name: v[:10] for p, v in restored.items()})
    if restored != baselines:
        print("[FAIL] md5 未逐字节还原")
        ok = False
    for which in RUNNERS:
        again = run(which)
        print(f"[after restore {which}] rc={again.returncode}")
        if again.returncode != 0:
            print("[FAIL] 还原后复跑不绿")
            ok = False
    print("M96 NEGATIVE VERIFICATION " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
