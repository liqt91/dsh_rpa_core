"""check_host_boundary.py 的负向验证探针（M65）。

两个方向都做：
  A. 沙盒方向——在临时树里逐条构造「该红」与「该绿」的形态，确认判据打中。
  B. 真仓库方向——真实改动一个 GUI 文件一个字节，确认门禁真红、且逐字节还原。

铁律：探针自检开头扫哨兵；注入包 try/finally；不删仓库里的任何文件
（删除场景只在临时目录里造）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".harness" / "scripts"))

import check_host_boundary as H  # noqa: E402

TMP = REPO / ".harness" / "spike" / "_host_probe_tmp"
VICTIM = REPO / "src" / "rpa_core" / "gui" / "i18n.py"
MARKER = b"\n# probe-injected-by-check_host_boundary-selftest\n"

failures: list[str] = []
case_seq = 0


def expect(name: str, errors: list[str], needle: str | None, *, should_fail: bool) -> None:
    joined = "\n".join(errors)
    if should_fail:
        ok = bool(errors) and needle is not None and needle in joined
    else:
        ok = not errors
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failures.append(name)
        print(f"        期望 {'红(' + str(needle) + ')' if should_fail else '绿'}，实际: {errors}")


def fresh_tree() -> tuple[Path, Path]:
    """每次用一个新目录，**不做 rmtree**——批量删除会撞上删除守卫，
    让探针在「删得太多」上失败，掩盖真正的判据结论。
    """
    global case_seq
    case_seq += 1
    base = TMP / f"case{case_seq}"
    gui = base / "gui"
    gui.mkdir(parents=True)
    (gui / "alpha.py").write_bytes(b"x = 1\n")
    (gui / "beta.py").write_bytes(b"y = 2\n")
    freeze = base / "freeze.json"
    H.GUI, H.FREEZE, H.DEMO, H.SRC = gui, freeze, base / "demo", base / "src"
    return gui, freeze


def freeze_now() -> None:
    current = H._gui_files()
    H.FREEZE.write_text(json.dumps({"gui": current}, indent=2) + "\n", encoding="utf-8")


def sandbox_cases() -> None:
    gui, _ = fresh_tree()
    freeze_now()

    errors: list[str] = []
    H.check_gui_frozen(errors)
    expect("一致时绿", errors, None, should_fail=False)

    (gui / "alpha.py").write_bytes(b"x = 999\n")
    errors = []
    H.check_gui_frozen(errors)
    expect("改内容 → 红", errors, "内容被修改", should_fail=True)

    (gui / "alpha.py").write_bytes(b"x = 1\n")
    (gui / "gamma.py").write_bytes(b"z = 3\n")
    errors = []
    H.check_gui_frozen(errors)
    expect("新增文件 → 红", errors, "新增了文件", should_fail=True)

    (gui / "gamma.py").unlink()
    (gui / "beta.py").unlink()
    errors = []
    H.check_gui_frozen(errors)
    expect("删除文件 → 红", errors, "文件被删除", should_fail=True)

    # 回填 beta，确认「删除」这条红不是别的原因带来的
    (gui / "beta.py").write_bytes(b"y = 2\n")
    errors = []
    H.check_gui_frozen(errors)
    expect("回填后重新绿", errors, None, should_fail=False)

    # 缺基线 → 红（防止基线文件被误删后静默变绿）
    H.FREEZE.unlink()
    errors = []
    H.check_gui_frozen(errors)
    expect("缺基线 → 红", errors, "缺少冻结基线", should_fail=True)


def isolation_cases() -> None:
    demo = TMP / f"case{case_seq}" / "demo" / "app" / "api"
    demo.mkdir(parents=True, exist_ok=True)

    (demo / "bad_qt.py").write_bytes(b"import PySide6.QtWidgets\n")
    errors = []
    H.check_demo_isolation(errors)
    expect("原型 import PySide6 → 红", errors, "不得引入 Qt", should_fail=True)

    (demo / "bad_qt.py").unlink()
    (demo / "bad_gui.py").write_bytes(b"from rpa_core.gui import app\n")
    errors = []
    H.check_demo_isolation(errors)
    expect("原型 from rpa_core.gui import app → 红", errors, "不得依赖 gui", should_fail=True)

    (demo / "bad_gui.py").unlink()
    (demo / "ok_i18n.py").write_bytes(
        b"from rpa_core.gui.i18n import load_command_display_names\n"
        b"from rpa_core.catalog import load_catalog\n"
    )
    errors = []
    H.check_demo_isolation(errors)
    expect("原型只用 gui.i18n + 能力层 → 绿", errors, None, should_fail=False)

    src = TMP / f"case{case_seq}" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "leak.py").write_bytes(b"import ppx_py\n")
    errors = []
    H.check_no_ppx_in_src(errors)
    expect("能力层 import ppx_py → 红", errors, "不得依赖宿主壳", should_fail=True)

    (src / "leak.py").unlink()
    errors = []
    H.check_no_ppx_in_src(errors)
    expect("清掉泄漏后绿", errors, None, should_fail=False)


def real_repo_case() -> None:
    original = VICTIM.read_bytes()
    if MARKER in original:
        print("[FAIL] 文件里已有上一次注入的哨兵，拒绝继续")
        failures.append("哨兵自检")
        return
    md5_before = hashlib.md5(original).hexdigest()
    try:
        VICTIM.write_bytes(original + MARKER)
        errors: list[str] = []
        H.GUI, H.FREEZE = REPO / "src" / "rpa_core" / "gui", REPO / ".harness" / "gui-freeze.json"
        H.check_gui_frozen(errors)
        expect("真仓库注入一个字节 → 门禁红", errors, "内容被修改", should_fail=True)
    finally:
        VICTIM.write_bytes(original)
    md5_after = hashlib.md5(VICTIM.read_bytes()).hexdigest()
    print(f"[{'PASS' if md5_before == md5_after else 'FAIL'}] 逐字节还原（md5 {md5_before[:8]}）")
    if md5_before != md5_after:
        failures.append("还原校验")

    errors = []
    H.check_gui_frozen(errors)
    expect("还原后门禁重新绿", errors, None, should_fail=False)


def main() -> int:
    sandbox_cases()
    isolation_cases()
    real_repo_case()
    print(f"\n临时树留在 {TMP}（不自动删除：批量删除会撞删除守卫）")
    print(f"{'PROBE FAILED: ' + str(len(failures)) if failures else 'PROBE PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
