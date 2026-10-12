"""M54 S1 负向验证探针（标准流程：起手哨兵自检 → 对照绿 → 注入精确红 → 逐字节还原核 md5）。

被测改动：GUI 参数面板字段标签接上 ``devserver/static/i18n.js`` 的中文层（M54 S1/G2/G4）。

五个注入，每个打在一个**不同**的判据组上（方向必须打在门禁实际检查的地方）：

  I1 i18n.py   `field_label` 恒返回键名       ⇒ 中文化层本体失效（覆盖/回退全没了）。
  I2 i18n.py   `GUI_LABEL_OVERRIDES` 改回「选择器」⇒ 元素槽位的**有意口径分歧**被抹平。
  I3 param_form `CONDITION_OP_LABELS` 手抄回一份 ⇒ ops 与 i18n.js 脱钩（G4 回归）。
  I4 param_form `_attach` 的 label 回裸键名    ⇒ 面板**没接线**（纯函数对、界面没用上）。
  I5 i18n.js   删掉 `flowDir` 标签             ⇒ 全覆盖门禁（S3）抓「新增字段忘配中文」。

★ 哨兵 `# [M54-I18N-NEGATIVE]`（Python）与 `// [M54-I18N-NEGATIVE]`（JS）——**按后缀取**。
★ 输出重定向到文件再读，绝不接管道（M48：BrokenPipe 打断还原会把注入留在工作区）。
★ pytest 计数从 **junitxml** 取且**不 unlink()**（删除守卫按 turn 累计、>50 即 fail-closed）。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
XML = ROOT / ".harness" / "spike" / "_m54_i18n_last.xml"
LOG = ROOT / ".harness" / "spike" / "_m54_i18n_runs.txt"

F_I18N = "src/rpa_core/gui/i18n.py"
F_FORM = "src/rpa_core/gui/param_form.py"
F_JS = "src/rpa_core/devserver/static/i18n.js"
C_CHECK = "commands/browser/check.json"
C_CLICK = "commands/browser/click.json"

FILES = (F_I18N, F_FORM, F_JS, C_CHECK, C_CLICK)

SUITES = [
    "tests/contract/test_gui_i18n.py",
    "tests/contract/test_gui_param_form.py",
    "tests/contract/test_gui_panels.py",
]

PY_SENTINEL = "# [M54-I18N-NEGATIVE]"
JS_SENTINEL = "// [M54-I18N-NEGATIVE]"
# JSON 不能写注释 ⇒ 哨兵做成一个**多余的枚举标签键**（本身就是「死标签」注入），
# 起手自检统一按子串 "M54-I18N-NEGATIVE" 找（三类文件都命中）。
JSON_SENTINEL = '"__M54-I18N-NEGATIVE__": "哨兵"'

# 判据名常量（junitxml 的 name 属性 = 函数名）
T_FALLBACK = "test_field_label_falls_back_three_levels"
T_SCOPED = "test_command_scoped_labels_differ_for_same_key"
T_YINGDAO = "test_element_slot_uses_yingdao_wording"
T_OPS = "test_ops_labels_come_from_i18n_js"
T_COVER = "test_every_manifest_field_has_chinese_label"
T_ENUM = "test_every_enum_field_has_value_labels"
T_RENDER = "test_param_form_labels_come_from_i18n"
T_STAR = "test_required_field_label_has_star"
T_REFNAME = "test_param_form_referenced_param_shows_only_element_name"

INJECTIONS = [
    (
        "I1",
        F_I18N,
        '    """字段中文标签：命令级覆盖 → 公共键 → 键名原文 → GUI 有意分歧覆盖。"""\n    data = _load()',
        '    """字段中文标签：命令级覆盖 → 公共键 → 键名原文 → GUI 有意分歧覆盖。"""\n'
        f"    return key  {PY_SENTINEL}\n    data = _load()",
        {T_FALLBACK, T_SCOPED, T_YINGDAO, T_COVER, T_RENDER, T_REFNAME},
        "field_label 恒返回键名 ⇒ 中文化层本体失效",
    ),
    (
        "I2",
        F_I18N,
        '    "selector": "操作目标",',
        f'    "selector": "选择器",  {PY_SENTINEL}',
        # T_RENDER 的字面锚点里就写着「操作目标 *」⇒ 口径被改回必然连带它红（可推理，非意外）
        {T_YINGDAO, T_REFNAME, T_RENDER},
        "元素槽位口径被改回与 Web 一致（有意分歧被抹平）",
    ),
    (
        "I3",
        F_FORM,
        "CONDITION_OP_LABELS: dict[str, str] = load_op_labels()",
        f'CONDITION_OP_LABELS: dict[str, str] = dict(load_op_labels(), truthy="为真")  {PY_SENTINEL}',
        {T_OPS},
        "ops 标签与 i18n.js 脱钩（G4 回归手抄）",
    ),
    (
        "I4",
        F_FORM,
        "        display = field_label(name, self._command_id)",
        f"        display = name  {PY_SENTINEL}",
        {T_RENDER, T_STAR},
        "面板没接线：label 回到裸键名（纯函数对、界面没用上）",
    ),
    (
        "I5",
        F_JS,
        '    flowDir: "流程目录",',
        f"    {JS_SENTINEL}",
        {T_COVER},
        "i18n.js 少一个字段标签 ⇒ 全覆盖门禁",
    ),
    (
        "I6",
        C_CHECK,
        '"x-enum-labels": {"check": "勾选", "uncheck": "取消勾选", "toggle": "反选"}',
        '"x-enum-labels": {"check": "勾选", "uncheck": "取消勾选", "toggle": "反选", '
        + JSON_SENTINEL
        + "}",
        {T_ENUM},
        "枚举标签多一个键（死标签：枚举里没这个值）",
    ),
    (
        "I7",
        C_CLICK,
        '"x-enum-labels": {"single": "单击", "double": "双击"}',
        '"x-enum-labels": {"single": "单击", ' + JSON_SENTINEL + "}",
        {T_ENUM},
        "枚举标签少一个键（UI 里会露出英文原值）",
    ),
]


def md5(rel: str) -> str:
    return hashlib.md5((ROOT / rel).read_bytes()).hexdigest()


def run_suite() -> tuple[int, dict[str, str], int]:
    """跑 pytest，红名单从 junitxml 取（不 unlink）。返回 (rc, {用例名: failure|error}, passed)。"""
    before = XML.stat().st_mtime_ns if XML.exists() else None
    try:
        proc = subprocess.run(
            [str(PY), "-m", "pytest", *SUITES, "--no-header", "--tb=no",
             f"--junitxml={XML}", "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True, timeout=900,
        )
        rc = proc.returncode
        out = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        return -1, {"<TIMEOUT 挂住>": "timeout"}, 0

    reds: dict[str, str] = {}
    passed = 0
    fresh = XML.exists() and (before is None or XML.stat().st_mtime_ns != before)
    if fresh:
        for case in ET.parse(XML).iter("testcase"):
            children = list(case)
            hit = next((c for c in children if c.tag in ("failure", "error")), None)
            if hit is not None:
                reds[case.get("name") or "?"] = hit.tag
            else:
                passed += 1
    return rc, reds, passed


def restore(originals: dict[str, bytes]) -> None:
    for rel, blob in originals.items():
        (ROOT / rel).write_bytes(blob)


def has_sentinel() -> list[str]:
    """三类文件的哨兵都含 `M54-I18N-NEGATIVE`（py 注释 / js 注释 / json 死标签键）。"""
    return [
        rel
        for rel in FILES
        if "M54-I18N-NEGATIVE" in (ROOT / rel).read_text(encoding="utf-8")
    ]


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

    # ② 对照跑：必须绿
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
            if old not in text:
                log(f"[{tag}] 锚点未命中 ⇒ 注入无效，中止：{rel}")
                failures += 1
                break
            path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
            if "M54-I18N-NEGATIVE" not in path.read_text(encoding="utf-8"):
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
                f"[{tag}] {flag} rc={rc} passed={passed} 期望红={len(expected)} 实际红={len(actual)} | {note}"
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
