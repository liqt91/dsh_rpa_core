"""M52 S6 负向验证探针：元素引用去快照（ref-only）。

标准流程：对照绿 → 注入精确红 → 逐字节还原核 md5 → 还原后复绿。

本轮（维护者 2026-10-12 拍板「硬切 / fail-closed / 只显元素名 / 现在做」）
把 ADR-001 的「引用模型」收口成**唯一真值源**：`with` 里不再有值快照。

九处注入，覆盖六条线：

  I1  model/workflow.py：`_reject_snapshot_alongside_element_ref` 变成空判 ⇒ T_REJECT 红
      （旧双写数据又会被静默接受）。
  I2  runtime/element_refs.py：缺元素时不再记 `missing` ⇒ T_RESOLVE_MISSING + T_ORCH_MISSING 红
      （回到「解析不到当没引用」的旧语义）。
  I3  runtime/orchestrator.py：错误码换成 INVALID_INPUT ⇒ T_ORCH_MISSING 红
      （硬失败还在，但不是那个**可被提示认领**的码）。
  I4  gui/app.py::_insert_element：把值快照写回 `with` ⇒ 4 条红（两条写入路径不再逐字节
      相等；且 ✕ 摘引用后**快照会顶上来**，节点从「缺必填等用户填」变成「带着旧值跑」）。
  I5  gui/canvas.py::_has_config_error：不看 `elementRefs` ⇒ T_CANVAS_REF 红（ref-only 恒红）。
  I6  gui/app.py::collect_validation_issues：不看 `elementRefs` ⇒ T_VALIDATE_REF 红。
  I7  gui/param_form.py::_apply_element_state：值行不再隐藏 ⇒ T_PANEL_SHOW 红。
  I8  **两处一起删**（`values()` 不跳过被引用的键 + `apply()` 不再 `pop`）⇒ T_PANEL_MATCH 红。
      ★ 单删任一处**没有任何症状**——这两层互为兜底（`values()` 保证 `with` 重建时不带该键、
      `apply()` 的显式 `pop` 又兜一道）。所以「只删一层」的注入**永远算假绿灯**，
      必须一次删掉整对，才是在验证「被引用的参数不许出现在 `with`」这个不变量。
  I9  gui/app.py::run_failure_hint：丢掉 `element_ref_missing_hint` ⇒ T_HINT_AUTH 红
      （提示接线只有一处，必须判「组合入口」而不是只判两个纯函数）。

★ 哨兵 `#`（全部是 Python 文件），按文件后缀取——M48 的教训：给 JS 写 `#` 会让
  语法坏掉、门禁**不打印任何 FAIL 行**，只数汇总行就会假绿。
★ 输出**重定向到文件再读**，绝不接管道——M48：BrokenPipe 打断 restore_all 会把注入
  留在工作区，之后所有改动都发生在「带着哨兵的工作区」上。
★ pytest 计数从 **junitxml** 取且**不 unlink()**（用 mtime 判断本轮是否真产出报告）——
  删除守卫按 **turn** 累计删除数 >50 即 fail-closed；M52 S4 就是被 `xml.unlink()` 打的。
★ `old` **整行取到、绝不带尾随换行**：`inject()` 是 `replace(old, new + "  " + 哨兵)`，
  `old` 若含块尾 `\n`，这个换行会被吃掉 ⇒ 哨兵与下一行粘连成一行注释（M52 S4/S5 连犯两次）。
★ 每处注入登记**期望失败集**，断言「实际红 ⊆ 期望红」——否则「把模块打倒」
  （收集/导入级失败）也会被记成命中。
★ 环境级判据（三条缺一不可）：rc≠0 / failed==0 / passed>0 ⇒ **有界重跑一次**，
  重跑 rc=0 且 passed 数一致才认绿，并**显式打印记档**（绝不静默放行）。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
BAK = pathlib.Path(tempfile.gettempdir()) / "m52s6_neg_backup"

FILES = {
    "wf": "src/rpa_core/model/workflow.py",
    "refs": "src/rpa_core/runtime/element_refs.py",
    "orch": "src/rpa_core/runtime/orchestrator.py",
    "app": "src/rpa_core/gui/app.py",
    "canvas": "src/rpa_core/gui/canvas.py",
    "pf": "src/rpa_core/gui/param_form.py",
}

PY_SUITES = [
    "tests/contract/test_element_refs.py",
    "tests/contract/test_gui_panels.py",
    "tests/contract/test_gui_validate.py",
    "tests/contract/test_gui_canvas.py",
    "tests/contract/test_gui_run.py",
]

_SENTINEL = "# [M52S6-NEGATIVE]"

T_REJECT = "test_action_node_rejects_snapshot_alongside_element_ref"
T_RESOLVE_MISSING = "test_resolve_element_refs_reports_missing_without_fallback"
T_ORCH_MISSING = "test_orchestrator_hard_fails_when_element_missing"
T_INSERT_INTO_SELECTOR = "test_insert_element_into_selector"
T_INSERT_REF_ONLY = "test_insert_element_writes_ref_only"
T_CLEAR_PLAIN = "test_param_form_clear_element_ref_returns_to_plain"
T_PANEL_MATCH = "test_param_form_element_pick_matches_insert_element_output"
T_PANEL_SHOW = "test_param_form_referenced_param_shows_only_element_name"
T_CANVAS_REF = "test_gutter_error_badge_cleared_by_element_ref"
T_VALIDATE_REF = "test_element_ref_satisfies_required_param"
T_HINT_AUTH = "test_run_failure_hint_is_one_authority_for_both_kinds"

FLAKES: list[str] = []

WF = "wf"
REFS = "refs"
ORCH = "orch"
APP = "app"
CANVAS = "canvas"
PF = "pf"


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run_py_once() -> tuple[int, str, int, int, list[str]]:
    xml = ROOT / ".harness" / "spike" / "_m52s6_last.xml"
    before = xml.stat().st_mtime_ns if xml.exists() else None
    proc = subprocess.run(
        [
            str(PY), "-m", "pytest", *PY_SUITES,
            "-p", "no:cacheprovider",
            "--no-header", "--tb=no", f"--junitxml={xml}",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=1200,
    )
    out = proc.stdout + proc.stderr
    passed = failed = 0
    failed_ids: list[str] = []
    fresh = xml.exists() and (before is None or xml.stat().st_mtime_ns != before)
    if fresh:
        import xml.etree.ElementTree as ET

        root = ET.parse(xml).getroot()
        for suite in root.iter("testsuite"):
            passed += int(suite.get("tests", 0))
            failed += int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        for case in root.iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                failed_ids.append(case.get("name") or "?")
        skipped = sum(int(s.get("skipped", 0)) for s in root.iter("testsuite"))
        passed = passed - failed - skipped
    return proc.returncode, out, passed, failed, failed_ids


def run_py() -> tuple[int, str, int, int, list[str]]:
    """``_run_py_once`` + 「退出阶段环境级崩溃」的有界重跑（本机已知现象）。"""
    rc, out, passed, failed, ids = _run_py_once()
    if rc != 0 and failed == 0 and passed > 0:
        rc2, out2, passed2, failed2, ids2 = _run_py_once()
        if rc2 == 0 and failed2 == 0 and passed2 == passed:
            FLAKES.append(f"rc={rc} 重跑 rc=0（{passed} passed）")
            print(f"  ⚠ 环境级 flaky：退出阶段崩溃 rc={rc} → 重跑 rc=0（{passed} passed）")
            return rc2, out2, passed2, failed2, ids2
        print(f"  ⚠ 退出阶段崩溃后重跑仍不绿：rc={rc} → {rc2}，按原结果上报")
    return rc, out, passed, failed, ids


def refresh_backups() -> None:
    """起手刷备份：备份目录是**长生命周期状态**，新增受控文件后不刷新会在第一条注入
    之后才 `FileNotFoundError`，把源码树留成注入态（M48 I15 的教训）。"""
    assert_no_sentinel()
    BAK.mkdir(parents=True, exist_ok=True)
    for key, rel in FILES.items():
        (BAK / f"{key}.bak").write_bytes((ROOT / rel).read_bytes())


def restore_all() -> None:
    for key, rel in FILES.items():
        src = BAK / f"{key}.bak"
        dst = ROOT / rel
        if not src.exists():
            raise SystemExit(f"备份缺失，拒绝还原（先跑 refresh_backups）：{src}")
        data = src.read_bytes()
        if _SENTINEL.encode() in data:
            raise SystemExit(f"备份里含哨兵，拒绝还原：{src}")
        dst.write_bytes(data)


def assert_no_sentinel() -> None:
    dirty = [rel for rel in FILES.values() if _SENTINEL.encode() in (ROOT / rel).read_bytes()]
    if dirty:
        raise SystemExit(
            "工作区含未还原的注入哨兵，拒绝运行（先手动还原再跑）：\n  " + "\n  ".join(dirty)
        )


def inject_one(key: str, old: str, new: str) -> None:
    path = ROOT / FILES[key]
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"锚点出现 {count} 次（要求 1）：{FILES[key]}\n{old!r}")
    path.write_bytes(text.replace(old, new + "  " + _SENTINEL).encode("utf-8"))


def main() -> int:
    refresh_backups()
    originals = {key: md5(ROOT / rel) for key, rel in FILES.items()}

    print("=== 对照跑（注入前必须全绿）===")
    rc, out, passed, failed, _ids = run_py()
    if rc != 0 or failed != 0 or passed == 0:
        print(out[-3000:])
        print(f"对照不绿：rc={rc} passed={passed} failed={failed}")
        return 1
    print(f"对照绿：{passed} passed / {failed} failed / rc={rc}")

    # 每处注入 = (标签, 期望失败集, [(文件键, old, new), ...])
    # 允许多点编辑：I8 的两层**互为兜底**，必须一次删掉整对。
    injections: list[tuple[str, set[str], list[tuple[str, str, str]]]] = [
        (
            "I1 ActionNode 不再拒绝「快照 + 引用」共存（硬切失效）",
            {T_REJECT},
            [
                (
                    WF,
                    "        refs = self.element_refs or {}\n"
                    "        clashes = sorted(key for key in refs if key in self.with_)\n"
                    "        if clashes:",
                    "        refs = self.element_refs or {}\n"
                    "        clashes: list[str] = []\n"
                    "        if clashes:",
                )
            ],
        ),
        (
            "I2 缺元素不再记 missing（回到「解析不到当没引用」）",
            {T_RESOLVE_MISSING, T_ORCH_MISSING},
            [
                (
                    REFS,
                    "        if value is None:\n"
                    "            resolution.missing.append((key, name))\n"
                    "            continue",
                    "        if value is None:\n            continue",
                )
            ],
        ),
        (
            "I3 硬失败还在，但错误码换成 INVALID_INPUT（提示认领不到）",
            {T_ORCH_MISSING},
            [
                (
                    ORCH,
                    "                    ErrorCode.ELEMENT_REF_MISSING,",
                    "                    ErrorCode.INVALID_INPUT,",
                )
            ],
        ),
        (
            "I4 _insert_element 把值快照写回 `with`（两路不再逐字节相等 + ✕ 后快照顶上来）",
            {T_INSERT_INTO_SELECTOR, T_INSERT_REF_ONLY, T_CLEAR_PLAIN, T_PANEL_MATCH},
            [
                (
                    APP,
                    "        holder.args.pop(key, None)\n"
                    "        if holder.raw is not None:\n"
                    "            holder.raw.setdefault(\"with\", {}).pop(key, None)\n"
                    "            holder.raw.setdefault(\"elementRefs\", {})[key] = name",
                    "        holder.args[key] = value\n"
                    "        if holder.raw is not None:\n"
                    "            holder.raw.setdefault(\"with\", {})[key] = value\n"
                    "            holder.raw.setdefault(\"elementRefs\", {})[key] = name",
                )
            ],
        ),
        (
            "I5 画布红点判定不看 elementRefs（ref-only 恒红）",
            {T_CANVAS_REF},
            [
                (
                    CANVAS,
                    "        return any(\n"
                    "            key not in refs and args.get(key) in (None, \"\", [], {}) "
                    "for key in required\n"
                    "        )",
                    "        return any(\n"
                    "            args.get(key) in (None, \"\", [], {}) for key in required\n"
                    "        )",
                )
            ],
        ),
        (
            "I6 编译期必填校验不看 elementRefs（ref-only 恒报缺参）",
            {T_VALIDATE_REF},
            [
                (
                    APP,
                    "                    if key not in with_args and key not in refs",
                    "                    if key not in with_args",
                )
            ],
        ),
        (
            "I7 引用生效时值行不隐藏（label 与行内控件照显）",
            {T_PANEL_SHOW},
            [
                (
                    PF,
                    "        label = self._row_labels.get(name)\n"
                    "        if label is not None:\n"
                    "            label.setVisible(not active)\n"
                    "        shown = self._row_shown.get(name)\n"
                    "        if shown is not None:\n"
                    "            shown.setVisible(not active)",
                    "        label = self._row_labels.get(name)\n"
                    "        if label is not None:\n"
                    "            label.setVisible(True)\n"
                    "        shown = self._row_shown.get(name)\n"
                    "        if shown is not None:\n"
                    "            shown.setVisible(True)",
                )
            ],
        ),
        (
            "I8 两处互为兜底一起删（values() 不跳过 + apply() 不 pop）",
            {T_PANEL_MATCH},
            [
                (
                    PF,
                    "            if self._current_ref(name) is not None:\n"
                    "                continue  # 引用生效 ⇒ 值由运行期从元素库取，节点不存快照",
                    "            if False:\n"
                    "                continue  # 引用生效 ⇒ 值由运行期从元素库取，节点不存快照",
                ),
                (
                    APP,
                    "                        refs[key] = ref_name\n"
                    "                        holder.raw[\"with\"].pop(key, None)",
                    "                        refs[key] = ref_name",
                ),
            ],
        ),
        (
            "I9 运行失败提示只留缺参那条（元素缺失提示断线）",
            {T_HINT_AUTH},
            [
                (
                    APP,
                    "    return missing_element_param_hint(error) or "
                    "element_ref_missing_hint(error)",
                    "    return missing_element_param_hint(error)",
                )
            ],
        ),
    ]

    failures: list[str] = []
    try:
        for label, expect, edits in injections:
            print(f"\n=== {label} ===")
            try:
                for key, old, new in edits:
                    inject_one(key, old, new)
            except SystemExit as exc:
                print("注入失败：", exc)
                failures.append(label)
                restore_all()
                continue
            code, out, passed, failed, ids = run_py()
            detail = f"passed={passed} failed={failed} rc={code}"
            print(f"  红在：{sorted(ids)}")
            if code == 0 and failed == 0:
                print(f"  ✗ 假绿灯：注入后仍绿 —— {detail}")
                failures.append(label)
            elif failed == 0:
                print(f"  ✗ 非断言失败（疑似 error/收集错）—— {detail}")
                failures.append(label)
            elif set(ids) <= expect and set(ids) & expect:
                print(f"  ✓ 精确红（全部落在针对性判据上）：{detail}")
            else:
                # 失败集不是期望子集：可能是本机**环境级 flaky**（焦点竞争 / 退出阶段崩溃，
                # 尤其 test_gui_run.py 会真起子进程）掺进来，**但更可能是注入打歪**。
                # **有界重跑（最多 2 次）**判定，逐次显式记档，绝不静默放行；
                # 2 次都稳定复现 ⇒ 按「注入打歪」处理、回去修锚点（M52 S5 的教训：
                # 稳定复现时先查代码，别先喊 flaky）。
                extra = sorted(set(ids) - expect)
                print(f"  首跑红在期望之外：{extra} —— 有界重跑（最多 2 次）确认是否环境级")
                stable = False
                for attempt in (2, 3):
                    _c, _o, passed_n, failed_n, ids_n = run_py()
                    if failed_n and set(ids_n) <= expect and set(ids_n) & expect:
                        FLAKES.append(f"{label}: 首跑多余 {extra}，第 {attempt} 次跑消失")
                        print(
                            f"  ⚠ 环境级 flaky 记档：第 {attempt} 次跑红在 {sorted(ids_n)}"
                            f"（{passed_n} passed）"
                        )
                        stable = True
                        break
                    extra = sorted(set(ids_n) - expect)
                    print(f"  第 {attempt} 次跑仍红在期望之外：{extra}")
                if not stable:
                    print(f"  ✗ 红在期望之外（重跑仍现）—— {detail}")
                    failures.append(label + "(未命中针对性判据)")
            restore_all()
            now = {key: md5(ROOT / rel) for key, rel in FILES.items()}
            bad = {k: (now[k], originals[k]) for k in FILES if now[k] != originals[k]}
            if bad:
                print(f"  ✗ 还原后 md5 不一致：{bad}")
                failures.append(label + "(未还原)")
            else:
                print(f"  ✓ 逐字节还原 md5 一致（{len(FILES)} 个受控文件）：{now['app'][:12]}…")
    finally:
        restore_all()

    print("\n=== 还原后复绿 ===")
    code, out, passed, failed, _ids = run_py()
    print(f"  pytest：{passed} passed / {failed} failed / rc={code}")
    if code != 0 or failed != 0 or passed == 0:
        failures.append("还原后复绿失败")

    print("\n=== 结果 ===")
    if FLAKES:
        print(f"环境级 flaky 记档（{len(FLAKES)} 次）：{FLAKES}")
    if failures:
        print("失败项：", failures)
        return 1
    print("全部注入命中、全部逐字节还原、还原后复绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
