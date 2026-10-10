"""M52 负向验证探针（标准流程：对照绿 → 注入精确红 → 逐字节还原核 md5）。

被测判据：``tests/contract/test_desktop_locator_ranking.py``（11 条）。

六处注入，覆盖「唯一硬门 / penalty 表 / path 计分 / 哈希 id 降权 / 稳定序 / 接线」六组：

  I1 模型层：choose_best_locator 的排序键**反过来**（count_of/penalty_of 对调）——
     唯一性不再是硬门（count=2 但更稳的会压过唯一的那条）→ 唯一硬门 + 退化 + 接线判据红。
  I2 模型层：locator_penalty 不再计 path 级数 → path 计分判据红。
  I3 共享层：looks_generated_id 恒 False（生成 id 不再降权）→ 哈希 id 降权判据红。
  I4 模型层：locator_penalty 硬编码 40（不读 penalty 表）→ 「表被读到」判据红。
  I5 模型层：候选先 reverse（拍乱输入序）→ 完全并列时不再取首个 → 稳定序判据红。
  I6 agent 层：_pick_best_candidate 绕开共享引擎（直接取首条）→ 接线两条判据红。

锚点是**实现级**的：S2 重构后 I1 改打 ``rank_candidates`` 调用形态、I3 落到
``model/selector_ranking.py``（哈希判定已迁至共享层）——重构**必须同步锚点**，
否则注入打空、探针静默失效（"假绿"）。

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
TEST = "tests/contract/test_desktop_locator_ranking.py"
BAK = pathlib.Path(tempfile.gettempdir()) / "m52_neg_backup"

SENTINEL = "# [M52-NEGATIVE-INJECTED]"

PATHS = {
    "model": "src/rpa_core/model/desktop.py",
    "ranking": "src/rpa_core/model/selector_ranking.py",
    "agent": "src/rpa_core/capture/desktop_agent.py",
}


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_tests() -> tuple[int, str, int, int]:
    """跑目标测试，返回 (rc, 输出, passed 数, failed 数)。

    计数从 **junitxml** 取（pytest 自己写的事实源）；**不 unlink()** 那个 xml——
    本机删除守卫按 turn 累计删除数、>50 即 fail-closed（见 probe_m48_negative 注释）。
    """
    xml = ROOT / ".harness" / "spike" / "_m52_last.xml"
    before = xml.stat().st_mtime_ns if xml.exists() else None
    proc = subprocess.run(
        [
            str(PY), "-m", "pytest", TEST, "-q", "--no-header", "--tb=no",
            f"--junitxml={xml}",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
    )
    out = proc.stdout + proc.stderr
    passed = failed = 0
    fresh = xml.exists() and (before is None or xml.stat().st_mtime_ns != before)
    if fresh:
        import xml.etree.ElementTree as ET

        root = ET.parse(xml).getroot()
        for suite in root.iter("testsuite"):
            passed += int(suite.get("tests", 0))
            failed += int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        skipped = sum(int(s.get("skipped", 0)) for s in root.iter("testsuite"))
        passed = passed - failed - skipped
    return proc.returncode, out, passed, failed


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
    rc, out, passed, failed = run_tests()
    if rc != 0 or failed != 0 or passed == 0:
        print(out[-3000:])
        print(f"对照不绿：rc={rc} passed={passed} failed={failed}")
        return 1
    print(f"对照绿：{passed} passed / {failed} failed / rc={rc}")

    injections = [
        (
            "I1 模型层：字典序反过来（唯一性不再是硬门）",
            "src/rpa_core/model/desktop.py",
            '    ranked = rank_candidates(\n'
            '        usable,\n'
            '        count_of=lambda entry: entry["count"],\n'
            '        penalty_of=lambda entry: locator_penalty(entry["locator"]),\n'
            "    )\n"
            "    return ranked[0]",
            '    ranked = rank_candidates(\n'
            '        usable,\n'
            '        count_of=lambda entry: locator_penalty(entry["locator"]),\n'
            '        penalty_of=lambda entry: entry["count"],\n'
            "    )\n"
            "    return ranked[0]",
        ),
        (
            "I2 模型层：locator_penalty 不再计 path 级数",
            "src/rpa_core/model/desktop.py",
            '    path = locator.get("path")\n'
            "    if isinstance(path, list):\n"
            "        total += PATH_STEP_PENALTY * len(path)\n"
            "    return total",
            "    return total",
        ),
        (
            "I3 共享层：looks_generated_id 恒 False（生成 id 不降权）",
            "src/rpa_core/model/selector_ranking.py",
            "    text = value.strip()\n"
            "    if not text:\n"
            "        return False",
            "    text = value.strip()\n"
            "    if text or not text:\n"
            "        return False",
        ),
        (
            "I4 模型层：locator_penalty 硬编码 40（不读 penalty 表）",
            "src/rpa_core/model/desktop.py",
            '        if locator.get(field) is not None:\n            total += penalty',
            '        if locator.get(field) is not None:\n            total += 40',
        ),
        (
            "I5 模型层：候选先 reverse（拍乱输入序，破坏稳定序）",
            "src/rpa_core/model/desktop.py",
            "    if not usable:\n        return None",
            "    if not usable:\n        return None\n    usable.reverse()",
        ),
        (
            "I6 agent 层：_pick_best_candidate 绕开共享引擎（直接取首条）",
            "src/rpa_core/capture/desktop_agent.py",
            "    chosen = choose_best_locator(entries)\n"
            "    if chosen is None:\n"
            "        return {}, 0\n"
            '    return chosen["locator"], chosen["count"]',
            "    if not entries:\n"
            "        return {}, 0\n"
            '    return entries[0]["locator"], entries[0]["count"]',
        ),
    ]

    failures = []
    try:
        for label, rel, old, new in injections:
            print(f"\n=== {label} ===")
            try:
                inject(rel, old, new)
            except SystemExit as exc:
                print("注入失败：", exc)
                failures.append(label)
                continue
            code, out, passed, failed = run_tests()
            detail = f"passed={passed} failed={failed} rc={code}"
            if code == 0 and failed == 0:
                print(f"  ✗ 假绿灯：注入后仍绿 —— {detail}")
                failures.append(label)
            elif failed == 0:
                print(f"  ✗ 非断言失败（疑似 error/收集错）—— {detail}")
                failures.append(label)
            else:
                print(f"  ✓ 精确红：{detail}")
            restore_all()
            key = next(k for k, v in PATHS.items() if v == rel)
            restored = md5(ROOT / rel)
            if restored != originals[key]:
                print(f"  ✗ 还原后 md5 不一致：{restored} != {originals[key]}")
                failures.append(label + "(未还原)")
            else:
                print(f"  ✓ 逐字节还原 md5 一致：{restored[:12]}…")
    finally:
        restore_all()

    print("\n=== 还原后复绿 ===")
    code, out, passed, failed = run_tests()
    print(f"  {passed} passed / {failed} failed / rc={code}")
    if code != 0 or failed != 0 or passed == 0:
        failures.append("还原后复绿失败")

    print("\n=== 结果 ===")
    if failures:
        print("失败项：", failures)
        return 1
    print("全部注入命中、全部逐字节还原、还原后复绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
