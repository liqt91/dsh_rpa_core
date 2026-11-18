"""M85 负向验证：把「图像框写进 last_rect」注入回去，新增的多帧用例必须变红。

注入是**合法语法的精准回滚**（只加一行赋值），不是砸代码——否则红了也证明不了
判据能抓住这个 bug。四条纪律：对照跑（注入前绿）、`1 failed` 且无 `N error`、
超时要报出来、逐字节还原核 md5。
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "src/rpa_core/capture/desktop_agent.py"
PY = ROOT / ".venv/Scripts/python.exe"
TEST = "tests/contract/test_desktop_image_hover_loop.py"

# 锚点整行、带足缩进（24 空格），并做唯一性自检
ANCHOR = "                        last_box = box\n"
INJECT = "                        last_rect = box\n"


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_test() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PY), "-m", "pytest", TEST, "-q", "--basetemp=D:/Temp/pt_m85neg"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env={**os.environ, "TMPDIR": "D:/Temp", "TEMP": "D:/Temp", "TMP": "D:/Temp"},
    )


def judge(label: str, proc: subprocess.CompletedProcess[str], *, expect_ok: bool) -> bool:
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    print(f"[{label}] rc={proc.returncode} tail={tail}")
    if proc.returncode == 0 and not expect_ok:
        print(f"[FAIL] {label}: 期望红但绿了 —— 判据没有打中")
        return False
    if proc.returncode != 0 and expect_ok:
        print(f"[FAIL] {label}: 期望绿但红了")
        return False
    return True


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    if original.count(ANCHOR) != 1:
        print(f"[FAIL] 锚点出现 {original.count(ANCHOR)} 次（要求恰好 1 次）")
        return 2
    before_md5 = md5(TARGET)
    ok = True
    try:
        base = run_test()
        ok &= judge("对照（未注入）", base, expect_ok=True)

        injected = original.replace(ANCHOR, ANCHOR + INJECT, 1)
        assert injected != original
        TARGET.write_text(injected, encoding="utf-8", newline="\n")
        bad = run_test()
        body = bad.stdout or ""
        ok &= judge("注入 last_rect = box", bad, expect_ok=False)
        # 汇总行格式随 pytest 版本/插件变，判据落在「恰好一条 FAILED」上，
        # 若汇总行存在则顺带核对 failed 计数与 error 数（error = 判据没跑起来）。
        if body.count("FAILED") != 1:
            print(f"[FAIL] FAILED 行数={body.count('FAILED')}（要求恰好 1 条）")
            ok = False
        failed = re.search(r"(\d+) failed", body)
        if failed is not None and failed.group(1) != "1":
            print(f"[FAIL] 失败数={failed.group(1)}（要求 1）")
            ok = False
        if re.search(r"\b\d+ error", body):
            print("[FAIL] 注入后出现 error（用例是收集/环境坏了，不是判据红了）")
            ok = False
        if "AttributeError" not in body:
            print("[FAIL] 红的原因不是 AttributeError —— 判据没打中真正的 bug")
            ok = False
    finally:
        # 逐字节还原：写回原文并核 md5
        TARGET.write_bytes(original.encode("utf-8"))
        after_md5 = md5(TARGET)
        if after_md5 != before_md5:
            print(f"[FAIL] 还原失败 {before_md5} != {after_md5}")
            ok = False
        else:
            print(f"[OK] 逐字节还原 md5={after_md5}")

    restored = run_test()
    ok &= judge("还原后复跑", restored, expect_ok=True)
    print("ALL PASS" if ok else "NEGATIVE PROBE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
