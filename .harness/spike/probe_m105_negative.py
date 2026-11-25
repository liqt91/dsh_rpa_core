"""M105 影刀式筛选器优化负向验证（对照绿 → 注入精确红 → 逐字节还原核 md5 → 元验证）。

注入面：
  I1 content 关掉状态 class 过滤 ⇒「stableClassesOf」红
  I2 content 关掉哈希后缀裁剪   ⇒「stableClassesOf」红
  I3 ranking 表摘掉 shadow 登记 ⇒ 两条跨端/档位判据红（**静默排最后**那一类失配）
  I4 background 关掉 xpath 分派 ⇒ 运行期分派门禁红
  I5 background 只穿 shadowRoot、最后一段不取目标 ⇒ 运行期分派门禁红（拿回 root 而非元素）
  M  元验证：保留 I4 现场、同时摘掉门禁里那条 xpath 断言 ⇒ 必须**变绿**

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
BAK = ROOT / ".harness" / "spike" / "_tmp" / "m105_neg_backup"
XML = ROOT / ".harness" / "spike" / "_m105_last.xml"

FILES = {
    "content": "extension/content.js",
    "background": "extension/background.js",
    "ranking": "src/rpa_core/model/selector_ranking.py",
    "helpers": "scripts/check_capture_helpers.mjs",
    "dispatch": "scripts/check_selector_query_kinds.mjs",
}
GATES = {
    "helpers": "scripts/check_capture_helpers.mjs",
    "dispatch": "scripts/check_selector_query_kinds.mjs",
}
PY_SUITE = ["tests/contract/test_web_candidate_ranking.py"]

SENTINEL = {
    ".js": "// [M105-NEGATIVE]",
    ".mjs": "// [M105-NEGATIVE]",
    ".py": "# [M105-NEGATIVE]",
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


def inject(key: str, old: str, new: str, *, count: int = 1) -> None:
    target = path(key)
    text = target.read_text(encoding="utf-8")
    found = text.count(old)
    if found != count:
        raise SystemExit(f"锚点出现 {found} 次（要求 {count}）：{FILES[key]}\n{old[:90]!r}")
    marker = SENTINEL[target.suffix]
    if marker in text:
        raise SystemExit(f"哨兵已存在，拒绝叠加注入：{FILES[key]}")
    target.write_text(
        marker + "\n" + text.replace(old, new), encoding="utf-8", newline="\n",
    )


def run_js(gate: str) -> tuple[int, list[str], str]:
    proc = subprocess.run(
        [NODE, GATES[gate]], cwd=ROOT, capture_output=True, text=True, timeout=180,
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


def expect_js(label: str, gate: str, wanted: set[str]) -> str | None:
    rc, labels, out = run_js(gate)
    if rc == 0 or not labels:
        return f"{label}：没红（rc={rc} labels={labels}）"
    unexpected = [item for item in labels if not any(w in item for w in wanted)]
    if unexpected:
        return f"{label}：红在期望之外 {unexpected}"
    print(f"  ✓ 精确红：{labels}")
    return None


def expect_py(label: str, wanted: set[str]) -> str | None:
    rc, names, _out = run_py()
    if rc == 0 or not names:
        return f"{label}：没红（rc={rc} names={names}）"
    unexpected = [name for name in names if name not in wanted]
    if unexpected:
        return f"{label}：红在期望之外 {unexpected}"
    print(f"  ✓ 精确红：{names}")
    return None


def main() -> int:
    for key in FILES:
        marker = SENTINEL[path(key).suffix]
        if marker in path(key).read_text(encoding="utf-8"):
            print(f"拒绝运行：{FILES[key]} 已有注入哨兵")
            return 1

    print("=== 对照：未注入必须全绿 ===")
    for gate in GATES:
        rc, labels, _out = run_js(gate)
        if rc != 0 or labels:
            print(f"对照不绿：{gate} rc={rc} labels={labels}")
            return 1
    rc, names, _out = run_py()
    if rc != 0 or names:
        print(f"对照不绿：pytest rc={rc} names={names}")
        return 1
    print("  ✓ JS 两门禁 + pytest 全绿")

    backup_all()
    failures: list[str] = []
    try:
        injections = [
            ("I1 content 关状态类过滤", "content",
             "      if (STATE_CLASS_RE.test(cls)) continue;", "      if (false) continue;",
             "js", "helpers", {"stableClassesOf"}),
            ("I2 content 关哈希裁剪", "content",
             '      const base = cls.replace(HASH_SUFFIX_RE, "");', "      const base = cls;",
             "js", "helpers", {"stableClassesOf"}),
            ("I3 ranking 摘掉 shadow 登记", "ranking",
             '    "shadow": 35,\n', "", "py", "",
             {"test_m105_optimizer_kinds_are_registered_and_ordered",
              "test_capture_script_kinds_are_all_registered_in_table"}),
            ("I4 background 关掉 xpath 分派", "background",
             '    if (sel.startsWith("xpath:")) {', "    if (false) {",
             "js", "dispatch", {"xpath: 前缀"}),
            ("I5 background 最后一段不取目标", "background",
             "            if (i === parts.length - 1 || node.shadowRoot) {",
             "            if (node.shadowRoot) {",
             "js", "dispatch", {"shadow: 前缀"}),
        ]
        for label, key, old, new, runner, gate, wanted in injections:
            print(f"\n=== {label} ===")
            inject(key, old, new)
            problem = expect_js(label, gate, wanted) if runner == "js" else expect_py(label, wanted)
            if problem:
                failures.append(problem)
                print(" ", problem)
            restore_all()

        # 元验证：same 现场（I4），但把门禁里那条 xpath 断言摘掉 ⇒ 必须变绿。
        print("\n=== M 元验证：保留 I4 现场、摘掉新判据必须变绿 ===")
        inject("background", '    if (sel.startsWith("xpath:")) {', "    if (false) {")
        inject("dispatch",
               'check("xpath: 前缀走 document.evaluate",\n'
               '  query("xpath://div[contains(normalize-space(.), \'详情\')]"), [xpathTarget]);',
               '// xpath 断言在元验证里临时摘掉')
        rc, labels, out = run_js("dispatch")
        if rc != 0 or labels:
            failures.append(f"M：摘掉判据后仍红 rc={rc} labels={labels}\n{out[-800:]}")
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
    print("\nM105 NEGATIVE OK（5 注入 + 1 元验证全命中，逐字节还原）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
