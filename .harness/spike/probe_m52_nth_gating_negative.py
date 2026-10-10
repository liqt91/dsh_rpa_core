"""M52 补丁负向验证探针：``:nth-of-type`` 只在**必要**时才补（标准流程：对照绿 → 注入精确红 → 逐字节还原核 md5）。

被测改动（用户真机反馈「nth-of-type 的选择器感觉不太稳定」）：

- ``extension/content.js::pathEntryFor``：原先「父节点有 >1 个同标签兄弟」就补
  ``:nth-of-type(n)``，**没看本层片段（tag + 首类）是否已把目标唯一化**。于是
  ``div.search-area``（特征 class 唯一）也被硬塞序号，父节点多几个同标签兄弟就跟着变号，
  页面一重排 / 插入兄弟即失效。修法：仅当片段在同标签兄弟间**仍不唯一**时才补序号——
  同构列表（每个 ``li.hotsearch-item`` 一模一样）保号，特征 class 层级不再补。
- ``scripts/check_capture_helpers.mjs``：改被钉住的旧期望 + 新增「特征 class 唯一不补号」/
  「同构列表仍补号」/「首类不足时现状」成对断言。
- 三方 build ``0.9.0 → 0.9.1``。

四处注入，覆盖「gating 被撤 / 永远不补号（过度去号）/ 片段丢了 class / build 三方一致」：

  I1 content：撤掉 gating（``&& ambiguous`` 删掉）⇒ 回到「有同标签兄弟就补号」的旧行为，
     新断言「特征 class 已唯一不补号 / 祖先链 nthOfType / search-area」三条红。
  I2 content：``&& ambiguous`` 改成 ``&& false`` ⇒ **永远不补号**（过度去号），同构列表断言红。
      —— I1/I2 是 gating 的**两个方向**：撤掉判据 / 判据恒假，两边都要能被抓住。
  I3 content：``classListOf`` 恒返回 ``[]`` ⇒ 片段丢掉 class ⇒ 首类不再参与唯一化判断，
     整片路径断言红（证明片段里的 class 是**读侧**真读到的，不是摆设）。
  I4 content：EXT_BUILD 改成一个不存在的值（**只改 content.js**）⇒ 三方一致判据红。
      —— 该值**运行期从文件解析**（不硬编码），避免每次 bump 都要改探针锚点。

★ 哨兵**按文件后缀取**（JS ``//`` / Python ``#``）：M48 的教训是给 JS 注入写了 ``#``，
``#`` 在 JS 里是语法错 ⇒ node 切片在 ``new Function`` 处抛异常后 ``process.exit(1)``，
**且不打印任何 ``FAIL |`` 行** ⇒ 只数汇总行就会把它当成"红得恰到好处"（假绿）。

★ node 门禁的失败判据是 **returncode != 0 + 有 FAIL 行**，不是数汇总行（同上教训）。
★ 输出**重定向到文件再读**，绝不接管道（M48：BrokenPipe 会打断 restore_all，把注入留在工作区）。

复用 ``probe_m51b_negative.py`` 的纪律：起手 refresh_backups / 无哨兵自检 / try-finally restore_all /
pytest 计数取 junitxml 且 **不 unlink()**（删除守卫按 turn 累计、>50 即 fail-closed）。
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
BAK = pathlib.Path(tempfile.gettempdir()) / "m52_nth_neg_backup"

FILES = {
    "content": "extension/content.js",
    "helpers": "scripts/check_capture_helpers.mjs",
}

JS_SUITE = "scripts/check_capture_helpers.mjs"
PY_SUITES = {
    "contract": ["tests/contract/test_capture_extension.py"],
}

_SENTINEL_JS = "// [M52-NTH-NEGATIVE]"
_SENTINEL_PY = "# [M52-NTH-NEGATIVE]"


def sentinel_for(key: str) -> str:
    """哨兵按文件后缀取（见模块 docstring 里 M48 的教训）。"""
    suffix = pathlib.Path(FILES[key]).suffix
    return _SENTINEL_JS if suffix in (".js", ".mjs") else _SENTINEL_PY


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def current_build() -> str:
    """从 content.js 解析当前 EXT_BUILD（I4 用它 ⇒ bump 版本无需改探针锚点）。"""
    match = re.search(r'const EXT_BUILD = "([^"]+)"', (ROOT / FILES["content"]).read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("无法从 content.js 解析 EXT_BUILD")
    return match.group(1)


FLAKES: list[str] = []


def _run_py_once(paths: list[str]) -> tuple[int, str, int, int, list[str]]:
    """跑 pytest，计数从 **junitxml** 取（**不 unlink()**：删除守卫按 turn 累计、>50 即 fail-closed）。"""
    xml = ROOT / ".harness" / "spike" / "_m52_nth_last.xml"
    before = xml.stat().st_mtime_ns if xml.exists() else None
    proc = subprocess.run(
        [
            str(PY), "-m", "pytest", *paths, "--no-header", "--tb=no",
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
    """``_run_py_once`` + 「退出阶段环境级崩溃」的有界重跑（本机已知现象，同 M51-B 探针）。"""
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
    rc, out, passed, failed, _ids = run_py(PY_SUITES["contract"])
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
    helpers = "helpers"  # noqa: F841  （预留：本次无 helpers 侧注入）
    gating = "        if (same.length > 1 && ambiguous) {"

    # 末元素 = 期望失败集：js 为**判据 label 的子串集合**（断言每条 FAIL 都落在期望内且非空）；
    # py 为**用例名集合**（断言「实际红 ⊆ 期望红」且非空）。
    injections = [
        (
            "I1 content：撤掉 gating（回到「有同标签兄弟就补号」的旧行为）",
            "js",
            content,
            gating,
            "        if (same.length > 1) {",
            {"无 id", "祖先链每级可展示", "特征 class 唯一"},
        ),
        (
            "I2 content：gating 恒假 ⇒ 永远不补号（过度去号）",
            "js",
            content,
            gating,
            "        if (false) {",
            {"同构列表", "首类不足以唯一化时", "祖先链收缩候选"},
        ),
        (
            "I3 content：classListOf 恒空 ⇒ 片段丢 class（首类不再参与唯一化判断）",
            "js",
            content,
            "  const classListOf = (node) => (typeof node.className === \"string\"",
            "  const classListOf = (node) => (false",
            {
                "无 id", "祖先链每级可展示", "特征 class 唯一",
                "同构列表", "首类不足以唯一化时", "祖先链收缩候选",
            },
        ),
        (
            "I4 content：EXT_BUILD 改成一个不存在的值（只改 content.js ⇒ 三方不一致）",
            "contract",
            content,
            f'  const EXT_BUILD = "{build}";',
            '  const EXT_BUILD = "0.0.0-stale";',
            {"test_ext_build_marker_matches_manifest_version"},
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
    code, out, passed, failed, _ids = run_py(PY_SUITES["contract"])
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
