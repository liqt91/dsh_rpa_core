"""负向验证：安装器的冒烟判据真的会红吗？

三处「合法语法的精准放宽」注入 bin/rpa-skill.mjs，每处只破坏一个行为，
跑 workbuddy-suite/verify/skill_install_test.mjs，断言「实际红 == 期望红」，
然后逐字节还原并核 md5。

期望集是按依赖关系推的：破坏 skill.json 会连带「拷贝了完整技能树」也红，
因为后者把 skill.json 列为必需文件 —— 这不是判据错，是同源红的两个观测点。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

TARGET = Path("bin/rpa-skill.mjs")
SMOKE = "workbuddy-suite/verify/skill_install_test.mjs"
SENTINEL = "NEG-INJECT"

INJECTIONS = [
    {
        "name": "跳过占位符替换",
        "old": "    if (!text.includes(PLACEHOLDER)) continue;\n",
        "new": "    if (true) continue;  // " + SENTINEL + "\n",
        "expect": {"占位符替换成安装后的绝对路径"},
    },
    {
        "name": "不写 skill.json",
        "old": '  writeFileSync(join(dest, "skill.json"), JSON.stringify(manifest, null, 2) + "\\n", "utf8");\n',
        "new": "  // " + SENTINEL + " 故意不写清单\n",
        "expect": {"拷贝了完整技能树", "skill.json 可解析且字段齐全"},
    },
    {
        "name": "幂等检查失效",
        "old": "  if (installed && !force) {\n",
        "new": "  if (false) {  // " + SENTINEL + "\n",
        "expect": {"重复安装被拒绝（幂等）"},
    },
]


def run_smoke() -> tuple[int, set[str]]:
    proc = subprocess.run(
        ["node", SMOKE], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    failed = set()
    for line in text.splitlines():
        if not line.startswith("FAIL"):
            continue
        name = line[4:].split(" — ")[0].strip()
        if name:
            failed.add(name)
    return proc.returncode, failed


def main() -> int:
    if SENTINEL in TARGET.read_text(encoding="utf-8"):
        print("哨兵残留，拒绝运行", file=sys.stderr)
        return 2

    original = TARGET.read_bytes()
    baseline = hashlib.md5(original).hexdigest()
    problems: list[str] = []

    try:
        rc, failed = run_smoke()
        print(f"[基线] rc={rc} 红={sorted(failed) or '无'}")
        if rc != 0:
            problems.append("基线本身不绿，负向验证无意义")

        for item in INJECTIONS:
            text = TARGET.read_text(encoding="utf-8")
            hits = text.count(item["old"])
            if hits != 1:
                problems.append(f"{item['name']}: 锚点命中 {hits} 次（应为 1）")
                continue
            TARGET.write_bytes(text.replace(item["old"], item["new"]).encode("utf-8"))
            if SENTINEL not in TARGET.read_text(encoding="utf-8"):
                problems.append(f"{item['name']}: 注入没落盘")
                TARGET.write_bytes(original)
                continue

            rc, failed = run_smoke()
            ok = rc != 0 and failed == item["expect"]
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] 注入「{item['name']}」→ rc={rc} 红={sorted(failed)}")
            if not ok:
                problems.append(
                    f"{item['name']}: 期望 {sorted(item['expect'])} 实际 {sorted(failed)}"
                )

            TARGET.write_bytes(original)
            if hashlib.md5(TARGET.read_bytes()).hexdigest() != baseline:
                problems.append(f"{item['name']}: 还原失败")
    finally:
        if hashlib.md5(TARGET.read_bytes()).hexdigest() != baseline:
            TARGET.write_bytes(original)
            print("[兜底] 已逐字节还原")

    rc, failed = run_smoke()
    print(f"[复跑] rc={rc} 红={sorted(failed) or '无'}")
    if rc != 0:
        problems.append("还原后复跑不绿")

    if problems:
        for p in problems:
            print("  ! " + p)
        print("\nNEGATIVE VERIFICATION FAILED")
        return 1
    print("\nNEGATIVE VERIFICATION PASSED（三注入全拦、md5 一致、复跑复绿）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
