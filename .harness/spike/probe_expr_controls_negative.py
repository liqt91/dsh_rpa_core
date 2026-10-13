"""参数表达式控件（fx / Py）改动的负向验证探针。

被测改动（2026-10-13，维护者口径）：

  A. 门控**读 manifest 字段级** ``x-fx`` / ``x-python``（此前桌面端对所有纯 string
     字段一律给 fx、不看声明 ⇒ 与 Web 分化）。
  B. ``fx`` **不是开关**——点击即弹变量列表、选中直接插到光标处；「＋变量」中间按钮删掉。
  C. ``Py`` 是模式开关，与 fx **互斥**；``_exprModes`` 是运行期唯一真值。
  D. 标签删光 ⇒ **自动撤销** ``fx`` 态（否则字面 CSS 选择器 ``a[title]`` 会被当标签解析）。
  E.（M58）**编辑态双缓冲**：切模式存当前槽、取目标槽，来回切不丢内容；缓冲只活在
     面板内存，落盘仍是「模式 + 一份值」。
  F.（M58）``x-multiline`` ⇒ 多行输入区；表达式底色选择器要跟真实控件类型一致。
  G.（M58）Py 按钮灰/彩两态 + 加宽（此前只有 Qt 默认按下阴影，看不出点亮没有）。

十四个注入，每个打在**不同**判据上（方向必须落在门禁实际检查的地方）：

  I1  门控失效（所有 string 都给表达式行）   ⇒ 「按声明门控」的判据应红。
  I2  fx 插入变量**不置** ``fx`` 态          ⇒ 插入判据应红。
  I3  Py 选中**不置** ``python`` 态          ⇒ 互斥判据 + 依赖模式切换的新判据应红。
  I4  标签删光**不撤销** ``fx`` 态           ⇒ 撤销判据应红。
  I5  fx 按钮变回**可选中**（开关）          ⇒ 「不是开关」判据应红。
  I6  ``expr_modes()`` **不剪枝**             ⇒ 剪枝判据应红。
  I7  换槽后**不填回**目标槽                 ⇒ 「两份都还在」判据应红。
  I8  首次切换**不继承**（填空）             ⇒ 「继承」判据应红。
  I9  换槽后**不复位光标到末尾**             ⇒ 光标判据应红。
  I10 切回普通**不恢复 fx 态**               ⇒ 「标签还活着」判据应红。
  I11 ``x-multiline`` **不读声明**            ⇒ 多行判据应红。
  I12 多行样式选择器写死 ``QLineEdit``        ⇒ 选择器判据应红。
  I13 Py 两态样式丢失                        ⇒ 两态判据应红。
  I14 Py 不加宽                              ⇒ 加宽判据应红。

★ 哨兵 ``# [EXPR-CONTROLS-NEGATIVE]``（只动 Python，故只有一种后缀）。
★ 锚点唯一性自检（``count == 1``）：改错处会让「注入」静默落到别的函数上。
   实测被拦下两次：I4 的 ``pop`` 行出现两处（缩进不同）；I2 的 8 空格锚点是
   M58 新增的 12 空格 ``_restore_fx_mode_for_content`` 那一行的**子串**，都靠自检暴露。
★ 输出重定向到文件再读，绝不接管道（BrokenPipe 打断还原会把注入留在工作区）。
★ pytest 红名单从 **junitxml** 取且**不 unlink()**（删除守卫按 turn 累计、>50 即
  fail-closed），用 **mtime 变化**判本轮是否产了报告。
★ 期望红集**按推理补全**、不照抄实际：M58 的新判据大多围绕「模式切换」这一个机制，
  打到 I2/I3 时必然连带红，这些连带是预期内的，逐条写进期望集。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
XML = ROOT / ".harness" / "spike" / "_expr_controls_last.xml"
LOG = ROOT / ".harness" / "spike" / "_expr_controls_runs.txt"

F_PARAM = "src/rpa_core/gui/param_form.py"
FILES = (F_PARAM,)

SUITES = ["tests/contract/test_gui_expr_controls.py"]

SENTINEL = "# [EXPR-CONTROLS-NEGATIVE]"

# 判据名常量（junitxml 的 name 属性 = 函数名）
T_GATED = "test_expr_controls_gated_by_manifest_declaration"
T_UNDECLARED = "test_undeclared_string_field_gets_no_fx"
T_INSERT = "test_fx_inserts_tag_at_cursor_and_enters_fx_mode"
T_NOT_CHECKABLE = "test_fx_button_is_not_checkable"
T_EXCLUSIVE = "test_py_toggle_is_exclusive_with_fx"
T_DROP = "test_fx_mode_dropped_when_last_tag_removed"
T_PRUNED = "test_expr_modes_pruned_to_hosted_fields"
# M58 新增判据（同一机制 ⇒ 注入时按推理一并列入期望集，不照抄实际）
T_MULTI_COMPOSE = "test_multiline_and_expr_declarations_compose"
T_SWAP_KEEPS = "test_py_toggle_keeps_both_edits"
T_NO_LEAK = "test_slots_never_leak_into_saved_values"
T_TAGS_ALIVE = "test_switching_back_keeps_tags_alive"
T_MULTI_SWAP = "test_multiline_slot_swap_works_on_plain_text_edit"
T_MULTI_HL = "test_multiline_editor_expression_highlight_uses_its_own_selector"
T_INHERIT = "test_first_py_toggle_inherits_current_text"
T_SLOT_CURSOR = "test_slot_swap_moves_cursor_to_end"
T_MULTI_DECL = "test_multiline_declaration_switches_to_plain_text_edit"
T_PY_STYLE = "test_py_button_carries_two_state_style"
T_PY_WIDE = "test_py_button_is_widened_for_a_visible_state_block"
# 这条是「声明键真被读」的闸：注入把 get("x-multiline") 换成 False 时它会红
T_BOTH_READ_DECL = "test_both_editors_read_the_same_multiline_declaration"

_A_GATE = "            if allow_fx or allow_python:"
# M58 起 `_restore_fx_mode_for_content` 里也有一行 `_expr_modes[name] = "fx"`
# （12 空格），而 8 空格的锚点是它的**子串** ⇒ 必须带上一行上下文才唯一。
_A_FX_SET = (
    '        _insert_into_editor(editor, f"[{path}]")\n'
    '        self._expr_modes[name] = "fx"'
)
_A_PY_SET = '            self._expr_modes[name] = "python"'
_A_DROP = (
    "        if editor is None or has_tags(_editor_text(editor)):\n"
    "            return\n"
    "        self._expr_modes.pop(name, None)"
)
# M58 起 fx 按钮改带描边（去掉了 setAutoRaise），锚点跟着换到样式行
_A_FX_STYLE = "            fx_button.setStyleSheet(_FX_BUTTON_QSS)"
_A_PRUNE = (
    "        return {k: v for k, v in self._expr_modes.items() if k in self._expr_rows}"
)
# ---- M58 锚点 ----
_A_SLOT_FILL = "        _replace_editor_text(editor, slots[target])"
_A_SLOT_INHERIT = (
    "        if target not in slots:\n"
    "            slots[target] = slots[current]"
)
# 只打**多行**那半：QLineEdit.setText 本来就落在行尾，单行分支删掉不会红
_A_CURSOR_END = (
    "        cursor = editor.textCursor()\n"
    "        cursor.movePosition(QTextCursor.MoveOperation.End)\n"
    "        editor.setTextCursor(cursor)"
)
_A_RESTORE_FX = "            self._restore_fx_mode_for_content(name)"
# ★ 注入是**条件表达式**里的 `if`，后面不能带冒号（带冒号直接是语法错误、
#   变成收集期 error ⇒ 红名单里只有模块名，判不出「打到哪条判据」）
_A_MULTILINE_GATE = '                if field_schema.get("x-multiline")'
_A_SELECTOR = (
    '    selector = "QPlainTextEdit" if isinstance(editor, QPlainTextEdit) '
    'else "QLineEdit"'
)
_A_PY_QSS = "            py_button.setStyleSheet(_PY_BUTTON_QSS)"
_A_PY_WIDE_SET = "            py_button.setMinimumWidth(_PY_BUTTON_MIN_WIDTH)"

INJECTIONS = [
    (
        "I1",
        _A_GATE,
        f"            if True:  {SENTINEL}",
        {T_GATED, T_UNDECLARED, T_PRUNED},
        "门控失效：所有 string 字段都给表达式行 ⇒ 三处判据连带红",
    ),
    (
        "I2",
        _A_FX_SET,
        '        _insert_into_editor(editor, f"[{path}]")\n'
        f"        pass  {SENTINEL}",
        {T_INSERT, T_MULTI_COMPOSE},
        "fx 插入变量不置 fx 态 ⇒ 插入判据应红",
    ),
    (
        "I3",
        _A_PY_SET,
        f"            pass  {SENTINEL}",
        {T_EXCLUSIVE, T_SWAP_KEEPS, T_NO_LEAK, T_TAGS_ALIVE, T_MULTI_SWAP, T_MULTI_HL},
        "Py 选中不置 python 态 ⇒ 互斥判据 + 依赖模式切换的新判据连带红",
    ),
    (
        "I4",
        _A_DROP,
        "        if editor is None or has_tags(_editor_text(editor)):\n"
        f"            return  {SENTINEL}",
        {T_DROP},
        "标签删光不撤销 fx 态 ⇒ 撤销判据应红",
    ),
    (
        "I5",
        _A_FX_STYLE,
        f"            fx_button.setStyleSheet(_FX_BUTTON_QSS){SENTINEL}\n"
        "            fx_button.setCheckable(True)",
        {T_NOT_CHECKABLE},
        "fx 按钮变回可选中 ⇒ 「不是开关」判据应红",
    ),
    (
        "I6",
        _A_PRUNE,
        f"        return dict(self._expr_modes)  {SENTINEL}",
        {T_PRUNED},
        "expr_modes() 不剪枝 ⇒ 剪枝判据应红",
    ),
    (
        "I7",
        _A_SLOT_FILL,
        f"        pass  {SENTINEL}",
        {T_SWAP_KEEPS, T_SLOT_CURSOR, T_MULTI_SWAP, T_NO_LEAK},
        "换槽后不填回目标槽 ⇒ 「两份都还在 / 光标 / 多行 / 不泄漏」判据连带红",
    ),
    (
        "I8",
        _A_SLOT_INHERIT,
        f'        slots[target] = slots.get(target, "")  {SENTINEL}',
        {T_INHERIT, T_SWAP_KEEPS, T_MULTI_SWAP, T_SLOT_CURSOR},
        "首次切换不继承（填空）⇒ 「继承 / 两份都还在 / 多行 / 光标」判据连带红",
    ),
    (
        "I9",
        _A_CURSOR_END,
        f"        pass  {SENTINEL}",
        {T_SLOT_CURSOR},
        "换槽后不复位光标（多行控件）⇒ 光标判据应红",
    ),
    (
        "I10",
        _A_RESTORE_FX,
        f"            pass  {SENTINEL}",
        {T_TAGS_ALIVE},
        "切回普通不恢复 fx 态 ⇒ 「标签还活着」判据应红",
    ),
    (
        "I11",
        _A_MULTILINE_GATE,
        f"                if False  {SENTINEL}",
        {
            T_MULTI_DECL,
            T_MULTI_COMPOSE,
            T_MULTI_HL,
            T_MULTI_SWAP,
            T_BOTH_READ_DECL,
            T_SLOT_CURSOR,
        },
        "x-multiline 不读声明 ⇒ 四条多行判据 + 「声明键被读」闸 + 光标判据连带红",
    ),
    (
        "I12",
        _A_SELECTOR,
        f'    selector = "QLineEdit"  {SENTINEL}',
        {T_MULTI_HL},
        "多行样式选择器写死 QLineEdit ⇒ 选择器判据应红",
    ),
    (
        "I13",
        _A_PY_QSS,
        f"            pass  {SENTINEL}",
        {T_PY_STYLE},
        "Py 两态样式丢失 ⇒ 两态判据应红",
    ),
    (
        "I14",
        _A_PY_WIDE_SET,
        f"            pass  {SENTINEL}",
        {T_PY_WIDE},
        "Py 不加宽 ⇒ 加宽判据应红",
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
