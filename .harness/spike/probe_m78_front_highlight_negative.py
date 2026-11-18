"""M78 负向验证：高亮/截图/前置三条线必须真接线。

A. flash 才画框，preview/clear 不画；截图失败不影响命中；
B. --shot 的元数据要能到统一回传；
C. uia / win32 两个执行器的元素动作都要前置窗口。

哨兵自检 / 锚点唯一 / try-finally 逐字节还原核 md5 / 不接管道。
跑法：./.venv/Scripts/python.exe .harness/spike/probe_m78_front_highlight_negative.py
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "src/rpa_core/capture/desktop_verification.py"
FACADE = ROOT / "src/rpa_core/capture/verification.py"
UIA = ROOT / "src/rpa_core/executors/desktop.py"
WIN32 = ROOT / "src/rpa_core/executors/desktop_win32.py"
HIGHLIGHT = ROOT / "src/rpa_core/capture/desktop_highlight.py"
TEST = ROOT / "tests/contract/test_desktop_front_and_highlight.py"

A_FLASH = "            if rect is not None:\n                self._flash(rect)"
B_SHOT = '            out["shot"] = dict(shot)'
C_UIA = "                    _bring_window_to_front(session.window_handle)"
D_WIN32 = "                _bring_window_to_front(session.window_handle)"
E_CMDS_UIA = '"desktop.click", "desktop.input", "desktop.getText"'
F_CMDS_WIN32 = '"desktop.win32.click", "desktop.win32.input", "desktop.win32.getText"'

FILES = (VERIFY, FACADE, UIA, WIN32, HIGHLIGHT, TEST)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def check_static() -> None:
    assert A_FLASH in VERIFY.read_text(encoding="utf-8"), "flash 没接高亮"
    assert B_SHOT in FACADE.read_text(encoding="utf-8"), "统一回传没带 shot"
    uia = UIA.read_text(encoding="utf-8")
    win32 = WIN32.read_text(encoding="utf-8")
    assert C_UIA in uia, "uia 执行器没前置窗口"
    assert D_WIN32 in win32, "win32 执行器没前置窗口"
    assert E_CMDS_UIA in uia, "uia 动作集合不对"
    assert F_CMDS_WIN32 in win32, "win32 动作集合不对"
    assert "def flash_rect" in HIGHLIGHT.read_text(encoding="utf-8"), "高亮实现不存在"


def _run_tests() -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTEST_ADDOPTS"] = "--basetemp=C:/Users/Administrator/AppData/Local/Temp/rpa-core-m78-neg"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(TEST), "-o", "addopts="],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _expect_red(name: str, patch: tuple[Path, str, str], *, run_tests: bool) -> None:
    before = {p: _md5(p) for p in FILES}
    path, old, new = patch
    source = path.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError(f"{name}: 锚点不唯一（count={source.count(old)}）")
    try:
        path.write_text(source.replace(old, new), encoding="utf-8", newline="\n")
        if run_tests:
            code, out = _run_tests()
            assert code != 0, f"{name}: 注入后仍绿"
            assert not re.search(r"\b\d+\s+errors?\b", out, re.IGNORECASE) and "ERROR " not in out, (
                f"{name}: 红来自 error 而非 failed\n{out[-1200:]}"
            )
            assert "failed" in out.lower(), f"{name}: 没有 failed 汇总\n{out[-1200:]}"
        else:
            try:
                check_static()
            except AssertionError:
                return
            raise AssertionError(f"{name}: 静态判据没红")
    finally:
        path.write_text(source, encoding="utf-8", newline="\n")
        for p, digest in before.items():
            assert _md5(p) == digest, f"{name}: {p.name} 还原后 md5 不一致"


def main() -> None:
    try:
        check_static()
    except AssertionError as exc:
        raise SystemExit(f"哨兵自检失败：{exc}") from exc
    code, out = _run_tests()
    if code != 0:
        raise SystemExit(f"对照跑没绿：\n{out[-1500:]}")

    _expect_red("A1 删高亮调用", (VERIFY, A_FLASH, "            if False:\n                pass"),
                run_tests=True)
    _expect_red("B1 删 shot 回传", (FACADE, B_SHOT, "            pass"), run_tests=True)
    _expect_red("C1 删 uia 前置", (UIA, C_UIA, "                    pass"), run_tests=False)
    _expect_red("D1 删 win32 前置", (WIN32, D_WIN32, "                pass"), run_tests=False)
    _expect_red("E1 改 uia 动作集", (UIA, E_CMDS_UIA,
                                  '"desktop.click", "desktop.input"'), run_tests=False)
    _expect_red("F1 改 win32 动作集", (WIN32, F_CMDS_WIN32,
                                    '"desktop.win32.click", "desktop.win32.input"'),
                run_tests=False)

    check_static()
    print("M78 NEGATIVE VERIFICATION PASSED (6/6)")


if __name__ == "__main__":
    main()
