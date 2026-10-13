"""参数行形态「标签一行 / 控件一行」（M59）的负向验证探针。

被测改动（维护者 2026-10-13：「参数设置侧边栏，感觉要根据侧边栏宽度来设计参数名标签
和填写区域……参数名标签、py按钮、fx按钮放一行，参数填写放第二行」）：

  A. 参数行改用**字段束**（``_field_bundle``：标签一行 + 控件一行，横跨两列），
     替掉原来的 QFormLayout 双列。双列里标签列按全表最宽标签定宽、从每一行扣除，
     300px 侧边栏里固定吃掉 88px ⇒ 带两个按钮的字段输入框只剩 **76px**。
     改后实测同一字段 **284px**，表单最小宽 347 → 260px。
  B. Python / 变量 两个按钮搬到**标签行右端**（原来夹在输入框两侧）。
  C. 按钮文案由缩写改详细名（``Py`` → ``Python``、``fx`` → ``变量``），且**预算有限**
     ——标签与按钮都不能缩到 sizeHint 以下，三者相加就是这一行的最小宽。
  D. 控制流表单（``ControlNodeForm``）同口径改造，并补上同样的外层弹性空隙。
  E. ``x-multiline`` 的字段继续保持多行输入区（回归保护，见 M58）。

四个注入，每个打在**不同**判据上（方向必须落在门禁实际检查的地方）：

  I15 字段束内改回**横向**布局（标签与控件同排） ⇒ 「标签在上」三条 + 「不裁字」一条（横向
      一排又把按钮挤扁）共四条判据应红。
  I16 Python 按钮**不放标签行**（塞进字段束的下一行） ⇒ 「按钮与标签同行」判据应红。
  I17 按钮文案**超预算**（``Python`` → ``Python 表达式``）⇒ 「默认侧边栏下不裁字」判据应红。
  I18 去掉 ParamForm 末尾的**弹性空隙** ⇒ 「行不被剩余高度撑开」两条判据应红。
      ★ 只有含 QPlainTextEdit 的行会被撑开（它的 sizePolicy 是 Expanding，而
      ``setFixedHeight`` 不改 sizePolicy）；元素库行 / 单行行**没有**这个信号。
      本探针首版给元素库行写过一条断言，I18 判为「未命中」⇒ 那条是**恒绿假判据**，
      已删并换成「真机命令」版本（见 tests/contract/test_gui_param_form.py）。

★ 哨兵 ``# [M59-ROW-LAYOUT-NEGATIVE]``（只动 Python，故只有一种后缀）。
★ 锚点唯一性自检（``count == 1``）：改错处会让「注入」静默落到别的函数上。
  本轮的坑：``outer.addStretch(1)`` 在 ParamForm 与 ControlNodeForm 里**各有一处**，
  裸锚点 count == 2 ⇒ 必须带上后面那行 ``properties = ...`` 才唯一。
★ 输出重定向到文件再读，绝不接管道（BrokenPipe 打断还原会把注入留在工作区）。
★ pytest 红名单从 **junitxml** 取且**不 unlink()**（删除守卫按 turn 累计、>50 即
  fail-closed），用 **mtime 变化**判本轮是否产了报告。
★ 期望红集**按推理补全**、不照抄实际。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
XML = ROOT / ".harness" / "spike" / "_m59_last.xml"
LOG = ROOT / ".harness" / "spike" / "_m59_runs.txt"

F_PARAM = "src/rpa_core/gui/param_form.py"
FILES = (F_PARAM,)

SUITES = ["tests/contract/test_gui_param_form.py"]

SENTINEL = "# [M59-ROW-LAYOUT-NEGATIVE]"

# 判据名常量（junitxml 的 name 属性 = 函数名）
T_STRETCH = "test_multiline_expr_row_does_not_absorb_leftover_panel_height"
T_LABEL_ABOVE = "test_param_row_puts_label_above_control"
T_BUTTONS_ON_HEAD = "test_expr_buttons_share_the_label_row"
T_REAL_MULTILINE = "test_real_commands_multiline_row_does_not_absorb_leftover_panel_height"
T_CONTROL_SHAPE = "test_control_form_rows_use_the_same_bundle_shape"
T_TEXT_BUDGET = "test_expr_button_text_fits_the_default_sidebar"

_A_BUNDLE_BOX = "    box = QVBoxLayout(bundle)"
_A_PY_HEAD = "            head.addWidget(py_button)"
_A_PY_TEXT = '_PY_BUTTON_TEXT = "Python"'
# ★ 裸 `outer.addStretch(1)` 在 ParamForm 与 ControlNodeForm 里各有一处（count == 2），
#   必须带上后面那行 `properties = ...` 才唯一——只打 ParamForm，ControlNodeForm 不动。
_A_STRETCH = (
    "        outer.addLayout(self._form)\n"
    "        outer.addStretch(1)\n"
    "\n"
    '        properties = schema.get("properties", {})'
)

INJECTIONS = [
    (
        "I15",
        _A_BUNDLE_BOX,
        f"    box = QHBoxLayout(bundle)  {SENTINEL}",
        {T_LABEL_ABOVE, T_BUTTONS_ON_HEAD, T_CONTROL_SHAPE, T_TEXT_BUDGET},
        "字段束改回横向布局（标签与控件同排）⇒ 三条「标签在上」判据 + "
        "「默认侧边栏下不裁字」连带红（横向一排又把按钮挤扁了）",
    ),
    (
        "I16",
        _A_PY_HEAD,
        f"            parent.layout().addWidget(py_button)  {SENTINEL}",
        {T_BUTTONS_ON_HEAD},
        "Python 按钮不放标签行（落到字段束的下一行）⇒ 「按钮与标签同行」判据应红",
    ),
    (
        "I17",
        _A_PY_TEXT,
        f'_PY_BUTTON_TEXT = "Python 表达式"  {SENTINEL}',
        {T_TEXT_BUDGET},
        "按钮文案超预算（Python → Python 表达式）⇒ 「默认侧边栏下不裁字」判据应红",
    ),
    (
        "I18",
        _A_STRETCH,
        "        outer.addLayout(self._form)\n"
        f"        outer.addStretch(0)  {SENTINEL}\n"
        "\n"
        '        properties = schema.get("properties", {})',
        {T_STRETCH, T_REAL_MULTILINE},
        "去掉表单末尾弹性空隙 ⇒ 两条「行不被剩余高度撑开」判据连带红"
        "（实测 data.setVar.value 89→224px、handleDialog.promptText 86→270px）",
    ),
]


def md5(rel: str) -> str:
    return hashlib.md5((ROOT / rel).read_bytes()).hexdigest()


def run_suite() -> tuple[int, dict[str, str], int]:
    """跑 pytest，红名单从 junitxml 取（不 unlink）。返回 (rc, {用例名: 类型}, passed)。"""
    before = XML.stat().st_mtime_ns if XML.exists() else None
    try:
        proc = subprocess.run(
            [
                str(PY), "-m", "pytest", *SUITES, "--no-header", "--tb=no",
                f"--junitxml={XML}", "-p", "no:cacheprovider",
            ],
            cwd=ROOT, capture_output=True, text=True, timeout=900,
        )
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        return -1, {"<TIMEOUT 挂住>": "timeout"}, 0

    reds: dict[str, str] = {}
    passed = 0
    fresh = XML.exists() and (before is None or XML.stat().st_mtime_ns != before)
    if fresh:
        for case in ET.parse(XML).iter("testcase"):
            hit = next((c for c in list(case) if c.tag in ("failure", "error")), None)
            if hit is not None:
                reds[case.get("name") or "?"] = hit.tag
            else:
                passed += 1
    return rc, reds, passed


def restore(originals: dict[str, bytes]) -> None:
    for rel, blob in originals.items():
        (ROOT / rel).write_bytes(blob)


def has_sentinel() -> list[str]:
    return [rel for rel in FILES if SENTINEL in (ROOT / rel).read_text(encoding="utf-8")]


def main() -> int:
    lines: list[str] = []

    def log(text: str) -> None:
        print(text)
        lines.append(text)

    # ① 起手哨兵自检：带着上轮注入就拒绝运行（不靠人记性）
    dirty = has_sentinel()
    if dirty:
        log(f"ABORT：这些文件仍带哨兵，先还原：{dirty}")
        return 2

    originals = {rel: (ROOT / rel).read_bytes() for rel in FILES}
    before_md5 = {rel: md5(rel) for rel in FILES}
    log("baseline md5: " + ", ".join(f"{rel}={h[:8]}" for rel, h in before_md5.items()))

    # ② 对照跑：必须绿（pytest 收尾崩溃按「有界重跑一次」处置）
    rc, reds, passed = run_suite()
    if reds == {} and passed == 0:
        log(f"[BASELINE] rc={rc} 未收集到用例，重跑一次")
        rc, reds, passed = run_suite()
    log(f"[BASELINE] rc={rc} passed={passed} reds={sorted(reds)}")
    if reds or passed == 0:
        log("对照不绿（或 0 收集）⇒ 先查基线，不继续注入")
        return 2

    failures = 0
    try:
        for tag, old, new, expected, note in INJECTIONS:
            restore(originals)
            path = ROOT / F_PARAM
            text = path.read_text(encoding="utf-8")
            count = text.count(old)
            if count != 1:
                log(f"[{tag}] 锚点出现 {count} 次（须恰为 1）⇒ 注入无效，中止")
                failures += 1
                break
            path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
            if SENTINEL not in path.read_text(encoding="utf-8"):
                log(f"[{tag}] 注入未写入文件，中止")
                failures += 1
                break

            rc, reds, passed = run_suite()
            actual = set(reds)
            extra = sorted(actual - expected)
            missed = sorted(expected - actual)
            ok = not extra and not missed and passed > 0
            flag = "OK" if ok else "MISMATCH"
            log(
                f"[{tag}] {flag} rc={rc} passed={passed} 期望红={len(expected)} "
                f"实际红={len(actual)} | {note}"
            )
            log(f"       实际: {sorted(actual)}")
            if missed:
                log(f"       ★ 未命中（判据没打到）: {missed}")
            if extra:
                log(f"       ★ 多红（连带影响，需分辨）: {extra}")
            if not ok:
                failures += 1
    finally:
        # ③ 逐字节还原
        restore(originals)

    after_md5 = {rel: md5(rel) for rel in FILES}
    same = after_md5 == before_md5
    log(f"[RESTORE] md5 一致={same}")
    if not same:
        for rel in FILES:
            if before_md5[rel] != after_md5[rel]:
                log(f"   ★ {rel} 未还原：{before_md5[rel][:8]} -> {after_md5[rel][:8]}")
        failures += 1

    rc, reds, passed = run_suite()
    log(f"[AFTER-RESTORE] rc={rc} passed={passed} reds={sorted(reds)}")
    if reds or passed == 0:
        log("还原后判据不绿 ⇒ 留下坏状态，必须查")
        failures += 1

    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    log(f"\n== 负向验证{'全部通过' if failures == 0 else f'有 {failures} 处问题'} ==")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
