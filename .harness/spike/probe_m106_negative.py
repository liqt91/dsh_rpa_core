"""M106 影刀筛选器对标收口负向验证（对照绿 → 注入精确红 → 逐字节还原核 md5 → 元验证）。

注入面：
  I1 content 关掉属性名降级     ⇒ JS 切片 handleDefaultAttr 两条断言红
  I2 content 不收 index-of-type ⇒ JS 切片「进候选层」红
  I3 content 摘掉 svg 特化      ⇒ JS 切片 svg 顺序红
  I4 element_editing 不回喂勾选 ⇒ pytest 勾选回喂用例红
  I5 browser 关掉唯一性 Stats   ⇒ pytest 运行期唯一性两条用例红
  M  元验证：保留 I5 现场、临时禁用两条新判据 ⇒ 必须**变绿**

纪律：起手哨兵自检；try/finally 逐字节还原并核 md5；node 判据 = rc≠0 且有 FAIL 行；
输出 capture_output 不接管道（BrokenPipe 会打断还原）；pytest 计数取 junitxml，不 unlink。
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
NODE = shutil.which("node") or "node"
BAK = ROOT / ".harness" / "spike" / "_tmp" / "m106_neg_backup"
XML = ROOT / ".harness" / "spike" / "_m106_last.xml"

FILES = {
    "content": "extension/content.js",
    "helpers": "scripts/check_capture_helpers.mjs",
    "editing": "src/rpa_core/capture_kit/element_editing.py",
    "browser": "src/rpa_core/executors/browser.py",
    "fallback_test": "tests/contract/test_browser_element_fallback.py",
}
GATE = "scripts/check_capture_helpers.mjs"
PY_SUITE = [
    "tests/unit/test_element_editing.py",
    "tests/contract/test_browser_element_fallback.py",
]

SENTINEL = {
    ".js": "// [M106-NEGATIVE]",
    ".mjs": "// [M106-NEGATIVE]",
    ".py": "# [M106-NEGATIVE]",
}

originals: dict[str, bytes] = {}


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def path(key: str) -> pathlib.Path:
    return ROOT / FILES[key]


def backup_all() -> None:
    BAK.mkdir(parents=True, exist_ok=True)
    for key in FILES:
        originals[key] = path(key).read_bytes()
        shutil.copy2(path(key), BAK / pathlib.Path(FILES[key]).name)


def restore_all() -> None:
    for key, raw in originals.items():
        target = path(key)
        target.write_bytes(raw)
        assert md5(target.read_bytes()) == md5(raw), f"md5 还原失败：{key}"
        assert SENTINEL[target.suffix] not in target.read_text(encoding="utf-8"), f"哨兵残留：{key}"
    print("  ✓ 全部文件逐字节还原、md5 一致、哨兵清零")


def inject(key: str, old: str, new: str, *, count: int = 1,
           allow_same_step: bool = False) -> None:
    """``allow_same_step`` 只给「同一步注入同一文件的两处锚点」用，挡的是跨步骤叠加。"""
    target = path(key)
    text = target.read_text(encoding="utf-8")
    found = text.count(old)
    if found != count:
        raise SystemExit(f"锚点出现 {found} 次（要求 {count}）：{FILES[key]}\n{old[:90]!r}")
    marker = SENTINEL[target.suffix]
    if marker in text and not allow_same_step:
        raise SystemExit(f"哨兵已存在，拒绝叠加注入：{FILES[key]}")
    target.write_text(
        marker + "\n" + text.replace(old, new), encoding="utf-8", newline="\n",
    )


def run_js() -> tuple[int, list[str], str]:
    proc = subprocess.run(
        [NODE, GATE], cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    out = proc.stdout + proc.stderr
    labels = [line.split(" | ", 1)[1].split(" →", 1)[0]
              for line in out.splitlines() if line.startswith("FAIL | ")]
    return proc.returncode, labels, out


def run_py() -> tuple[int, list[str], str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", *PY_SUITE, "--no-header", "--tb=no", f"--junitxml={XML}"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    names: list[str] = []
    if XML.exists():
        root = ET.parse(XML).getroot()
        for case in root.iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                names.append(case.get("name") or "?")
    return proc.returncode, names, proc.stdout + proc.stderr


def expect_js(label: str, wanted: set[str]) -> str | None:
    rc, labels, out = run_js()
    if rc == 0 or not labels:
        return f"{label}：没红（rc={rc} labels={labels}）"
    unexpected = [item for item in labels if not any(w in item for w in wanted)]
    if unexpected:
        return f"{label}：红在期望之外 {unexpected}"
    print(f"  ✓ 精确红：{labels}")
    return None


def expect_py(label: str, wanted: set[str]) -> str | None:
    rc, names, out = run_py()
    if rc == 0 or not names:
        return f"{label}：没红（rc={rc} names={names}）"
    unexpected = [name for name in names if name not in wanted]
    if unexpected:
        return f"{label}：红在期望之外 {unexpected}\n{out[-600:]}"
    print(f"  ✓ 精确红：{names}")
    return None


def main() -> int:
    for key in FILES:
        marker = SENTINEL[path(key).suffix]
        if marker in path(key).read_text(encoding="utf-8"):
            print(f"拒绝运行：{FILES[key]} 已有注入哨兵")
            return 1

    print("=== 对照：未注入必须全绿 ===")
    rc, labels, out = run_js()
    if rc != 0 or labels:
        print(f"对照不绿：JS rc={rc} labels={labels}\n{out[-600:]}")
        return 1
    rc, names, out = run_py()
    if rc != 0 or names:
        print(f"对照不绿：pytest rc={rc} names={names}\n{out[-600:]}")
        return 1
    print("  ✓ JS 切片 + pytest 全绿")

    backup_all()
    failures: list[str] = []
    try:
        injections = [
            ("I1 content 关掉属性名降级", "content",
             '    if (!countFor("css", full) && countFor("css", presence)) out.push(presence);',
             "    if (false) out.push(presence);",
             "js", {"handleDefaultAttr", "值漂移后"}),
            ("I2 content 不收 index-of-type", "content",
             '    if (index) push("attribute", index);',
             "    if (false) push(\"attribute\", index);",
             "js", {"index-of-type 以 attribute 候选身份进优化器"}),
            ("I3 content 摘掉 svg 特化", "content",
             '    svg: ["viewBox", ...TEXT_ATTR_ORDER],\n', "",
             "js", {"svg 属性特化"}),
            ("I4 element_editing 不回喂勾选", "editing",
             "        if isinstance(levels, list) and isinstance(selector.get(\"candidates\"), list):",
             "        if False:",
             "py", {"test_apply_selector_feeds_checked_attrs_back_into_candidates"}),
            ("I5 browser 关掉唯一性 Stats", "browser",
             "        not_unique = count > 1  # M106 S5：歧义也要走 Stats + 候选唯一化",
             "        not_unique = False  # M106 S5：歧义也要走 Stats + 候选唯一化",
             "py", {"test_uniqueness_rescue_uses_candidate_with_exactly_one_match",
                    "test_unresolved_ambiguity_is_reported_in_evidence"}),
        ]
        for label, key, old, new, runner, wanted in injections:
            print(f"\n=== {label} ===")
            inject(key, old, new)
            problem = expect_js(label, wanted) if runner == "js" else expect_py(label, wanted)
            if problem:
                failures.append(problem)
                print(" ", problem)
            restore_all()

        # 元验证：same 现场（I5），但把两条新判据临时改名禁用 ⇒ 必须变绿。
        print("\n=== M 元验证：保留 I5 现场、摘掉新判据必须变绿 ===")
        inject("browser",
               "        not_unique = count > 1  # M106 S5：歧义也要走 Stats + 候选唯一化",
               "        not_unique = False  # M106 S5：歧义也要走 Stats + 候选唯一化")
        inject("fallback_test",
               "def test_uniqueness_rescue_uses_candidate_with_exactly_one_match(tmp_path):",
               "def _disabled_m106_uniqueness_rescue(tmp_path):")
        inject("fallback_test",
               "def test_unresolved_ambiguity_is_reported_in_evidence(tmp_path):",
               "def _disabled_m106_ambiguity_evidence(tmp_path):",
               allow_same_step=True)
        rc, names, out = run_py()
        if rc != 0 or names:
            failures.append(f"M：摘掉判据后仍红 rc={rc} names={names}\n{out[-800:]}")
            print("  ✗ 摘掉判据后仍红")
        else:
            print("  ✓ 同现场摘掉判据变绿（判据确实在拦这件事）")
    finally:
        restore_all()

    if failures:
        print("\n=== 失败 ===")
        for item in failures:
            print(" -", item)
        return 1
    print("\nM106 NEGATIVE OK（5 注入 + 1 元验证全命中，逐字节还原）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
