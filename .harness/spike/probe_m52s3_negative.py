"""M52 S3 负向验证探针（标准流程：对照绿 → 注入精确红 → 逐字节还原核 md5）。

被测判据（S3 新增/改动）：
- ``tests/contract/test_desktop_locator_ranking.py``（桌面候选落盘 + 打分 + 接线）
- ``tests/contract/test_capture_extension.py``（浏览器候选补分 + 接线）
- ``tests/contract/test_gui_panels.py``（GUI 只读展示候选与分数）

十二处注入，覆盖「打分 / 全量保留 / path 注入 / 落盘接线 / 补分接线 / 展示接线 /
**hover 快路径**（2026-10-10 修的那条真根因）」：

  I1  agent 层：_evaluate_candidates 不按表打分（penalty 恒 0）→ 打分判据红。
  I2  agent 层：_evaluate_candidates 只留第一条（丢掉其余候选）→ 全量保留判据红。
  I3  agent 层：path 不再注入候选（penalty 不计祖先链）→ path 注入判据红。
  I4  agent 层：capture_at 不落盘候选（selector 无 candidates）→ 点捕获落盘判据红。
  I5  extension 层：_score_candidates 不按引擎打分（penalty 恒 0）→ 补分判据红。
  I6  extension 层：_read_loop 不调 _score_candidates（绕开打分）→ 补分接线判据红。
  I7  panel 层：candidates_text 不再渲染分数 → GUI 分数判据红。
  I8  panel 层：确认框不渲染候选（label 恒空）→ GUI 展示接线判据红。
  I9  agent 层：**_describe_info 不落盘候选**（hover 路径无 candidates）
      → `test_describe_info_persists_scored_candidates` 红（本次事故的精确复现）。
  I10 agent 层：_describe_info 的实测接缝断开（`_root_verify` → None）
      → 同上判据红（候选为空表）。
  I11 agent 层：_locator_candidates 只留第一条（丢掉宽泛层）→ 全量保留判据红。
  I12 agent 层：**_describe_info 就地手搓候选**（另立第二套构造，不再与 capture_at 同源）
      → 「两端相等」判据 `test_candidates_are_shared_by_both_capture_paths` 红。

I12 是重点：它复现的正是**本次事故的形状**（两条路各自造候选）——只靠「两条路各自绿」
的判据抓不到，必须有「两端相等」的判据。

锚点是**实现级**的：与 S1/S2 探针（``probe_m52_negative.py`` / ``probe_m52s2_negative.py``）
分文件、分哨兵，但遵循同一套「refresh_backups / restore_all / 无哨兵自检 / 不接管道」
纪律（见 M40/M48/M50 教训）。

每处：先跑对照（原始文件必须绿）→ 注入 → 精确失败 → 还原 → md5 一致 → 复绿。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
BAK = pathlib.Path(tempfile.gettempdir()) / "m52s3_neg_backup"

SENTINEL = "# [M52S3-NEGATIVE-INJECTED]"

PATHS = {
    "agent": "src/rpa_core/capture/desktop_agent.py",
    "extension": "src/rpa_core/capture/extension.py",
    "panel": "src/rpa_core/gui/element_panel.py",
}

TESTS = {
    "agent": "tests/contract/test_desktop_locator_ranking.py",
    "extension": "tests/contract/test_capture_extension.py",
    "panel": "tests/contract/test_gui_panels.py",
}

ALL_TESTS = list(TESTS.values())


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


FLAKES: list[str] = []


def run_tests(test_paths: list[str]) -> tuple[int, str, int, int, list[str]]:
    """``_run_once`` + 「退出阶段环境级崩溃」的有界重跑（本机已知现象，见下）。"""
    rc, out, passed, failed, ids = _run_once(test_paths)
    # 环境级 flaky：pytest 子进程在**收集与断言全部完成之后**、于解释器退出阶段崩溃
    # （本机为 0xC0000005，同 memory §3 记录的 rc=139 现象）。判据三条缺一不可：
    # ① rc != 0；② failed == 0；③ passed > 0。此时**重跑一次同一命令**，只有重跑
    # rc=0 且 passed 数一致才认绿，并**显式记档**（绝不静默放行；重跑仍不绿则按原结果报）。
    if rc != 0 and failed == 0 and passed > 0:
        rc2, out2, passed2, failed2, ids2 = _run_once(test_paths)
        if rc2 == 0 and failed2 == 0 and passed2 == passed:
            FLAKES.append(f"rc={rc} 重跑 rc=0（{passed} passed）")
            print(f"  ⚠ 环境级 flaky：退出阶段崩溃 rc={rc} → 重跑 rc=0（{passed} passed）")
            return rc2, out2, passed2, failed2, ids2
        print(f"  ⚠ 退出阶段崩溃后重跑仍不绿：rc={rc} → {rc2}，按原结果上报")
    return rc, out, passed, failed, ids


def _run_once(test_paths: list[str]) -> tuple[int, str, int, int, list[str]]:
    """跑目标测试，返回 (rc, 输出, passed 数, failed 数, 失败用例名列表）。

    计数从 **junitxml** 取；**不 unlink()** 那个 xml——本机删除守卫按 turn 累计删除数、
    >50 即 fail-closed（见 probe_m48_negative 注释）；用 **mtime 变化**判断本轮是否真产出报告。

    失败**用例名**一并带出：只有「红了几条」不够——按纪律必须能审「红在**针对性判据**上」，
    否则「把模块打倒」（收集/导入级失败）也会被记成命中（M48 I21 教训）。
    """
    xml = ROOT / ".harness" / "spike" / "_m52s3_last.xml"
    before = xml.stat().st_mtime_ns if xml.exists() else None
    proc = subprocess.run(
        [
            str(PY), "-m", "pytest", *test_paths, "-q", "--no-header", "--tb=no",
            f"--junitxml={xml}",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
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


def refresh_backups() -> None:
    assert_no_sentinel()
    BAK.mkdir(parents=True, exist_ok=True)
    for key, rel in PATHS.items():
        (BAK / f"{key}.bak").write_bytes((ROOT / rel).read_bytes())


def restore_all() -> None:
    for key, rel in PATHS.items():
        src = BAK / f"{key}.bak"
        dst = ROOT / rel
        if not src.exists():
            raise SystemExit(f"备份缺失，拒绝还原（先跑 refresh_backups）：{src}")
        data = src.read_bytes()
        if SENTINEL.encode() in data:
            raise SystemExit(f"备份里含哨兵，拒绝还原：{src}")
        dst.write_bytes(data)


def assert_no_sentinel() -> None:
    dirty = [rel for rel in PATHS.values() if SENTINEL.encode() in (ROOT / rel).read_bytes()]
    if dirty:
        raise SystemExit(
            "工作区含未还原的注入哨兵，拒绝运行（先手动还原再跑）：\n  "
            + "\n  ".join(dirty)
        )


def inject(rel: str, old: str, new: str) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"锚点出现 {count} 次（要求 1）：{rel}\n{old!r}")
    path.write_bytes(text.replace(old, new + "  " + SENTINEL).encode("utf-8"))


def main() -> int:
    refresh_backups()
    originals = {key: md5(ROOT / rel) for key, rel in PATHS.items()}

    print("=== 对照跑（注入前必须全绿）===")
    rc, out, passed, failed, _ids = run_tests(ALL_TESTS)
    if rc != 0 or failed != 0 or passed == 0:
        print(out[-3000:])
        print(f"对照不绿：rc={rc} passed={passed} failed={failed}")
        return 1
    print(f"对照绿：{passed} passed / {failed} failed / rc={rc}")

    agent = PATHS["agent"]
    extension = PATHS["extension"]
    panel = PATHS["panel"]

    # 每项末元素 = **该注入针对的判据名**（集合，断言「实际红 ⊆ 期望红」且非空）：
    # 只数「红了几条」不够——「把模块打倒」（收集/导入级失败）也会有一条红，
    # 必须能审「红在**针对性**判据上」（M48 I21 教训）。
    injections = [
        (
            "I1 agent 层：_evaluate_candidates 不按表打分（penalty 恒 0）",
            "agent",
            agent,
            '                "penalty": locator_penalty(full),',
            '                "penalty": 0,',
            {
                # 这三条都断言「penalty 值 = 表算出来的值」，恒 0 必红。
                "test_evaluate_candidates_keeps_all_with_scores",
                "test_evaluate_candidates_injects_path_into_scores",
                "test_describe_info_persists_scored_candidates",
            },
        ),
        (
            "I2 agent 层：_evaluate_candidates 只留第一条（丢掉其余候选）",
            "agent",
            agent,
            "    return scored\n\n\ndef _choose_from_scored(scored: list[dict]) -> tuple[dict, int]:",
            "    return scored[:1]\n\n\ndef _choose_from_scored(scored: list[dict]) -> tuple[dict, int]:",
            {
                # 切片发生在 `_evaluate_candidates` 的**唯一出口**上，所以凡断言
                # 「候选条数/择优结果」的判据都会被带红——都是同一处改动的真实后果。
                "test_evaluate_candidates_keeps_all_with_scores",
                "test_capture_at_persists_scored_candidates",
                "test_describe_info_persists_scored_candidates",
                "test_candidates_are_shared_by_both_capture_paths",
                "test_pick_best_candidate_selects_unique_over_lower_penalty",
                "test_pick_best_candidate_falls_back_to_smallest_count",
            },
        ),
        (
            "I3 agent 层：path 不再注入候选（祖先链不计分）",
            "agent",
            agent,
            "        if path_steps:\n            full[\"path\"] = [dict(step) for step in path_steps]",
            "        if False:\n            full[\"path\"] = [dict(step) for step in path_steps]",
            {"test_evaluate_candidates_injects_path_into_scores"},
        ),
        (
            "I4 agent 层：capture_at 不落盘候选（selector 无 candidates）",
            "agent",
            agent,
            '    selector: dict = {"locator": best_locator}\n'
            "    if scored:\n"
            '        selector["candidates"] = scored',
            '    selector: dict = {"locator": best_locator}\n'
            "    if False:\n"
            '        selector["candidates"] = scored',
            {"test_capture_at_persists_scored_candidates"},
        ),
        (
            "I5 extension 层：_score_candidates 不按引擎打分（penalty 恒 0）",
            "extension",
            extension,
            '                    item["penalty"] = web_candidate_penalty(item)',
            '                    item["penalty"] = 0',
            {"test_extension_capture_scores_browser_candidates"},
        ),
        (
            "I6 extension 层：_read_loop 不调 _score_candidates（绕开打分）",
            "extension",
            extension,
            "                        self._score_candidates(descriptor)",
            "                        pass",
            {"test_extension_capture_scores_browser_candidates"},
        ),
        (
            "I7 panel 层：candidates_text 不再渲染分数",
            "panel",
            panel,
            '        score_text = (\n'
            '            f"分 {penalty}"\n'
            "            if isinstance(penalty, int) and not isinstance(penalty, bool)\n"
            '            else "分未算"\n'
            "        )",
            '        score_text = "分未算"',
            {
                "test_candidates_text_shows_scores_and_desktop_locators",
                "test_element_dialog_shows_candidates_readonly",
            },
        ),
        (
            "I8 panel 层：确认框不渲染候选（label 恒空）",
            "panel",
            panel,
            "        self.candidates_label = QLabel(candidates_text(descriptor))",
            '        self.candidates_label = QLabel("")',
            {"test_element_dialog_shows_candidates_readonly"},
        ),
        # ---- 2026-10-10 修的那条真根因：hover 快路径（_describe_info） -------------
        (
            "I9 agent 层：_describe_info 不落盘候选（hover 路径无 candidates）",
            "agent",
            agent,
            '    selector: dict = {"locator": locator}\n'
            "    if scored:\n"
            '        selector["candidates"] = scored',
            '    selector: dict = {"locator": locator}\n'
            "    if False:\n"
            '        selector["candidates"] = scored',
            {
                "test_describe_info_persists_scored_candidates",
                "test_candidates_are_shared_by_both_capture_paths",
            },
        ),
        (
            "I10 agent 层：_describe_info 实测接缝断开（_root_verify → None）",
            "agent",
            agent,
            "        _locator_candidates(control_type, automation_id, name),\n"
            "        _root_verify(root_hwnd),",
            "        _locator_candidates(control_type, automation_id, name),\n"
            "        None,",
            {
                "test_describe_info_persists_scored_candidates",
                "test_candidates_are_shared_by_both_capture_paths",
            },
        ),
        (
            "I11 agent 层：_locator_candidates 只留第一条（丢掉宽泛层）",
            "agent",
            agent,
            "    return out\n",
            "    return out[:1]\n",
            {
                "test_evaluate_candidates_keeps_all_with_scores",
                "test_capture_at_persists_scored_candidates",
                "test_describe_info_persists_scored_candidates",
            },
        ),
        (
            "I12 agent 层：_describe_info 就地手搓候选（第二套构造，不与 capture_at 同源）",
            "agent",
            agent,
            "        _locator_candidates(control_type, automation_id, name),\n",
            "        [("
            '{"control_type": control_type}, '
            '{"backend": "uia", "controlType": control_type}, '
            "None)],\n",
            {
                "test_describe_info_persists_scored_candidates",
                "test_candidates_are_shared_by_both_capture_paths",
            },
        ),
    ]

    failures = []
    try:
        for label, key, rel, old, new, expect in injections:
            print(f"\n=== {label} ===")
            try:
                inject(rel, old, new)
            except SystemExit as exc:
                print("注入失败：", exc)
                failures.append(label)
                continue
            code, out, passed, failed, ids = run_tests([TESTS[key]])
            detail = f"passed={passed} failed={failed} rc={code}"
            print(f"  红在：{sorted(ids)}")
            if code == 0 and failed == 0:
                print(f"  ✗ 假绿灯：注入后仍绿 —— {detail}")
                failures.append(label)
            elif failed == 0:
                print(f"  ✗ 非断言失败（疑似 error/收集错）—— {detail}")
                failures.append(label)
            elif not set(ids) <= expect:
                # 「精确红」必须红在**针对性判据**上：打到别的判据（或把模块打倒）不算命中
                print(f"  ✗ 红在期望之外的判据：{sorted(set(ids) - expect)} —— {detail}")
                failures.append(label + "(未命中针对性判据)")
            else:
                print(f"  ✓ 精确红（全部落在针对性判据上）：{detail}")
            restore_all()
            restored = md5(ROOT / rel)
            if restored != originals[key]:
                print(f"  ✗ 还原后 md5 不一致：{restored} != {originals[key]}")
                failures.append(label + "(未还原)")
            else:
                print(f"  ✓ 逐字节还原 md5 一致：{restored[:12]}…")
    finally:
        restore_all()

    print("\n=== 还原后复绿 ===")
    code, out, passed, failed, _ids = run_tests(ALL_TESTS)
    print(f"  {passed} passed / {failed} failed / rc={code}")
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
