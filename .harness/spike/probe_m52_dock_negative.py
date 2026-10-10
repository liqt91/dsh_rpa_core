"""M52 补丁负向验证探针：底部 Dock 必须**全部叠成同一片页签**（标准流程：对照绿 → 注入精确红 → 逐字节还原核 md5）。

被测改动（维护者真机反馈「元素库默认去下面了，但不能做成和日志的 tab 切换吗？两个并列了」）：

- ``gui/app.py::_stack_bottom_panels``：旧实现只在「元素库 + 运行**都**建好」时
  ``tabifyDockWidget(元素库, 运行)``，**漏了**运行历史 / 数据表格——它们一旦被显示就与
  元素库**左右并列**（Qt 的 ``addDockWidget`` 对同区 Dock 默认是**并排分栏**、不是页签）。
  维护者截图正是「没跑过流程（运行面板没建）⇒ 元素库 + 运行历史并列」。
  修法：锚固定为**元素库**，四个底部 Dock 谁建好就并入锚一次（``_bottom_tabbed`` 去重）。
- ``_toggle_history_dock`` / ``_toggle_table_dock``：补 ``raise_()``——同片后 ``show()``
  只让「这一片」可见、当前页签可能还停在元素库那页，用户会以为没打开。
- 判据 +3（``tests/contract/test_gui_view_menu.py``）：元素库+运行历史同片（截图场景）/
  四个底部面板同一条页签 / 工具栏入口点完当前页切过去。

四处注入，覆盖「遍历范围缩水 / 叠放方向反了 / 锚开闭反了 / 忘了顶页」：

  I1 app：``for key in ("run", "history", "table")`` 缩回 ``("run",)`` ⇒ 运行历史/数据表格
     永不并入 ⇒ 两条新判据红（截图场景 + 四片合一）。
  I2 app：``tabifyDockWidget(anchor, dock)`` 参数反转 ⇒ 元素库不再排最前 ⇒ 四条顺序判据红。
  I3 app：``if anchor is None: return`` 反成 ``if anchor is not None: return`` ⇒ 永 return、
     一次也不并入 ⇒ 四条同片/顺序判据红。
     —— I1/I3 是「并入范围」的两个方向（少并 / 全不并），I2 是「叠放方向」，
     三者一起证明判据读的是**真实页签布局**、不是摆设。
  I4 app：删 ``_toggle_history_dock`` 里的 ``raise_()`` ⇒ 当前页仍停在元素库 ⇒ 顶页判据红。

★ 哨兵 ``#``（Python 文件）。M48 教训：哨兵按**文件后缀**取，给 JS 写 ``#`` 会让语法坏掉、
门禁一行 FAIL 都不打（假绿）。
★ 输出**重定向到文件再读**，绝不接管道（M48：BrokenPipe 打断 restore_all 会把注入留在工作区）。
★ pytest 计数从 **junitxml** 取且 **不 unlink()**（删除守卫按 turn 累计、>50 即 fail-closed，
   探针脚本里任何 unlink 先问「这个 turn 里还会删多少次」）；用 mtime 判本轮是否真产出报告。

复用 ``probe_m52_nth_gating_negative.py`` 的纪律：起手 refresh_backups / 无哨兵自检 /
try-finally restore_all / 退出阶段环境级崩溃有界重跑。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
BAK = pathlib.Path(tempfile.gettempdir()) / "m52_dock_neg_backup"

FILES = {
    "app": "src/rpa_core/gui/app.py",
}

PY_SUITE = "tests/contract/test_gui_view_menu.py"

_SENTINEL = "# [M52-DOCK-NEGATIVE]"

FLAKES: list[str] = []


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run_py_once() -> tuple[int, str, int, int, list[str]]:
    """跑 pytest，计数从 **junitxml** 取（**不 unlink()**：删除守卫按 turn 累计）。"""
    xml = ROOT / ".harness" / "spike" / "_m52_dock_last.xml"
    before = xml.stat().st_mtime_ns if xml.exists() else None
    proc = subprocess.run(
        [str(PY), "-m", "pytest", PY_SUITE, "--no-header", "--tb=no", f"--junitxml={xml}"],
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
    four = {
        "test_elements_and_history_share_bottom_tab_strip",
        "test_all_bottom_docks_share_one_tab_strip",
        "test_elements_and_run_share_one_bottom_tab_strip",
        "test_elements_dock_tabs_with_run_when_elements_built_first",
    }
    two = {
        "test_elements_and_history_share_bottom_tab_strip",
        "test_all_bottom_docks_share_one_tab_strip",
    }
    # 顶页判据的前提就是「同片」：同片被破坏时它理应一起红（连带而非越界）
    toggle = {"test_toggle_buttons_raise_their_tab"}

    # 末元素 = 期望失败用例名集合（断言「实际红 ⊆ 期望红」且非空）
    injections = [
        (
            "I1 app：遍历范围缩回 (\"run\",) ⇒ 运行历史/数据表格永不并入",
            app,
            '        for key in ("run", "history", "table"):',
            '        for key in ("run",):',
            two | toggle,
        ),
        (
            "I2 app：tabifyDockWidget 参数反转 ⇒ 元素库不再排最前",
            app,
            "            self.tabifyDockWidget(anchor, dock)",
            "            self.tabifyDockWidget(dock, anchor)",
            four,
        ),
        (
            "I3 app：锚开闭判据反转 ⇒ 永 return、一次也不并入",
            app,
            "        if anchor is None:\n            return  # 锚还没建；元素库建好时会自己再调一次",
            "        if anchor is not None:\n            return  # 注入：反了，永不并入",
            four | toggle,
        ),
        (
            "I4 app：删 _toggle_history_dock 的 raise_ ⇒ 当前页不切过去",
            app,
            "        dock.show()\n"
            "        # 四个底部面板同片页签：只 show 会让「这一片」可见、当前页签可能还停在别页\n"
            "        dock.raise_()\n"
            "        self._refresh_history()",
            "        dock.show()\n"
            "        # 注入：去掉 raise_\n"
            "        self._refresh_history()",
            {"test_toggle_buttons_raise_their_tab"},
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
            elif not set(ids) <= expect:
                print(f"  ✗ 红在期望之外的判据：{sorted(set(ids) - expect)} —— {detail}")
                failures.append(label + "(未命中针对性判据)")
            else:
                print(f"  ✓ 精确红（全部落在针对性判据上）：{detail}")
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
