"""M51-B 负向验证探针（标准流程：对照绿 → 注入精确红 → 逐字节还原核 md5）。

被测改动（M51-B：浏览器候选**供给**扩容——M52 S3 实测候选恒空之后的补供给）：

- ``extension/content.js``：``candidatesFor`` 新增**祖先链收缩候选**（kind="path"），
  从 ``pathFor(el)`` 的祖先链派生「末 N 级 + 去 :nth-of-type 变体」，全部实测 matchedCount。
- ``src/rpa_core/model/selector_ranking.py``：``WEB_KIND_PENALTIES`` 登记 ``path``。
- ``scripts/check_capture_helpers.mjs``：桩 DOM 扩 class / 后代组合 / nth 形态 + 新断言。

七处注入，覆盖「候选来源 / 去 nth 变体 / 收缩级数 / kind 跨端登记 / build 三方一致 /
门禁自身的桩 DOM」：

  I1 content：祖先链候选来源被掐（MAX_PATH_SUFFIX=0）→ JS 门禁的祖先链断言红。
  I2 content：去掉「去 :nth-of-type 变体」→ 断言少一条而红。
  I3 content：候选 kind 改名成未登记值 → **宿主**成对判据（content.js 的 kind ⊆ 表）红。
  I4 content：只保留末 1 级（丢掉中间受缩层）→ 祖先链断言红。
  I5 ranking：``path`` 未登记 → kind 登记判据 + 跨端成对判据双双红。
  I6 content：EXT_BUILD 回退一版（**只改一个文件**）→ 三方一致判据红。
  I7 helpers：桩 DOM 的 class 分支恒 false → 祖先链断言红。
      —— 这条是「**验证验证者**」：证明门禁确实在求值，不是恒真。

★ 哨兵**按文件后缀取**（JS ``//`` / Python ``#``）：M48 的教训是给 JS 注入写了 ``#``，
``#`` 在 JS 里是语法错 ⇒ node 切片在 ``new Function`` 处抛异常后 ``process.exit(1)``，
**且不打印任何 ``FAIL |`` 行** ⇒ 只数汇总行就会把它当成"红得恰到好处"（假绿）。

★ node 门禁的失败判据是 **returncode != 0 + 有 FAIL 行**，不是数汇总行（同上教训）。

锚点是**实现级**的（与 S1/S2/S3 探针分文件、分哨兵），但遵循同一套
「refresh_backups / restore_all / 无哨兵自检 / 不接管道」纪律（M40/M48/M50 教训）。
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
NODE = shutil.which("node")
BAK = pathlib.Path(tempfile.gettempdir()) / "m51b_neg_backup"

FILES = {
    "content": "extension/content.js",
    "ranking": "src/rpa_core/model/selector_ranking.py",
    "helpers": "scripts/check_capture_helpers.mjs",
}

JS_SUITE = "scripts/check_capture_helpers.mjs"

PY_SUITES = {
    "ranking": ["tests/contract/test_web_candidate_ranking.py"],
    "contract": ["tests/contract/test_capture_extension.py"],
}

ALL_PY = PY_SUITES["ranking"] + PY_SUITES["contract"]

_SENTINEL_JS = "// [M51B-NEGATIVE-INJECTED]"
_SENTINEL_PY = "# [M51B-NEGATIVE-INJECTED]"


def sentinel_for(key: str) -> str:
    """哨兵按文件后缀取（见模块 docstring 里 M48 的教训）。"""
    suffix = pathlib.Path(FILES[key]).suffix
    return _SENTINEL_JS if suffix in (".js", ".mjs") else _SENTINEL_PY


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def current_build() -> str:
    """从 content.js 解析当前 EXT_BUILD（I6 用它 ⇒ 以后 bump 版本无需改探针锚点）。

    M52 补丁把 build 从 0.9.0 → 0.9.1 后，I6 里硬编码的 `0.9.0` 锚点就失效了（注入会报
    「锚点出现 0 次」）。改成运行期解析即一劳永逸。
    """
    match = re.search(r'const EXT_BUILD = "([^"]+)"', (ROOT / FILES["content"]).read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("无法从 content.js 解析 EXT_BUILD")
    return match.group(1)


FLAKES: list[str] = []


def _run_py_once(paths: list[str]) -> tuple[int, str, int, int, list[str]]:
    """跑 pytest，计数从 **junitxml** 取（**不 unlink()**：删除守卫按 turn 累计、>50 即 fail-closed）。"""
    xml = ROOT / ".harness" / "spike" / "_m51b_last.xml"
    before = xml.stat().st_mtime_ns if xml.exists() else None
    proc = subprocess.run(
        [
            str(PY), "-m", "pytest", *paths, "-q", "--no-header", "--tb=no",
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


def run_py(paths: list[str]) -> tuple[int, str, int, int, list[str]]:
    """``_run_py_once`` + 「退出阶段环境级崩溃」的有界重跑（本机已知现象，同 M52 S3 探针）。"""
    rc, out, passed, failed, ids = _run_py_once(paths)
    if rc != 0 and failed == 0 and passed > 0:
        rc2, out2, passed2, failed2, ids2 = _run_py_once(paths)
        if rc2 == 0 and failed2 == 0 and passed2 == passed:
            FLAKES.append(f"rc={rc} 重跑 rc=0（{passed} passed）")
            print(f"  ⚠ 环境级 flaky：退出阶段崩溃 rc={rc} → 重跑 rc=0（{passed} passed）")
            return rc2, out2, passed2, failed2, ids2
        print(f"  ⚠ 退出阶段崩溃后重跑仍不绿：rc={rc} → {rc2}，按原结果上报")
    return rc, out, passed, failed, ids


def run_js(script: str) -> tuple[int, str, list[str]]:
    """node 门禁：失败判据是 **returncode != 0**，并抓 ``FAIL |`` 行供「红在哪条」审。"""
    proc = subprocess.run([NODE, script], cwd=ROOT, capture_output=True, text=True, timeout=300)
    out = proc.stdout + proc.stderr
    labels = [
        line[len("FAIL | "):].split(" → ")[0].strip()
        for line in out.splitlines()
        if line.startswith("FAIL | ")
    ]
    return proc.returncode, out, labels


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
        if _SENTINEL_JS.encode() in data or _SENTINEL_PY.encode() in data:
            raise SystemExit(f"备份里含哨兵，拒绝还原：{src}")
        dst.write_bytes(data)


def assert_no_sentinel() -> None:
    dirty = [
        rel
        for rel in FILES.values()
        if _SENTINEL_JS.encode() in (ROOT / rel).read_bytes()
        or _SENTINEL_PY.encode() in (ROOT / rel).read_bytes()
    ]
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
    path.write_bytes(text.replace(old, new + "  " + sentinel_for(key)).encode("utf-8"))


def main() -> int:
    if NODE is None:
        raise SystemExit("找不到 node（门禁依赖它，先装 node 再跑探针）")
    refresh_backups()
    originals = {key: md5(ROOT / rel) for key, rel in FILES.items()}
    build = current_build()

    print("=== 对照跑（注入前必须全绿）===")
    rc, out, passed, failed, _ids = run_py(ALL_PY)
    if rc != 0 or failed != 0 or passed == 0:
        print(out[-3000:])
        print(f"对照不绿（pytest）：rc={rc} passed={passed} failed={failed}")
        return 1
    print(f"对照绿（pytest）：{passed} passed / {failed} failed / rc={rc}")
    jrc, jout, _jl = run_js(JS_SUITE)
    if jrc != 0:
        print(jout[-2000:])
        print(f"对照不绿（node 切片 {JS_SUITE}）：rc={jrc}")
        return 1
    print(f"对照绿（node 切片 {JS_SUITE}）：rc=0")

    content = "content"
    ranking = "ranking"
    helpers = "helpers"

    # 末元素 = 期望失败集：py 为**用例名集合**（断言「实际红 ⊆ 期望红」且非空）；
    # js 为**判据 label 的子串集合**（断言每条 FAIL 都落在期望内）。
    injections = [
        (
            "I1 content：祖先链候选来源被掐（MAX_PATH_SUFFIX=0）",
            "js",
            content,
            "  const MAX_PATH_SUFFIX = 3;",
            "  const MAX_PATH_SUFFIX = 0;",
            {"祖先链收缩候选", "只有 class 的元素"},
        ),
        (
            "I2 content：去掉「去 :nth-of-type 变体」",
            "js",
            content,
            "      if (stripped !== tail) out.push(stripped);",
            "      if (false) out.push(stripped);",
            {"祖先链收缩候选"},
        ),
        (
            "I3 content：候选 kind 改名成未登记值（跨端一致判据）",
            "ranking",
            content,
            '    for (const selector of pathSuffixSelectorsFor(el)) push("path", selector);',
            '    for (const selector of pathSuffixSelectorsFor(el)) push("cssx", selector);',
            {"test_capture_script_kinds_are_all_registered_in_table"},
        ),
        (
            "I4 content：只保留末 1 级（丢掉中间受缩层）",
            "js",
            content,
            "    const top = Math.min(fragments.length - 1, MAX_PATH_SUFFIX);",
            "    const top = Math.min(fragments.length - 1, 1);",
            {"祖先链收缩候选"},
        ),
        (
            "I5 ranking：path 未登记（落兜底值）",
            "ranking",
            ranking,
            '    "path": 20,          # 祖先链的收缩写法（tag.class / 后代组合）：比属性弱、比裸 tag 强',
            "    # path 登记被移除（注入）",
            {
                "test_path_kind_is_registered_below_unknown",
                "test_capture_script_kinds_are_all_registered_in_table",
            },
        ),
        (
            "I6 content：EXT_BUILD 回退一版（只改一个文件 ⇒ 三方不一致）",
            "contract",
            content,
            f'  const EXT_BUILD = "{build}";',
            '  const EXT_BUILD = "0.0.0-stale";',
            {"test_ext_build_marker_matches_manifest_version"},
        ),
        (
            "I7 helpers：桩 DOM 的 class 分支恒 false（验证门禁自身在求值）",
            "js",
            helpers,
            "    return true;\n  }\n  return false;\n};",
            "    return false;\n  }\n  return false;\n};",
            {"祖先链收缩候选", "只有 class 的元素"},
        ),
    ]

    failures: list[str] = []
    try:
        for label, runner, key, old, new, expect in injections:
            print(f"\n=== {label} ===")
            try:
                inject(key, old, new)
            except SystemExit as exc:
                print("注入失败：", exc)
                failures.append(label)
                continue
            if runner == "js":
                code, out, labels = run_js(JS_SUITE)
                print(f"  红在：{labels}")
                if code == 0:
                    print(f"  ✗ 假绿灯：注入后仍绿（rc={code}）")
                    failures.append(label)
                elif not labels:
                    print("  ✗ 非断言失败（无 FAIL 行：疑似语法错/异常，不是精确红）")
                    failures.append(label)
                else:
                    extra = [item for item in labels if not any(sub in item for sub in expect)]
                    if extra:
                        print(f"  ✗ 红在期望之外的判据：{extra}")
                        failures.append(label + "(未命中针对性判据)")
                    else:
                        print("  ✓ 精确红（全部落在针对性判据上）")
            else:
                code, out, passed, failed, ids = run_py(PY_SUITES[runner])
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
    code, out, passed, failed, _ids = run_py(ALL_PY)
    print(f"  pytest：{passed} passed / {failed} failed / rc={code}")
    if code != 0 or failed != 0 or passed == 0:
        failures.append("还原后复绿失败（pytest）")
    jrc, _jout, _jl = run_js(JS_SUITE)
    print(f"  node 切片：rc={jrc}")
    if jrc != 0:
        failures.append("还原后复绿失败（node 切片）")

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
