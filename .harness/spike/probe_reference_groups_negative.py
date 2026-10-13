"""fx 变量菜单「只列用户命名的变量」改动的负向验证探针。

被测改动（2026-10-13，维护者口径）：

  「内置的不用展示出来，只需要展示流程输入的变量，和每一步明确定义了是输出变量的
   变量（就是有变量名的）；循环变量/错误变量有名字就列。」

⇒ ``MainWindow._reference_groups`` 只产三类：流程参数 ``inputs.*``、输出别名 +
变量写入目标、显式命名的 ``loop.<item_var>`` / ``<error_var>.code``；``steps.*``
与内置作用域根名不进菜单。菜单按组渲染，组标题是不可点的灰行，空组不渲染。

七个注入，每个打在**不同**判据上（方向必须落在门禁实际检查的地方）：

  G1 自动路径 `steps.*` / 内置作用域**回流**        ⇒ 排除判据应红；
    连带「循环与错误变量」组的内容判据（它断言该组**精确内容**，注入的 `loop.item`
    会进 `scoped`）——**推理应含**，非「把模块打倒」。
  G2 变量写入退回**硬编码命令 id**（不认 x-var-write）⇒ 目标名判据应红（getCell 漏掉）。
  G3 循环/错误变量判据**放宽成「raw 里有键」**       ⇒ 命名判据应红（内置名回流）。
  G4 空组**不剔除**（provider 侧）                  ⇒ 两条标签判据应红。
  G5 组标题**变成可点**                            ⇒ 分组判据应红。
  G6 空组**不剔除**（表单侧）                       ⇒ 空组分隔条判据应红。
  G7 无变量时**不给占位灰行**                       ⇒ 占位判据应红。

★ 哨兵 ``# [REF-GROUPS-NEGATIVE]``；每个注入自带 ``(文件, old, new)``。
★ 锚点唯一性自检（``count == 1``）：改错处会让「注入」静默落到别的函数上。
★ 输出重定向到文件再读，绝不接管道（BrokenPipe 打断还原会把注入留在工作区）。
★ pytest 红名单从 **junitxml** 取且**不 unlink()**（删除守卫按 turn 累计、>50 即
  fail-closed），用 **mtime 变化**判本轮是否产了报告。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
XML = ROOT / ".harness" / "spike" / "_ref_groups_last.xml"
LOG = ROOT / ".harness" / "spike" / "_ref_groups_runs.txt"

F_APP = "src/rpa_core/gui/app.py"
F_PARAM = "src/rpa_core/gui/param_form.py"
FILES = (F_APP, F_PARAM)

SUITES = [
    "tests/contract/test_gui_expr_controls.py",
    "tests/contract/test_gui_param_form.py",
    "tests/contract/test_gui_flow_inputs_linkage.py",
]

SENTINEL = "# [REF-GROUPS-NEGATIVE]"

# 判据名常量（junitxml 的 name 属性 = 函数名）
T_EXCLUDE = "test_reference_groups_exclude_auto_paths_and_builtins"
T_NAMED = "test_reference_groups_list_named_outputs_and_var_writes"
T_LOOP = "test_reference_groups_list_only_explicitly_named_loop_and_error_vars"
T_DROP_INPUTS = "test_reference_groups_drop_deleted_inputs"
T_HEADERS = "test_variable_menu_renders_group_headers_as_disabled_rows"
T_SKIP_EMPTY = "test_variable_menu_skips_empty_groups_and_their_separators"
T_PLACEHOLDER = "test_variable_menu_shows_placeholder_when_nothing_is_named"

_A_GROUPS = "        groups = ["
_A_VAR_WRITE = (
    "            var_write = manifest.x_var_write if manifest is not None else None"
)
_A_ITEM_VAR = (
    "            if isinstance(item_var, str) and item_var != default_item_var:\n"
    '                scoped.append(f"loop.{item_var}")\n'
    '            error_var = raw.get("error_var")\n'
    "            if isinstance(error_var, str) and error_var != default_error_var:"
)
_A_RETURN = "        return [(label, paths) for label, paths in groups if paths]"
_A_HEADER = "            menu.addAction(label).setEnabled(False)"
_A_MENU_FILTER = "        groups = [(label, paths) for label, paths in groups if paths]"
_A_PLACEHOLDER = '            menu.addAction("（无可引用变量）").setEnabled(False)'

INJECTIONS = [
    (
        "G1",
        F_APP,
        _A_GROUPS,
        "        variables.append('steps.n1.outputs.text')  " + SENTINEL
        + "\n        scoped.append('loop.item')\n"
        + _A_GROUPS,
        {T_EXCLUDE, T_LOOP},
        "steps.* 与内置作用域回流 ⇒ 排除判据 + 该组精确内容判据应红",
    ),
    (
        "G2",
        F_APP,
        _A_VAR_WRITE,
        "            var_write = ("
        "{'field': 'varName'} if command_id == 'data.setVar' else None"
        f")  {SENTINEL}",
        {T_NAMED},
        "变量写入退回硬编码命令 id ⇒ data.table.getCell 的目标名漏掉",
    ),
    (
        "G3",
        F_APP,
        _A_ITEM_VAR,
        "            if isinstance(item_var, str):"
        f"  {SENTINEL}\n"
        '                scoped.append(f"loop.{item_var}")\n'
        '            error_var = raw.get("error_var")\n'
        "            if isinstance(error_var, str):",
        {T_LOOP},
        "循环/错误变量判据放宽成「raw 里有键」⇒ 内置名回流",
    ),
    (
        "G4",
        F_APP,
        _A_RETURN,
        f"        return list(groups)  {SENTINEL}",
        {T_DROP_INPUTS, T_LOOP},
        "provider 侧空组不剔除 ⇒ 空标题留在菜单里",
    ),
    (
        "G5",
        F_PARAM,
        _A_HEADER,
        f"            menu.addAction(label)  {SENTINEL}",
        {T_HEADERS},
        "组标题变成可点 ⇒ 分组判据应红",
    ),
    (
        "G6",
        F_PARAM,
        _A_MENU_FILTER,
        f"        groups = list(groups)  {SENTINEL}",
        {T_SKIP_EMPTY},
        "表单侧空组不剔除 ⇒ 空标题 + 孤立分隔条",
    ),
    (
        "G7",
        F_PARAM,
        _A_PLACEHOLDER,
        f"            pass  {SENTINEL}",
        {T_PLACEHOLDER},
        "无变量时不给占位灰行 ⇒ 弹出空菜单",
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
    log("baseline md5: " + ", ".join(f"{h[:8]}" for h in before_md5.values()))

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
        for tag, rel, old, new, expected, note in INJECTIONS:
            restore(originals)
            path = ROOT / rel
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
