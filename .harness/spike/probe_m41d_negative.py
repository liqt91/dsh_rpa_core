"""M41 S5 负向验证探针：「GUI 不自动放弃捕获」判据的三条断言各自独立有效。

判据：``tests/contract/test_gui_capture.py::test_capture_never_gives_up_on_its_own``
被测文件：``src/rpa_core/gui/app.py``（常量 + 两处传参）

为什么值得单独立三条注入：这条判据钉的是**三个不同环节**——① 常量本身是 ``inf``、
② 构造会话时把 ``inf`` 传下去、③ ``pick`` 实际收到 ``inf``。只做一处注入的话，另外两条
照样可能是**永远绿**的摆设（本仓反复踩到「常量/纯函数正确 ≠ 真的用上了」）。一次注入
一处，要求红的**行号**落在各自对应的那条断言上。

用法（探针自己用 CPython 跑 pytest，故用 .venv 的解释器启动）::

    .venv/Scripts/python.exe .harness/spike/probe_m41d_negative.py

**别用 ``git checkout`` 还原**：被测文件当时带着未提交改动，``checkout`` 会把它们一起
退回 HEAD 版本。探针走**内存备份还原**（``._m41d_clean.py``），收尾按 md5 逐字节核对。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET = ROOT / "src" / "rpa_core" / "gui" / "app.py"
BACKUP = ROOT / "._m41d_clean.py"
REPORT = ROOT / "_m41d_negative_report.txt"
TEST_ID = "tests/contract/test_gui_capture.py::test_capture_never_gives_up_on_its_own"

# key: (注入前锚点, 注入后文本, 期望变红的**行号**（判据文件里的断言行）, 说明)
CASES: dict[str, tuple[str, str, int, str]] = {
    "q1": (
        'CAPTURE_TIMEOUT_SECONDS = float("inf")',
        "CAPTURE_TIMEOUT_SECONDS = 90.0  # INJECTED-Q1",
        654,
        "常量改回 90 秒（自动放弃回来了）",
    ),
    "q2": (
        """        session = HybridCaptureSession(
            desktop_factory=DesktopCaptureSession,
            hover=True,
            timeout_seconds=CAPTURE_TIMEOUT_SECONDS,
        )""",
        """        session = HybridCaptureSession(
            desktop_factory=DesktopCaptureSession,
            hover=True,
            timeout_seconds=30.0,  # INJECTED-Q2
        )""",
        661,
        "构造会话时传别的值（常量是 inf 但没传下去）",
    ),
    "q3": (
        "                result = session.pick(timeout_seconds=CAPTURE_TIMEOUT_SECONDS)",
        "                result = session.pick(timeout_seconds=30.0)  # INJECTED-Q3",
        664,
        "pick 用别的值（构造对了但 pick 没用上）",
    ),
}

SENTINEL = "INJECTED-Q"


def _md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _pytest() -> tuple[int, str]:
    """跑判据，返回（退出码, 输出）。与门禁同口径：.venv 解释器 + -m pytest。"""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            TEST_ID,
            "-o",
            "addopts=",
            "-q",
            "--tb=line",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    clean_md5 = _md5(TARGET)
    BACKUP.write_bytes(TARGET.read_bytes())
    lines: list[str] = [f"clean md5 = {clean_md5}", ""]

    # ① 对照：干净态必须绿（否则「红了」可能是别的原因）
    code, out = _pytest()
    control_green = code == 0 and "1 passed" in out
    lines.append(f"[对照] 干净态：{'GREEN (1 passed)' if control_green else 'RED —— 先修干净态'}")
    if not control_green:
        lines.append(out[-1500:])

    verdicts: list[bool] = [control_green]
    for key, (old, new, line_no, desc) in CASES.items():
        text = TARGET.read_text(encoding="utf-8")
        if old not in text:
            lines.append(f"[{key}] 锚点未命中 —— 文件已变，探针需同步")
            verdicts.append(False)
            continue
        TARGET.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        injected = SENTINEL in TARGET.read_text(encoding="utf-8")

        code, out = _pytest()
        red = code != 0 and "1 failed" in out
        # 精确命中：失败行号必须落在该 case 期望的那条断言上
        hit = f"test_gui_capture.py:{line_no}:" in out
        # 还原（内存备份，逐字节）
        TARGET.write_bytes(BACKUP.read_bytes())
        healed = SENTINEL not in TARGET.read_text(encoding="utf-8")
        same = _md5(TARGET) == clean_md5

        verdicts.append(injected and red and hit and healed and same)
        lines.append(
            f"[{key}] {desc}\n"
            f"      注入写入={injected} 判据变红={red} 命中行={hit}(:{line_no}) "
            f"还原干净={healed} md5一致={same}"
        )
        if not (red and hit):
            lines.append("      " + out.strip().replace("\n", "\n      ")[-900:])

    # ② 收尾：还原后再跑一次，必须绿（否则留下了坏状态）
    code, out = _pytest()
    final_green = code == 0 and "1 passed" in out
    verdicts.append(final_green)
    lines.append("")
    lines.append(f"[收尾] 还原后复跑：{'GREEN' if final_green else 'RED —— 状态没还干净'}")
    lines.append(f"最终 md5 = {_md5(TARGET)} | 与 clean 一致 = {_md5(TARGET) == clean_md5}")

    ok = all(verdicts)
    lines.append("")
    lines.append(
        "NEGATIVE VERIFICATION PASSED"
        if ok
        else "NEGATIVE VERIFICATION FAILED（看上面哪一条没命中）"
    )
    report = "\n".join(lines)
    REPORT.write_text(report, encoding="utf-8", newline="\n")
    BACKUP.unlink(missing_ok=True)
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
