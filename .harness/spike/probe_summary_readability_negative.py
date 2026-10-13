"""画布「参数摘要可读性」改动的负向验证探针。

被测改动（2026-10-13，维护者反馈「灰色参数小字看不清」+「+N 后缀看不懂」）：

  A. 摘要字号 10px → 11px，且字阶由 ``MONO_PX``（命令 id/快捷键用）改为
     ``SUB_PX``（次级说明）—— ``mono_font`` 新增 ``size`` 关键字参数。
  B. 摘要末尾的 ``+N``（还有 N 个参数没展示）**移除**（解释成本高于收益；
     Web 端 ``app.js::summarize`` 本来就没有此后缀）。

四个注入，每个打在**不同**判据上（方向必须落在门禁实际检查的地方）：

  I1 canvas.py 摘要回 ``mono_font(option.font)`` ⇒ 接线失效，退回 10px。
  I2 fonts.py ``mono_font`` 忽略 ``size``        ⇒ 纯函数层失效（传了也不生效）。
  I3 flow_model.py 把 ``+N`` 加回去               ⇒ 折叠后缀回归。
  I4 fonts.py ``SUB_PX`` 调回 10                  ⇒ 需求本身（≥11px）被破坏。

★ 哨兵 ``# [SUMMARY-READABILITY-NEGATIVE]``（只动 Python，故只有一种后缀）。
★ 锚点唯一性自检（``count == 1``）：改错处会让「注入」静默落到别的函数上。
★ 输出重定向到文件再读，绝不接管道（M48：BrokenPipe 打断还原会把注入留在工作区）。
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
XML = ROOT / ".harness" / "spike" / "_summary_readability_last.xml"
LOG = ROOT / ".harness" / "spike" / "_summary_readability_runs.txt"

F_FONTS = "src/rpa_core/gui/fonts.py"
F_CANVAS = "src/rpa_core/gui/canvas.py"
F_MODEL = "src/rpa_core/gui/flow_model.py"

FILES = (F_FONTS, F_CANVAS, F_MODEL)

SUITES = [
    "tests/contract/test_gui_fonts.py",
    "tests/contract/test_gui_canvas.py",
    "tests/contract/test_gui_canvas_i18n.py",
    "tests/contract/test_gui_param_form.py",
]

SENTINEL = "# [SUMMARY-READABILITY-NEGATIVE]"

# 判据名常量（junitxml 的 name 属性 = 函数名）
T_WIRING = "test_canvas_summary_uses_sub_px_not_mono_px"
T_SIZE_KW = "test_mono_font_accepts_explicit_size"
T_SUB_MIN = "test_summary_font_is_at_least_11px"
T_LIMIT_CANVAS = "test_summarize_args_limits_pairs_and_truncates"
T_LIMIT_FORM = "test_summarize_args_limit_respected"

_ANCHOR_FONTS_SIZE = "    font.setPixelSize(MONO_PX if size is None else size)"
_ANCHOR_SUB_PX = "SUB_PX = 11"
_ANCHOR_CANVAS = "                painter.setFont(fonts.mono_font(option.font, size=fonts.SUB_PX))"
_ANCHOR_JOIN = '    return "  ".join(pairs)'

INJECTIONS = [
    (
        "I1",
        F_CANVAS,
        _ANCHOR_CANVAS,
        f"                painter.setFont(fonts.mono_font(option.font))  {SENTINEL}",
        {T_WIRING},
        "画布摘要退回 mono_font 缺省字阶（MONO_PX 10px）⇒ 接线失效",
    ),
    (
        "I2",
        F_FONTS,
        _ANCHOR_FONTS_SIZE,
        f"    font.setPixelSize(MONO_PX)  {SENTINEL}",
        {T_SIZE_KW},
        "mono_font 忽略 size ⇒ 纯函数层失效（传了也不生效）",
    ),
    (
        "I3",
        F_MODEL,
        _ANCHOR_JOIN,
        f'    extra = len(with_args) - limit\n'
        f'    if extra > 0:\n'
        f'        pairs.append(f"+{{extra}}")\n'
        f'    return "  ".join(pairs)  {SENTINEL}',
        {T_LIMIT_CANVAS, T_LIMIT_FORM},
        "把 +N 折叠后缀加回去 ⇒ 两端判据都应红",
    ),
    (
        "I4",
        F_FONTS,
        _ANCHOR_SUB_PX,
        f"SUB_PX = 10  {SENTINEL}",
        {T_SUB_MIN},
        "SUB_PX 调回 10 ⇒ 「不得小于 11px」的需求锚失效",
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
        for tag, rel, old, new, expected, note in INJECTIONS:
            restore(originals)
            path = ROOT / rel
            text = path.read_text(encoding="utf-8")
            count = text.count(old)
            if count != 1:
                log(f"[{tag}] 锚点出现 {count} 次（须恰为 1）⇒ 注入无效，中止：{rel}")
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
