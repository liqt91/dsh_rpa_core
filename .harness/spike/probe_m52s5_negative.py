"""M52 S5 负向验证探针：元素引用落盘 + 失败归属 + 缺参提示。

标准流程：对照绿 → 注入精确红 → 逐字节还原核 md5。

本轮修的三处（真机反馈「test 流程选了元素、没填 selector，运行报
`INVALID_INPUT · 节点 None  'selector' is a required property`」）：

- `gui/app.py::_action_form_dirty`：把**元素引用意图**也算作「未应用的编辑」。
  原先只比 值 / fx 模式 / 输出别名 / 超时重试，漏了引用 ⇒ 下拉选了元素但值控件
  看不出差异时被判「无改动」，保存/运行的 `_commit_pending_edits` 整段跳过，
  引用被**静默丢掉**（落盘节点既无 selector 也无 elementRefs）。
- `gui/app.py::_error_node_id`：错误的节点归属在 `error["details"]["nodeId"]`，
  **顶层没有** `nodeId`；两处渲染原先都直读顶层 ⇒ 界面永远显示「节点 None」。
- `gui/app.py::missing_element_param_hint`：缺**元素型**必填参数时补一句可操作提示。

四处注入（都在 app.py）：

  I1 `_action_form_dirty` 去掉元素引用那一段 ⇒ 判据 T_DIRTY 红（引用被静默丢）。
  I2 `_error_node_id` 退回只读顶层 ⇒ 判据 T_NODEID 红（「节点 None」复现）。
  I3 `missing_element_param_hint` 恒返回 None ⇒ 判据 T_HINT 红（提示没了）。
  I4 该函数的「键必须能由元素库提供」闸门换成硬编码 `["url", ...]` ⇒ 判据 T_HINT 红
     （`url` 被误当作元素型键 —— 证明闸门真在读事实源）。

★ 哨兵 `#`（Python 文件）；按文件后缀取（M48 教训：给 JS 写 `#` 会让语法坏掉）。
★ 输出**重定向到文件再读**，绝不接管道（M48：BrokenPipe 打断 restore_all 会把注入留在工作区）。
★ pytest 计数从 **junitxml** 取且 **不 unlink()**（删除守卫按 turn 累计、>50 即 fail-closed）。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
BAK = pathlib.Path(tempfile.gettempdir()) / "m52s5_neg_backup"

FILES = {
    "app": "src/rpa_core/gui/app.py",
}

PY_SUITES = [
    "tests/contract/test_gui_panels.py",
    "tests/contract/test_gui_run.py",
]

_SENTINEL = "# [M52S5-NEGATIVE]"

T_DIRTY = "test_element_ref_intent_alone_counts_as_pending_edit"
T_NODEID = "test_run_failure_attributes_node_from_details"
T_HINT = "test_missing_required_element_param_gets_actionable_hint"

FLAKES: list[str] = []


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run_py_once() -> tuple[int, str, int, int, list[str]]:
    xml = ROOT / ".harness" / "spike" / "_m52s5_last.xml"
    before = xml.stat().st_mtime_ns if xml.exists() else None
    proc = subprocess.run(
        [str(PY), "-m", "pytest", *PY_SUITES, "--no-header", "--tb=no", f"--junitxml={xml}"],
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


def inject(key: str, old: str, new: str) -> None:
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

    app = "app"

    injections = [
        (
            "I1 _action_form_dirty 不再把元素引用算作未应用编辑",
            app,
            # ★ old **不带尾随换行**：`inject()` 是 `replace(old, new + "  " + 哨兵)`，
            #   old 若含块尾 `\n`，这个换行会被吃掉 ⇒ 哨兵与下一行 `rt = ...` 粘连成一行注释
            #   ⇒ `rt` 未定义、`_action_form_dirty` 一旦被调用即 NameError，多红一条无关用例
            #   （曾据此误判为「环境级 flaky」，实为注入锚点自伤——M48/M52S4 的老坑重犯）。
            (
                "        existing_refs = (raw or {}).get(\"elementRefs\") or {}\n"
                "        for key, ref_name in form.element_refs().items():\n"
                "            if ref_name != existing_refs.get(key):\n"
                "                return True"
            ),
            "        pass  # (I1) 只看值差异",
            {T_DIRTY},
        ),
        (
            "I2 _error_node_id 退回只读顶层 nodeId",
            app,
            (
                "        details = error.get(\"details\")\n"
                "        if isinstance(details, dict) and details.get(\"nodeId\"):\n"
                "            return str(details[\"nodeId\"])\n"
                "        fallback = error.get(\"nodeId\")\n"
                "        return str(fallback) if fallback else None"
            ),
            (
                "        fallback = error.get(\"nodeId\")\n"
                "        return str(fallback) if fallback else None"
            ),
            {T_NODEID},
        ),
        (
            "I3 missing_element_param_hint 恒返回 None",
            app,
            "    if error.get(\"code\") != \"INVALID_INPUT\":\n        return None",
            "    if True:\n        return None",
            {T_HINT},
        ),
        (
            "I4 提示函数丢掉「键须能由元素库提供」闸门（改硬编码 url 也算）",
            app,
            (
                "    for key in sorted(element_capable_keys()):\n"
                "        if message != f\"'{key}' is a required property\":\n"
                "            continue"
            ),
            (
                "    for key in [\"url\", \"selector\", \"locator\"]:\n"
                "        if message != f\"'{key}' is a required property\":\n"
                "            continue"
            ),
            {T_HINT},
        ),
    ]

    failures: list[str] = []
    try:
        for label, key, old, new, expect in injections:
            print(f"\n=== {label} ===")
            try:
                inject(key, old, new)
            except SystemExit as exc:
                print("注入失败：", exc)
                failures.append(label)
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
            elif set(ids) <= expect:
                print(f"  ✓ 精确红（全部落在针对性判据上）：{detail}")
            else:
                # 失败集不是期望子集：可能是本机**环境级 flaky**（焦点竞争 / 退出阶段崩溃）掺进来，
                # **但更可能是注入锚点自伤**（例如尾随换行让哨兵吃掉下一行 ⇒ 多红一条无关用例，
                # 本轮 I1 就撞过并被误判为 flaky）。**有界重跑（最多 2 次）**判定，逐次显式记档，
                # 绝不静默放行；若 2 次都稳定复现，就按「注入打歪」处理、回去修锚点。
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
            restored = md5(ROOT / FILES[key])
            if restored != originals[key]:
                print(f"  ✗ 还原后 md5 不一致：{restored} != {originals[key]}")
                failures.append(label + "(未还原)")
            else:
                print(f"  ✓ 逐字节还原 md5 一致：{restored[:12]}…")
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
