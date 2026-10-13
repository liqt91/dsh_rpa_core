"""M56 负向验证探针（标准流程：起手哨兵自检 → 对照绿 → 注入精确红 → 逐字节还原核 md5）。

被测改动：GUI **画布**中文化——卡片标题显示命令中文名（与 Web ``commandName``
同口径）、参数摘要键名走 ``i18n.field_label``；三条产出路径（装载/新增/粘贴）
收敛到同一权威函数。

八个注入，每个打在一个**不同**的判据组上（方向必须打在门禁实际检查的地方）：

  I1 flow_model `command_label` 恒返回 id    ⇒ 标题中文化层**本体**失效。
  I2 flow_model `build_item` 标题回 command_id ⇒ 画布**没接线**（纯函数对、界面没用上）。
  I3 flow_model `summarize_args` 键名回裸键   ⇒ 摘要中文化失效（跨 5 个文件的多条判据）。
  I4 flow_model 摘要丢 `command_id`           ⇒ **命令级覆盖**失效（data.setVar.varName）。
  I5 app.py 参数应用路径丢 `command_id`       ⇒ 改完参数摘要退回裸键名（AST 闸）。
  I6 app.py 元素引用路径丢 `command_id`       ⇒ 同上，另一条产出路径（AST 闸）。
  I7 flow_model `create_action_item` 走恒等 label ⇒ **单条**产出路径漂移（三路一致判据）。
  I8 app.py 搜索文本摘掉 ROLE_COMMAND_ID      ⇒ 标题改中文后英文 id 搜不到。

★ 哨兵 `# [M56-CANVAS-I18N-NEGATIVE]`（本探针只动 Python，故只有一种后缀）。
★ 输出重定向到文件再读，绝不接管道（M48：BrokenPipe 打断还原会把注入留在工作区）。
★ pytest 红名单从 **junitxml** 取且**不 unlink()**（删除守卫按 turn 累计、>50 即 fail-closed），
  用 **mtime 变化**判本轮是否产了报告。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
XML = ROOT / ".harness" / "spike" / "_m56_canvas_i18n_last.xml"
LOG = ROOT / ".harness" / "spike" / "_m56_canvas_i18n_runs.txt"

F_MODEL = "src/rpa_core/gui/flow_model.py"
F_APP = "src/rpa_core/gui/app.py"

FILES = (F_MODEL, F_APP)

SUITES = [
    "tests/contract/test_gui_canvas_i18n.py",
    "tests/contract/test_gui_canvas.py",
    "tests/contract/test_gui_canvas_batch.py",
    "tests/contract/test_gui_param_form.py",
    "tests/contract/test_gui_panels.py",
]

SENTINEL = "# [M56-CANVAS-I18N-NEGATIVE]"

# 判据名常量（junitxml 的 name 属性 = 函数名）
T_TITLE = "test_canvas_action_title_is_chinese"
T_SUMKEYS = "test_canvas_summary_keys_use_chinese_labels"
T_CMDLVL = "test_summary_prefers_command_level_override"
T_PATHS = "test_all_production_paths_agree_on_title_and_summary"
T_APPGATE = "test_app_every_summarize_args_call_passes_command_id"
T_SEARCH = "test_canvas_search_text_still_matches_english_id"
# 既有判据（M56 改了它们断的口径，也一并纳入期望集）
T_RWF = "test_real_workflow_maps_to_sequence_with_actions"
T_LIMIT = "test_summarize_args_limits_pairs_and_truncates"
T_PRIO = "test_summarize_args_prioritizes_url_and_selector"
T_SEL1 = "test_summarize_args_shows_selector_before_other_fields"
T_LIM1 = "test_summarize_args_limit_respected"
T_APPLY = "test_selecting_action_card_shows_form_and_apply_updates_item"
# 按**英文命令 id** 搜索的契约面：画布搜索文本含 command id，故「data.」前缀能命中。
# 摘掉 command id ⇒ 这三条必然红（首轮探针把它们报成「多红」，取证后确认是**可推理的
# 连带**、不是真缺陷：`test_canvas_search_finds_and_cycles` 与
# `..._recomputed_after_structural_change` 都用 `_on_canvas_search_changed("data.")`
# 断言有命中；`T_SEARCH` 还额外汇总了「中文名也可搜」。）
T_OLDSRCH = "test_canvas_search_text_includes_command_and_args"
T_CYCLE = "test_canvas_search_finds_and_cycles"
T_RECOMP = "test_canvas_search_matches_are_recomputed_after_structural_change"

_ANCHOR_CMD = "    return load_command_display_names().get(command_id, command_id)"
_ANCHOR_TITLE = "        title = (label or command_label)(command_id) or command_id"
_ANCHOR_PAIRS = '        pairs.append(f"{field_label(key, command_id)}={text}")'
_ANCHOR_APPLY = "                summarize_args(values, command_id=item.data(ROLE_COMMAND_ID)),"
_ANCHOR_ELEM = "            summarize_args(holder.args, command_id=item.data(ROLE_COMMAND_ID)),"
_ANCHOR_ACTION = "        return build_item(raw)"
_ANCHOR_SEARCH = '            str(item.data(ROLE_COMMAND_ID) or ""),'

INJECTIONS = [
    (
        "I1",
        F_MODEL,
        _ANCHOR_CMD,
        f"    return command_id  {SENTINEL}",
        {T_TITLE, T_PATHS, T_SEARCH},
        "command_label 恒返回 id ⇒ 标题中文化本体失效",
    ),
    (
        "I2",
        F_MODEL,
        _ANCHOR_TITLE,
        f"        title = command_id  {SENTINEL}",
        {T_TITLE, T_PATHS, T_SEARCH},
        "build_item 标题回裸 id ⇒ 画布没接线",
    ),
    (
        "I3",
        F_MODEL,
        _ANCHOR_PAIRS,
        f'        pairs.append(f"{{key}}={{text}}")  {SENTINEL}',
        {T_SUMKEYS, T_CMDLVL, T_PATHS, T_RWF, T_LIMIT, T_PRIO, T_SEL1, T_LIM1, T_APPLY},
        "摘要键名回裸键 ⇒ 摘要中文化失效",
    ),
    (
        "I4",
        F_MODEL,
        _ANCHOR_PAIRS,
        f'        pairs.append(f"{{field_label(key)}}={{text}}")  {SENTINEL}',
        {T_CMDLVL},
        "摘要丢 command_id ⇒ 命令级覆盖失效",
    ),
    (
        "I5",
        F_APP,
        _ANCHOR_APPLY,
        f"                summarize_args(values),  {SENTINEL}",
        {T_APPGATE},
        "参数应用路径丢 command_id（AST 闸）",
    ),
    (
        "I6",
        F_APP,
        _ANCHOR_ELEM,
        f"            summarize_args(holder.args),  {SENTINEL}",
        {T_APPGATE},
        "元素引用路径丢 command_id（AST 闸）",
    ),
    (
        "I7",
        F_MODEL,
        _ANCHOR_ACTION,
        f"        return build_item(raw, label=lambda _cid: _cid)  {SENTINEL}",
        {T_PATHS},
        "新增路径走恒等 label ⇒ 单条产出路径漂移",
    ),
    (
        "I8",
        F_APP,
        _ANCHOR_SEARCH,
        f"            # 搜索文本摘掉 command id（负向注入）  {SENTINEL}",
        {T_SEARCH, T_OLDSRCH, T_CYCLE, T_RECOMP},
        "搜索文本丢 command id ⇒ 英文 id 搜不到",
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
            hit = next(
                (c for c in list(case) if c.tag in ("failure", "error")), None
            )
            if hit is not None:
                reds[case.get("name") or "?"] = hit.tag
            else:
                passed += 1
    return rc, reds, passed


def restore(originals: dict[str, bytes]) -> None:
    for rel, blob in originals.items():
        (ROOT / rel).write_bytes(blob)


def has_sentinel() -> list[str]:
    return [
        rel
        for rel in FILES
        if SENTINEL in (ROOT / rel).read_text(encoding="utf-8")
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
