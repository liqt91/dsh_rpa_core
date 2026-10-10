"""M52 S4 负向验证探针：参数面板「从元素库选择」（标准流程：对照绿 → 注入精确红 → 逐字节还原核 md5）。

被测改动（维护者问「输入文本的指令，selector 不是从元素库选择吗？」）：

- `runtime/element_refs.py`：新增 `_KEY_BY_KIND` 与 `param_key_for_element_kind` /
  `element_kind_for_param_key` / `element_capable_keys`——元素 kind ↔ 参数键的**唯一事实源**，
  且以 `_VALUE_BY_KEY`（取值路径表）为准；`gui/app.py::_insert_element` 改用它，不再 if/elif 硬编码。
- `gui/param_form.py`：给支持元素引用的参数（`selector` / `locator`）加一行「从元素库选择」
  （下拉按 kind 过滤 + ✕ 清除）；选中即把值填成**元素库最新值**并记引用意图，
  经 `element_refs()` 回传。
- `gui/app.py`：`_show_action_form` 注入元素来源；apply 时显式选择优先写/摘 `elementRefs`，
  未被显式动过的键仍按「值被手工改动即摘引用」的旧规则。

六处注入，覆盖「过滤失效 / 面板不记意图 / 应用端不消费 / 显式键被当接管 / 共享表不同源 / 来源没接上」：

  I1 param_form：下拉不再按 kind 过滤 ⇒ desktop 元素混进 selector 列表 ⇒ 判据 1 红。
  I2 param_form：`element_refs()` 恒返回 {} ⇒ 面板「选了也不记」⇒ 判据 2/3 红。
  I3 app：apply 里 `chosen` 恒空 ⇒ **应用端不消费**面板选择（写侧不记 / 读侧不用，两个方向）⇒ 判据 2/3 红。
  I4 app：stale 判据去掉 `key not in chosen` ⇒ 显式选的键也被当「用户接管」摘掉 ⇒ 判据 2 红。
  I5 runtime：`_KEY_BY_KIND["desktop"]` 指到 selector ⇒ 与 `_VALUE_BY_KEY` 不再互为反向 ⇒ 判据 6 红。
  I6 app：元素来源恒空 ⇒ 面板根本没有元素行 ⇒ 判据 1/2/3/4 红（覆盖「provider 没接上」）。

★ 哨兵 `#`（Python 文件）；**按文件后缀取**（M48 教训：给 JS 写 `#` 会让语法坏掉、门禁一行 FAIL 都不打）。
★ 输出**重定向到文件再读**，绝不接管道（M48：BrokenPipe 打断 restore_all 会把注入留在工作区）。
★ pytest 计数从 **junitxml** 取且 **不 unlink()**（删除守卫按 turn 累计、>50 即 fail-closed）；
   用 mtime 判本轮是否真产出报告。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
BAK = pathlib.Path(tempfile.gettempdir()) / "m52_epick_neg_backup"

FILES = {
    "app": "src/rpa_core/gui/app.py",
    "form": "src/rpa_core/gui/param_form.py",
    "refs": "src/rpa_core/runtime/element_refs.py",
}

PY_SUITES = [
    "tests/contract/test_element_refs.py",
    "tests/contract/test_gui_panels.py",
]

_SENTINEL = "# [M52-ELEMENT-PICK-NEGATIVE]"

# 用例名常量（junitxml 的 `name` 属性 = 函数名）
T_ROW = "test_param_form_element_row_lists_only_matching_kind"
T_PICK = "test_param_form_element_pick_matches_insert_element_output"
T_CLEAR = "test_param_form_clear_element_ref_keeps_hand_value"
T_KEEP = "test_param_form_apply_without_touching_element_keeps_ref"
T_MAP = "test_kind_key_mapping_is_sourced_from_value_table"

FLAKES: list[str] = []


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run_py_once() -> tuple[int, str, int, int, list[str]]:
    """跑 pytest，计数从 **junitxml** 取（**不 unlink()**：删除守卫按 turn 累计）。"""
    xml = ROOT / ".harness" / "spike" / "_m52_epick_last.xml"
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

    form = "form"
    app = "app"
    refs = "refs"

    injections = [
        (
            "I1 param_form：两道过滤都去掉 ⇒ desktop 元素混进 selector 列表",
            form,
            (
                '            if not isinstance(document, dict) or document.get("kind") != kind:\n'
                "                continue  # 只列与该参数后端匹配的元素"
                "（selector↔browser / locator↔desktop）\n"
                "            if element_value_for_key(document, name) is None:\n"
                "                continue  # 取不出定位值的元素，列了也填不进去"
            ),
            "            if not isinstance(document, dict):\n                continue",
            {T_ROW},
        ),
        (
            "I2 param_form：element_refs() 恒空 ⇒ 面板「选了也不记」",
            form,
            "        return dict(self._ref_intent)",
            "        return {}",
            {T_PICK, T_CLEAR},
        ),
        (
            "I3 app：apply 的 chosen 恒空 ⇒ 应用端不消费面板选择",
            app,
            "                chosen = form.element_refs()",
            "                chosen = {}",
            {T_PICK, T_CLEAR},
        ),
        (
            "I4 app：stale 去掉 `key not in chosen` ⇒ 显式选的键也被当接管摘掉",
            app,
            "                    if key not in chosen",
            "                    if True",
            {T_PICK},
        ),
        (
            "I5 runtime：_KEY_BY_KIND['desktop'] 指到 selector ⇒ 与取值表不再互为反向",
            refs,
            '    "desktop": "locator",',
            '    "desktop": "selector",',
            {T_MAP, "test_insert_element_kind_mismatch_hint"},  # 连带：插入路径的 kind 提示随表变化
        ),
        (
            "I6 app：元素来源恒空 ⇒ 面板没有元素行（provider 没接上）",
            app,
            "        store = self._element_store()\n        if store is None:\n            return []",
            "        return []\n        if True:\n            return []",
            {T_ROW, T_PICK, T_KEEP},
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
