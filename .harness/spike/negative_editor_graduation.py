"""负向验证：ADR 0021「编辑器住址」那条新判据真的拦得住吗。

三条注入，一条元验证：

1. 把 `apps/editor/ppx.toml` 挪走 ⇒ 必须红，且点名「编辑器不在 apps/editor」；
2. 重建 `.harness/demo/ppx_probe/` ⇒ 必须红，且点名「又回到脚手架」；
3. **元验证**：在第 1 条的现场上把 `check_editor_location(errors)` 那行调用摘掉
   ⇒ 必须变绿。否则「红」可能来自别的判据，我只是凑巧看到一条红。

每次注入都逐字节还原并核 md5；探针自身起手先扫哨兵，发现残留即硬失败（上一次
崩在中间会把源码留在注入态，静默跑下去只会得出相反的结论）。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
GATE = ROOT / ".harness" / "scripts" / "check_host_boundary.py"
TOML = ROOT / "apps" / "editor" / "ppx.toml"
TOML_BAK = ROOT / "apps" / "editor" / "ppx.toml.negative-bak"
LEGACY = ROOT / ".harness" / "demo" / "ppx_probe"

SENTINEL = "# 哨兵：这是负向验证留下的注入态，必须先还原"


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_gate() -> tuple[int, str]:
    done = subprocess.run(
        [str(PY), str(GATE)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def main() -> int:
    if TOML_BAK.exists() or LEGACY.exists() or SENTINEL in GATE.read_text(encoding="utf-8"):
        print("[FAIL] 发现上次残留（哨兵命中），先手动还原再跑")
        return 2

    before_gate_md5 = md5(GATE)
    results: list[tuple[str, bool, str]] = []

    rc, out = run_gate()
    if rc != 0:
        print("[FAIL] 基线本身就不绿，负向验证没有意义：\n" + out[-800:])
        return 2

    # 注入 1：编辑器不在 apps/editor
    TOML.rename(TOML_BAK)
    try:
        rc, out = run_gate()
        hit = rc == 1 and "编辑器不在 apps/editor" in out
        results.append(("挪走 ppx.toml ⇒ 应红且点名住址", hit, out.strip().splitlines()[-1] if out.strip() else ""))

        # 注入 3（元验证）：把新判据的接线摘掉，同现场应恢复绿
        original = GATE.read_bytes()
        text = original.decode("utf-8")
        wired = "    check_editor_location(errors)\n"
        assert text.count(wired) == 1, "接线锚点不唯一"
        GATE.write_bytes(text.replace(wired, "", 1).encode("utf-8"))
        try:
            rc2, out2 = run_gate()
            meta = rc2 == 0
            results.append(("摘掉新判据接线 ⇒ 同现场应变绿（元验证）", meta, out2.strip().splitlines()[-1] if out2.strip() else ""))
        finally:
            GATE.write_bytes(original)
    finally:
        TOML_BAK.rename(TOML)

    # 注入 2：脚手架目录复活
    LEGACY.mkdir(parents=True, exist_ok=True)
    try:
        rc, out = run_gate()
        hit = rc == 1 and "又回到 .harness/demo/ppx_probe" in out
        results.append(("重建 .harness/demo/ppx_probe ⇒ 应红", hit, out.strip().splitlines()[-1] if out.strip() else ""))
    finally:
        shutil.rmtree(LEGACY, ignore_errors=True)

    ok = True
    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}  ← {detail}")
        ok = ok and passed

    rc, _ = run_gate()
    restored = md5(GATE) == before_gate_md5 and rc == 0 and TOML.is_file() and not LEGACY.exists()
    print(f"[{'PASS' if restored else 'FAIL'}] 逐字节还原 + 复绿（gate md5 {before_gate_md5[:8]}）")
    ok = ok and restored
    print("NEGATIVE OK" if ok else "NEGATIVE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
