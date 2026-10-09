"""负向验证：捕获确认框三处体验（M47.10，2026-10-08）。

维护者实测四条反馈（①②③④，⑤ XPath 单独立片）：

  ① 节点树与属性表**并排**（此前上下堆叠）；
  ② 备选定位「逻辑盘一下」——结论是**候选生成面太窄**，不是「功能没用」，
     本轮不动（与 XPath 单独立片）；
  ③「当前命中 X 个」与「预览：命中 X 个」文案重复 → **只留一处**（编辑区那条）；
  ④ 主选择器框太小（QLineEdit 一行装不下长祖先链）→ 换**多行**自适应高度。

本轮新增/修改的判据有 **5 条**，逐一注入破坏、确认**真的**变红：

  N1 节点树 / 属性表不并排（退回上下）        → test_path_tree_and_attr_table_are_side_by_side
  N2 主选择器退回 QLineEdit（④ 的回归）      → test_selector_edit_is_multiline_and_grows
  N3 SelectorEdit 重演信号遮蔽（setText 不 emit）→ test_selector_edit_settext_emits_textchanged
  N4 高度不封顶（多行内容无限膨胀）           → test_selector_edit_height_tracks_line_count
  N5 命中标签不去重（预览标签又带「预览：」）  → test_form_live_preview_dispatch_and_label

本探针按仓库负向验证四条规矩办：
  ① 每处注入前先确认**原始文件绿**（跑一次对照，见 baseline）；
  ② 注入后必须 ``N failed`` 且**不得**出现 ``N error``（区分断言失败与语法/收集错误）；
  ③ 超时判「挂住」要报出，不算通过；
  ④ 逐字节还原核 md5。
每处注入的期望**落在具体断言上**（nodeid + 期望文本），不是「退出码非 0」——
后者等于一台「无论跑什么都报红」的假绿灯机。

用法：
    ./.venv/Scripts/python.exe .harness/spike/probe_m47_10_negative.py
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"

EDITOR = REPO / "src" / "rpa_core" / "gui" / "element_editor.py"
PANEL = REPO / "src" / "rpa_core" / "gui" / "element_panel.py"

EDITOR_TEST = "tests/contract/test_gui_element_editor.py"
PANEL_TEST = "tests/contract/test_gui_panels.py"

SENTINEL = "        pass  # [M47.10-NEGATIVE-INJECTED]"

ENV = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_pytest(target: str | list[str]) -> subprocess.CompletedProcess:
    targets = [target] if isinstance(target, str) else list(target)
    return subprocess.run(
        [str(PY), "-m", "pytest", *targets, "-p", "no:randomly"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=ENV,
    )


def counts(out: str) -> tuple[int, int]:
    failed = error = 0
    match = re.search(r"(\d+) failed", out)
    if match:
        failed = int(match.group(1))
    match = re.search(r"(\d+) error", out)
    if match:
        error = int(match.group(1))
    return failed, error


# ---------------------------------------------------------------------------
# 注入实现：每处返回 (目标文件, 注入后的源码, 期望变红的 nodeid 片段)
# ---------------------------------------------------------------------------

def inject_n1(_src: str) -> tuple[Path, str, str]:
    """N1：树与表不并排——把 QSplitter 换成纵向（退化回上下堆叠）。"""
    src = EDITOR.read_text(encoding="utf-8")
    old = "        splitter = QSplitter(Qt.Orientation.Horizontal)"
    new = "        splitter = QSplitter(Qt.Orientation.Vertical)"
    assert old in src, "N1 锚点未命中：splitter 构造行变了"
    return EDITOR, src.replace(old, new, 1), (
        "test_path_tree_and_attr_table_are_side_by_side"
    )


def inject_n2(_src: str) -> tuple[Path, str, str]:
    """N2：主选择器退回单行 QLineEdit（④ 的回归）。"""
    src = EDITOR.read_text(encoding="utf-8")
    old = '        self.css_edit = SelectorEdit(str(selector.get("css") or ""))'
    new = (
        '        from PySide6.QtWidgets import QLineEdit as _QE\n'
        '        self.css_edit = _QE(str(selector.get("css") or ""))'
    )
    assert old in src, "N2 锚点未命中：css_edit 构造行变了"
    return EDITOR, src.replace(old, new, 1), "test_selector_edit_is_multiline_and_grows"


def inject_n3(_src: str) -> tuple[Path, str, str]:
    """N3：重演信号遮蔽——把 textChanged 改成子类自实现的 Signal（不再连原生）。"""
    src = EDITOR.read_text(encoding="utf-8")
    old = "        self.document().contentsChanged.connect(self._sync_height)"
    new = (
        SENTINEL + "\n"
        "        self.textChanged = __import__('PySide6.QtCore', fromlist=['Signal']).Signal(str)"
    )
    assert old in src, "N3 锚点未命中：contentsChanged 连接行变了"
    return EDITOR, src.replace(old, new, 1), (
        "test_selector_edit_settext_emits_textchanged"
    )


def inject_n4(_src: str) -> tuple[Path, str, str]:
    """N4：高度不封顶——去掉 min(4, ...) 的上限。"""
    src = EDITOR.read_text(encoding="utf-8")
    old = "        rows = max(1, min(4, displayed or 1))"
    new = "        rows = max(1, (displayed or 1))"
    assert old in src, "N4 锚点未命中：rows 封顶行变了"
    return EDITOR, src.replace(old, new, 1), (
        "test_selector_edit_height_tracks_line_count"
    )


def inject_n5(_src: str) -> tuple[Path, str, str]:
    """N5：命中标签不去重——预览前缀「预览：」又回来。"""
    src = EDITOR.read_text(encoding="utf-8")
    old = '            text, color = "命中 1 个（页面上已黄框高亮）", SUCCESS'
    new = '            text, color = "预览：命中 1 个（页面上已黄框高亮）", SUCCESS'
    assert old in src, "N5 锚点未命中：命中 1 个文案变了"
    return EDITOR, src.replace(old, new, 1), "test_form_live_preview_dispatch_and_label"


INJECTIONS = (
    ("N1 树与属性表退回上下堆叠（① 不并排）", inject_n1, EDITOR_TEST),
    ("N2 主选择器退回单行 QLineEdit（④ 回归）", inject_n2, EDITOR_TEST),
    ("N3 重演信号遮蔽：setText 不 emit（③④ 的根因）", inject_n3, EDITOR_TEST),
    ("N4 高度不封顶（多行内容无限膨胀）", inject_n4, EDITOR_TEST),
    ("N5 命中标签又带「预览：」前缀（③ 不去重）", inject_n5, EDITOR_TEST),
)


def main() -> int:
    print("== M47.10 负向验证 ==")
    print("baseline：先确认原始文件绿")
    base = run_pytest([EDITOR_TEST, PANEL_TEST])
    bf, be = counts(base.stdout)
    if base.returncode != 0 or bf or be:
        print("  ✗ baseline 不绿，终止（先修绿再谈负向验证）")
        print(base.stdout[-2000:])
        return 1
    print(f"  ✓ baseline 绿（{len(base.stdout.splitlines())} 行输出，0 failed）")

    outcomes: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="m47-10-neg-"))
    try:
        for label, injector, target in INJECTIONS:
            path, new_src, expect_node = injector("")
            backup = tmp / (path.name + ".bak")
            shutil.copy2(path, backup)
            before = md5(path)
            path.write_text(new_src, encoding="utf-8")
            try:
                result = run_pytest(target)
            except subprocess.TimeoutExpired:
                outcomes.append((label, False, "超时＝挂住，不计通过"))
                continue
            failed, error = counts(result.stdout)
            hit_node = expect_node in result.stdout
            ok = (
                result.returncode != 0
                and failed > 0
                and error == 0
                and hit_node
            )
            note = (
                f"exit={result.returncode} failed={failed} error={error} "
                f"命中期望 nodeid={hit_node}"
            )
            outcomes.append((label, ok, note))
            # 逐字节还原 + 核 md5
            path.write_bytes(backup.read_bytes())
            after = md5(path)
            if after != before:
                outcomes.append((label + " · 还原", False, f"md5 不一致 {before}→{after}"))
            else:
                outcomes.append((label + " · 还原", True, f"md5 一致 {after}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n== 结果 ==")
    all_ok = True
    for label, ok, note in outcomes:
        mark = "✓" if ok else "✗"
        if not ok:
            all_ok = False
        print(f"  {mark} {label} — {note}")

    # 收尾再确认全绿（注入全部还原后）
    final = run_pytest([EDITOR_TEST, PANEL_TEST])
    ff, fe = counts(final.stdout)
    clean = final.returncode == 0 and not ff and not fe
    print(f"\n收尾：还原后 {'绿' if clean else '不绿（！）'}"
          f"（failed={ff} error={fe}）")
    if not clean:
        all_ok = False

    verdict = (
        "FULL NEGATIVE VERIFICATION PASSED"
        if all_ok
        else "NEGATIVE VERIFICATION FAILED"
    )
    print("\n" + verdict)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
